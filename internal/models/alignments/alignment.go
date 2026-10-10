// Package alignments joins one world stream with several Plays of the same
// session on the server tick timeline, and computes where an actor's last
// observed container contents differ from the world's.
//
// It reports observations and divergences only. It never decides what an
// actor knows or expects; that interpretation belongs to consumers.
package alignments

import (
	"context"
	"errors"
	"fmt"
	"io"
	"path/filepath"
	"sort"
	"strings"

	artifactsv1 "github.com/proj-airi/recorder-minecraft/apis/sdk/go/recorder-minecraft/artifacts/v1"
	"github.com/proj-airi/recorder-minecraft/internal/models/captures"
	"github.com/proj-airi/recorder-minecraft/internal/models/processorfiles"
	"github.com/proj-airi/recorder-minecraft/internal/models/worldcaptures"
	"github.com/samber/do/v2"
	"google.golang.org/protobuf/encoding/protojson"
)

const (
	// ProcessorName identifies outputs this processor owns.
	ProcessorName = "recorder-minecraft session align"
	// ProcessorVersion changes whenever the alignment rules or the output
	// contract change.
	ProcessorVersion = "1"

	scope      = "session"
	provenance = "deterministic transform"
	outputName = "session alignment"
)

// PlayInput names one Play's files explicitly. Perception is optional.
type PlayInput struct {
	Metadata   string
	Events     string
	Perception string
}

// ParsePlay reads `metadata=PATH,events=PATH[,perception=PATH]`.
func ParsePlay(spec string) (PlayInput, error) {
	var input PlayInput
	for _, part := range strings.Split(spec, ",") {
		name, value, ok := strings.Cut(part, "=")
		if !ok || value == "" {
			return PlayInput{}, fmt.Errorf("play %q: expected metadata=PATH,events=PATH[,perception=PATH]", spec)
		}
		var field *string
		switch name {
		case "metadata":
			field = &input.Metadata
		case "events":
			field = &input.Events
		case "perception":
			field = &input.Perception
		default:
			return PlayInput{}, fmt.Errorf("play %q: unknown key %q", spec, name)
		}
		if *field != "" {
			return PlayInput{}, fmt.Errorf("play %q: %s given twice", spec, name)
		}
		*field = value
	}
	if input.Metadata == "" || input.Events == "" {
		return PlayInput{}, fmt.Errorf("play %q: metadata and events are required", spec)
	}
	return input, nil
}

type Options struct {
	WorldMetadata string
	WorldEvents   string
	Plays         []PlayInput
	Output        string
	Overwrite     bool
}

type Result struct {
	Output        string
	SessionID     string
	Participants  int
	Events        uint64
	Divergences   uint64
	WithoutVision int
}

type Service struct {
	captures *captures.Service
	world    *worldcaptures.Service
}

func NewService(injector do.Injector) (*Service, error) {
	captureService, err := do.Invoke[*captures.Service](injector)
	if err != nil {
		return nil, err
	}
	worldService, err := do.Invoke[*worldcaptures.Service](injector)
	if err != nil {
		return nil, err
	}
	return &Service{captures: captureService, world: worldService}, nil
}

// actor is one input Play and everything derived from it.
type actor struct {
	index        int
	input        PlayInput
	metadata     captures.Metadata
	metadataFile processorfiles.Input
	events       captures.EventsSource
	timeline     *actorTimeline
	perception   perceptionResult
	observed     int
}

// Align writes the session alignment for one world session and the named
// Plays. Inputs are only read; the output is published atomically.
func (service *Service) Align(ctx context.Context, options Options) (Result, error) {
	if options.WorldMetadata == "" || options.WorldEvents == "" {
		return Result{}, errors.New("world session metadata and events are required")
	}
	if len(options.Plays) == 0 {
		return Result{}, errors.New("at least one play is required")
	}
	world, err := service.world.LoadMetadata(options.WorldMetadata)
	if err != nil {
		return Result{}, err
	}
	destination, err := processorfiles.PrepareOutput(outputName, options.Output, options.Overwrite, ownedOutput)
	if err != nil {
		return Result{}, err
	}
	actors, err := service.loadActors(world, options.Plays)
	if err != nil {
		return Result{}, err
	}
	if err := ctx.Err(); err != nil {
		return Result{}, err
	}

	observedKeys := map[containerKey]bool{}
	for _, actor := range actors {
		for _, update := range actor.timeline.updates {
			observedKeys[update.key] = true
		}
	}
	truth := worldTimeline{}
	allKeys := map[containerKey]bool{}
	var events []*artifactsv1.AlignedEvent
	worldEvents, err := service.world.ScanEvents(options.WorldEvents, world, func(event worldcaptures.Event) error {
		record, key := worldRecordOf(event)
		allKeys[key] = true
		// The index and the comparison only need containers some participant
		// observed; every other record stays reachable in the world stream.
		if observedKeys[key] {
			truth[key] = append(truth[key], record)
			kind := artifactsv1.AlignedEventKind_ALIGNED_EVENT_KIND_WORLD_CONTAINER_SNAPSHOT
			if record.removed {
				kind = artifactsv1.AlignedEventKind_ALIGNED_EVENT_KIND_WORLD_CONTAINER_REMOVED
			}
			events = append(events, &artifactsv1.AlignedEvent{Kind: kind, Source: record.ref, Dimension: key.dimension, BlockPos: key.position()})
		}
		return nil
	})
	if err != nil {
		return Result{}, err
	}
	worldMetadataFile, err := processorfiles.Digest(world.Path)
	if err != nil {
		return Result{}, err
	}

	worldCoverage := coverage{world.StartTick, world.EndTick}
	var divergences []*artifactsv1.ContainerDivergence
	participants := map[string][]*actor{}
	for _, actor := range actors {
		participants[actor.metadata.PlayerUUID] = append(participants[actor.metadata.PlayerUUID], actor)
	}
	outputDirectory := filepath.Dir(destination)
	for _, actor := range actors {
		actorCoverage := coverage{actor.metadata.StartTick, actor.metadata.EndTick}
		updates := updatesByContainer(actor.timeline.updates)
		var queries []presenceQuery
		var own []*artifactsv1.ContainerDivergence
		for _, key := range sortedKeys(updates) {
			history := diverge(key, truth[key], updates[key], actorCoverage, worldCoverage)
			if history.observations == 0 {
				continue
			}
			// A container absent from the world stream has no truth, so it
			// does not reduce the unobserved count of world containers.
			if allKeys[key] {
				actor.observed++
			}
			for _, value := range history.divergences {
				record := value.record(actor.metadata.ConnectionID)
				own = append(own, record)
				queries = append(queries, presenceQuery{tick: value.start, key: key, answer: &record.CoPresence})
			}
		}
		if actor.input.Perception == "" {
			for _, record := range own {
				record.CoPresence = &artifactsv1.CoPresence{Status: artifactsv1.CoPresenceStatus_CO_PRESENCE_STATUS_PERCEPTION_NOT_PROVIDED}
			}
		} else {
			reader := &perceptionReader{actor: actor, containers: observedKeys, participants: participants,
				previous: map[string]artifactsv1.TargetVisibility{}, last: map[string]*artifactsv1.AlignedEvent{}}
			actor.perception, err = reader.read(outputDirectory, queries)
			if err != nil {
				return Result{}, err
			}
			events = append(events, actor.perception.events...)
		}
		divergences = append(divergences, own...)
		events = append(events, actor.timeline.events...)
		events = append(events, membership(actor)...)
		if err := ctx.Err(); err != nil {
			return Result{}, err
		}
	}

	order := map[string]int{}
	for _, actor := range actors {
		order[actor.metadata.ConnectionID] = actor.index
	}
	sortEvents(events, order)
	sort.SliceStable(divergences, func(i, j int) bool {
		left, right := divergences[i], divergences[j]
		if left.GetStartTick() != right.GetStartTick() {
			return left.GetStartTick() < right.GetStartTick()
		}
		return order[left.GetConnectionId()] < order[right.GetConnectionId()]
	})

	header := &artifactsv1.SessionAlignmentHeader{
		Processor: &artifactsv1.PerceptionProcessor{Name: ProcessorName, Version: ProcessorVersion},
		Scope:     scope, Provenance: provenance, UsesFutureContext: true, FutureContext: futureContext,
		SessionId:     world.SessionID,
		WorldCoverage: &artifactsv1.TickRange{FirstTick: world.StartTick, LastTick: world.EndTick},
		WorldInputs: []*artifactsv1.PerceptionInput{
			worldMetadataFile.Lineage("world_metadata", "application/json", outputDirectory),
			{Role: "world_events", File: &artifactsv1.ArtifactFile{Path: processorfiles.RelativeInput(worldEvents.Path, outputDirectory),
				Sha256: worldEvents.SHA256, SizeBytes: uint64(worldEvents.SizeBytes), MediaType: "application/x-ndjson"}},
		},
		Assumptions:      assumptions(),
		KnownLimitations: knownLimitations(),
		EventCount:       uint64(len(events)),
		DivergenceCount:  uint64(len(divergences)),
	}
	result := Result{Output: destination, SessionID: world.SessionID, Participants: len(actors),
		Events: header.EventCount, Divergences: header.DivergenceCount}
	for _, actor := range actors {
		header.Participants = append(header.Participants, actor.participant(outputDirectory, len(allKeys)))
		if actor.input.Perception == "" {
			result.WithoutVision++
		}
	}

	err = processorfiles.Publish(outputName, destination, options.Overwrite, func(writer io.Writer) error {
		records := []*artifactsv1.SessionAlignmentRecord{{SchemaVersion: 1, Record: &artifactsv1.SessionAlignmentRecord_Header{Header: header}}}
		for _, event := range events {
			records = append(records, &artifactsv1.SessionAlignmentRecord{SchemaVersion: 1, Record: &artifactsv1.SessionAlignmentRecord_Event{Event: event}})
		}
		for _, divergence := range divergences {
			records = append(records, &artifactsv1.SessionAlignmentRecord{SchemaVersion: 1, Record: &artifactsv1.SessionAlignmentRecord_Divergence{Divergence: divergence}})
		}
		for _, record := range records {
			if err := writeRecord(writer, record); err != nil {
				return err
			}
		}
		// Event streams were checked while scanned; metadata was digested
		// before use and must still be the same file.
		for _, actor := range actors {
			if err := actor.metadataFile.Unchanged(); err != nil {
				return err
			}
		}
		return worldMetadataFile.Unchanged()
	})
	if err != nil {
		return Result{}, err
	}
	return result, nil
}

// loadActors validates every Play against the world session and scans its
// event stream. Actors are ordered by connection start, then connection id,
// so the output does not depend on flag order.
func (service *Service) loadActors(world worldcaptures.Metadata, plays []PlayInput) ([]*actor, error) {
	seen := map[string]bool{}
	actors := make([]*actor, 0, len(plays))
	for _, play := range plays {
		metadata, err := service.captures.LoadMetadata(play.Metadata)
		if err != nil {
			return nil, err
		}
		if metadata.SessionID != world.SessionID {
			return nil, fmt.Errorf("play %s belongs to session %s, not world session %s", metadata.ConnectionID, metadata.SessionID, world.SessionID)
		}
		if seen[metadata.ConnectionID] {
			return nil, fmt.Errorf("play %s is given more than once", metadata.ConnectionID)
		}
		seen[metadata.ConnectionID] = true
		observer := newObserver(metadata.ConnectionID)
		events, err := service.captures.ScanEvents(play.Events, metadata, func(event captures.Event) error {
			observer.observe(event)
			return nil
		})
		if err != nil {
			return nil, err
		}
		metadataFile, err := processorfiles.Digest(metadata.Path)
		if err != nil {
			return nil, err
		}
		actors = append(actors, &actor{input: play, metadata: metadata, metadataFile: metadataFile, events: events, timeline: observer.finish(metadata.EndTick)})
	}
	sort.Slice(actors, func(i, j int) bool {
		if actors[i].metadata.StartTick != actors[j].metadata.StartTick {
			return actors[i].metadata.StartTick < actors[j].metadata.StartTick
		}
		return actors[i].metadata.ConnectionID < actors[j].metadata.ConnectionID
	})
	for index, actor := range actors {
		actor.index = index
	}
	return actors, nil
}

func (actor *actor) participant(outputDirectory string, worldContainers int) *artifactsv1.SessionParticipant {
	participant := &artifactsv1.SessionParticipant{
		PlayerUuid: actor.metadata.PlayerUUID, PlayerName: actor.metadata.PlayerName, ConnectionId: actor.metadata.ConnectionID,
		Coverage:       &artifactsv1.TickRange{FirstTick: actor.metadata.StartTick, LastTick: actor.metadata.EndTick},
		TerminalReason: actor.metadata.TerminalReason,
		Inputs: []*artifactsv1.PerceptionInput{
			actor.metadataFile.Lineage("capture_metadata", "application/json", outputDirectory),
			{Role: "capture_events", File: &artifactsv1.ArtifactFile{Path: processorfiles.RelativeInput(actor.events.Path, outputDirectory),
				Sha256: actor.events.SHA256, SizeBytes: uint64(actor.events.SizeBytes), MediaType: "application/x-ndjson"}},
		},
		ObservedContainerCount:   uint32(actor.observed),
		UnobservedContainerCount: uint32(max(worldContainers-actor.observed, 0)),
	}
	if actor.perception.input != nil {
		participant.Inputs = append(participant.Inputs, actor.perception.input)
		participant.PerceptionCoverage = actor.perception.ticks
		participant.PerceptionIntervalTicks = actor.perception.interval
	}
	return participant
}

func (value divergence) record(connectionID string) *artifactsv1.ContainerDivergence {
	return &artifactsv1.ContainerDivergence{
		ConnectionId: connectionID, Dimension: value.key.dimension, BlockPos: value.key.position(),
		StartTick: value.start, LastTick: value.last, EndTick: value.end, End: value.reason, EndSource: value.endSource,
		Observed: value.observed, Truth: value.truth, TruthChanges: value.truthChanges,
	}
}

func worldRecordOf(event worldcaptures.Event) (worldRecord, containerKey) {
	ref := &artifactsv1.RecordRef{Stream: artifactsv1.RecordStream_RECORD_STREAM_WORLD_EVENTS, ServerTick: event.ServerTick, Sequence: event.Sequence}
	if removed := event.Message.GetContainerRemoved(); removed != nil {
		return worldRecord{ref: ref, removed: true}, keyOf(removed.GetDimension(), removed.GetBlockPos())
	}
	snapshot := event.Message.GetContainerSnapshot()
	record := worldRecord{ref: ref, size: snapshot.GetContainerSize(), contents: contents{},
		known: snapshot.GetContentsState() == artifactsv1.ContainerSnapshot_CONTENTS_STATE_KNOWN}
	for _, stack := range snapshot.GetSlots() {
		record.contents.set(stack.GetSlot(), stack)
	}
	return record, keyOf(snapshot.GetDimension(), snapshot.GetBlockPos())
}

// membership indexes the Play's start and end from its metadata.
func membership(actor *actor) []*artifactsv1.AlignedEvent {
	ref := func(tick int64) *artifactsv1.RecordRef {
		return &artifactsv1.RecordRef{Stream: artifactsv1.RecordStream_RECORD_STREAM_CAPTURE_METADATA, ConnectionId: actor.metadata.ConnectionID, ServerTick: tick}
	}
	return []*artifactsv1.AlignedEvent{
		{Kind: artifactsv1.AlignedEventKind_ALIGNED_EVENT_KIND_ACTOR_JOINED, Source: ref(actor.metadata.StartTick)},
		{Kind: artifactsv1.AlignedEventKind_ALIGNED_EVENT_KIND_ACTOR_LEFT, Source: ref(actor.metadata.EndTick)},
	}
}

// sortEvents orders the index by tick. Within a tick, joins come first and
// leaves last, world records precede actor records (world snapshots describe
// the end of the tick, but the stream is the shared reference), and actor
// records keep participant order and stream sequence.
func sortEvents(events []*artifactsv1.AlignedEvent, order map[string]int) {
	rank := func(kind artifactsv1.AlignedEventKind) int {
		switch kind {
		case artifactsv1.AlignedEventKind_ALIGNED_EVENT_KIND_ACTOR_JOINED:
			return 0
		case artifactsv1.AlignedEventKind_ALIGNED_EVENT_KIND_WORLD_CONTAINER_SNAPSHOT, artifactsv1.AlignedEventKind_ALIGNED_EVENT_KIND_WORLD_CONTAINER_REMOVED:
			return 1
		case artifactsv1.AlignedEventKind_ALIGNED_EVENT_KIND_ACTOR_CONTAINER_VIEW, artifactsv1.AlignedEventKind_ALIGNED_EVENT_KIND_ACTOR_CONTAINER_CLICK:
			return 2
		case artifactsv1.AlignedEventKind_ALIGNED_EVENT_KIND_ACTOR_LEFT:
			return 4
		}
		return 3
	}
	sort.SliceStable(events, func(i, j int) bool {
		left, right := events[i], events[j]
		if a, b := left.GetSource().GetServerTick(), right.GetSource().GetServerTick(); a != b {
			return a < b
		}
		if a, b := rank(left.GetKind()), rank(right.GetKind()); a != b {
			return a < b
		}
		if a, b := order[left.GetSource().GetConnectionId()], order[right.GetSource().GetConnectionId()]; a != b {
			return a < b
		}
		return left.GetSource().GetSequence() < right.GetSource().GetSequence()
	})
}

func writeRecord(writer io.Writer, record *artifactsv1.SessionAlignmentRecord) error {
	encoded, err := protojson.Marshal(record)
	if err != nil {
		return fmt.Errorf("encode session alignment ProtoJSON: %w", err)
	}
	if _, err := writer.Write(append(encoded, '\n')); err != nil {
		return fmt.Errorf("write session alignment JSONL: %w", err)
	}
	return nil
}

// ownedOutput recognizes a previous result by its header line.
func ownedOutput(path string) bool {
	line, ok := processorfiles.FirstLine(path)
	if !ok {
		return false
	}
	record := &artifactsv1.SessionAlignmentRecord{}
	if protojson.Unmarshal(line, record) != nil || record.GetSchemaVersion() != 1 {
		return false
	}
	header := record.GetHeader()
	return header != nil && header.GetProcessor().GetName() == ProcessorName && header.GetScope() == scope && header.GetProvenance() == provenance
}
