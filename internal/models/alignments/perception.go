package alignments

import (
	"bufio"
	"errors"
	"fmt"
	"io"
	"os"
	"sort"

	artifactsv1 "github.com/proj-airi/recorder-minecraft/apis/sdk/go/recorder-minecraft/artifacts/v1"
	"github.com/proj-airi/recorder-minecraft/internal/models/perceptions"
	"github.com/proj-airi/recorder-minecraft/internal/models/processorfiles"
	"google.golang.org/protobuf/encoding/protojson"
)

const (
	maxPerceptionLineBytes = 64 * 1024 * 1024
	playerEntityType       = "minecraft:player"
)

// perceptionResult is what one actor's perception.jsonl contributes.
type perceptionResult struct {
	input    *artifactsv1.PerceptionInput
	ticks    *artifactsv1.TickRange
	interval uint32
	events   []*artifactsv1.AlignedEvent
}

// presenceQuery asks for co-presence evidence of one divergence start.
type presenceQuery struct {
	tick   int64
	key    containerKey
	answer **artifactsv1.CoPresence
}

// perceptionReader streams one actor's samples once: it emits visibility
// transitions for the bounded target set and answers co-presence queries.
type perceptionReader struct {
	actor        *actor
	containers   map[containerKey]bool
	participants map[string][]*actor
	previous     map[string]artifactsv1.TargetVisibility
	// Last event emitted per target, to describe a target that disappears.
	last   map[string]*artifactsv1.AlignedEvent
	events []*artifactsv1.AlignedEvent
}

func (reader *perceptionReader) read(outputDirectory string, queries []presenceQuery) (perceptionResult, error) {
	actor := reader.actor
	input, err := processorfiles.Digest(actor.input.Perception)
	if err != nil {
		return perceptionResult{}, fmt.Errorf("perception: %w", err)
	}
	resolved := input.Path
	file, err := os.Open(resolved)
	if err != nil {
		return perceptionResult{}, fmt.Errorf("read perception: %w", err)
	}
	defer func() { _ = file.Close() }()
	lines := bufio.NewReaderSize(file, 1024*1024)

	header, err := reader.header(resolved, lines)
	if err != nil {
		return perceptionResult{}, err
	}
	interval := header.GetAssumptions().GetSamplingIntervalTicks()
	lastSampled := header.GetTicks().GetLastTick()
	sort.Slice(queries, func(i, j int) bool { return queries[i].tick < queries[j].tick })
	next := 0
	var previous *artifactsv1.PerceptionSample
	answer := func(until int64, inclusive bool) {
		for ; next < len(queries) && (queries[next].tick < until || inclusive); next++ {
			*queries[next].answer = reader.presence(previous, queries[next], interval, lastSampled)
		}
	}
	for number := 2; ; number++ {
		line, readErr := lines.ReadBytes('\n')
		if len(line) == 0 && errors.Is(readErr, io.EOF) {
			break
		}
		if readErr != nil && !errors.Is(readErr, io.EOF) {
			return perceptionResult{}, fmt.Errorf("read perception: %w", readErr)
		}
		if len(line) > maxPerceptionLineBytes || line[len(line)-1] != '\n' {
			return perceptionResult{}, fmt.Errorf("%s record %d must be an LF-terminated line within the size limit", resolved, number)
		}
		record := &artifactsv1.PerceptionRecord{}
		if err := protojson.Unmarshal(line[:len(line)-1], record); err != nil {
			return perceptionResult{}, fmt.Errorf("%s record %d: invalid PerceptionRecord ProtoJSON: %w", resolved, number, err)
		}
		sample := record.GetSample()
		if record.GetSchemaVersion() != 1 || sample == nil {
			return perceptionResult{}, fmt.Errorf("%s record %d: expected a schema 1 sample", resolved, number)
		}
		if previous != nil && sample.GetServerTick() <= previous.GetServerTick() {
			return perceptionResult{}, fmt.Errorf("%s record %d: sample ticks must increase", resolved, number)
		}
		answer(sample.GetServerTick(), false)
		reader.transitions(sample)
		previous = sample
	}
	answer(0, true)
	// The digest was taken before reading; an unchanged file means the lines
	// read are the bytes it describes.
	if err := input.Unchanged(); err != nil {
		return perceptionResult{}, err
	}
	return perceptionResult{
		input:    input.Lineage("perception", "application/x-ndjson", outputDirectory),
		ticks:    header.GetTicks(),
		interval: interval,
		events:   reader.events,
	}, nil
}

// header validates that the perception was derived from this actor's own
// event stream, by identity and by the digest its header recorded.
func (reader *perceptionReader) header(path string, lines *bufio.Reader) (*artifactsv1.PerceptionHeader, error) {
	line, err := lines.ReadBytes('\n')
	if err != nil || len(line) > maxPerceptionLineBytes {
		return nil, fmt.Errorf("%s has no complete header line", path)
	}
	record := &artifactsv1.PerceptionRecord{}
	if err := protojson.Unmarshal(line[:len(line)-1], record); err != nil {
		return nil, fmt.Errorf("%s header: invalid PerceptionRecord ProtoJSON: %w", path, err)
	}
	header := record.GetHeader()
	if record.GetSchemaVersion() != 1 || header == nil || header.GetProcessor().GetName() != perceptions.ProcessorName {
		return nil, fmt.Errorf("%s is not a perception output of %s", path, perceptions.ProcessorName)
	}
	metadata := reader.actor.metadata
	if header.GetSessionId() != metadata.SessionID || header.GetPlayerUuid() != metadata.PlayerUUID || header.GetConnectionId() != metadata.ConnectionID {
		return nil, fmt.Errorf("%s identity does not match Play %s", path, metadata.ConnectionID)
	}
	if header.GetAssumptions().GetSamplingIntervalTicks() < 1 {
		return nil, fmt.Errorf("%s declares no sampling interval", path)
	}
	for _, input := range header.GetInputs() {
		if input.GetRole() == "capture_events" && input.GetFile().GetSha256() != reader.actor.events.SHA256 {
			return nil, fmt.Errorf("%s was derived from a different event stream than %s", path, reader.actor.events.Path)
		}
	}
	return header, nil
}

// transitions emits one event per bounded target whose visibility differs
// from the previous sample. A target absent from a sample was determined not
// visible, so the first sample only reports visible and undetermined targets.
func (reader *perceptionReader) transitions(sample *artifactsv1.PerceptionSample) {
	current := map[string]artifactsv1.TargetVisibility{}
	targets := map[string]*artifactsv1.AlignedEvent{}
	ref := &artifactsv1.RecordRef{Stream: artifactsv1.RecordStream_RECORD_STREAM_PERCEPTION, ConnectionId: reader.actor.metadata.ConnectionID, ServerTick: sample.GetServerTick()}
	blocks := func(values []*artifactsv1.PerceivedBlockEntity, state artifactsv1.TargetVisibility) {
		for _, value := range values {
			key := keyOf(value.GetDimension(), value.GetBlockPos())
			if !reader.containers[key] {
				continue
			}
			id := fmt.Sprintf("b|%s|%d|%d|%d", key.dimension, key.x, key.y, key.z)
			current[id] = state
			targets[id] = &artifactsv1.AlignedEvent{Kind: artifactsv1.AlignedEventKind_ALIGNED_EVENT_KIND_ACTOR_BLOCK_ENTITY_VISIBILITY, Source: ref,
				Dimension: key.dimension, BlockPos: key.position(), TypeId: value.GetTypeId()}
		}
	}
	entities := func(values []*artifactsv1.PerceivedEntity, state artifactsv1.TargetVisibility) {
		for _, value := range values {
			if value.GetTypeId() != playerEntityType || value.GetUuid() == "" {
				continue
			}
			id := "e|" + value.GetUuid()
			current[id] = state
			targets[id] = &artifactsv1.AlignedEvent{Kind: artifactsv1.AlignedEventKind_ALIGNED_EVENT_KIND_ACTOR_ENTITY_VISIBILITY, Source: ref,
				EntityUuid: value.GetUuid(), TypeId: value.GetTypeId()}
		}
	}
	blocks(sample.GetVisibleBlockEntities(), artifactsv1.TargetVisibility_TARGET_VISIBILITY_VISIBLE)
	blocks(sample.GetUndeterminedBlockEntities(), artifactsv1.TargetVisibility_TARGET_VISIBILITY_UNDETERMINED)
	entities(sample.GetVisibleEntities(), artifactsv1.TargetVisibility_TARGET_VISIBILITY_VISIBLE)
	entities(sample.GetUndeterminedEntities(), artifactsv1.TargetVisibility_TARGET_VISIBILITY_UNDETERMINED)

	for id, previous := range reader.previous {
		if _, ok := current[id]; !ok && previous != artifactsv1.TargetVisibility_TARGET_VISIBILITY_NOT_VISIBLE {
			current[id] = artifactsv1.TargetVisibility_TARGET_VISIBILITY_NOT_VISIBLE
			targets[id] = reader.lastTarget(id, ref)
		}
	}
	ids := make([]string, 0, len(current))
	for id, state := range current {
		if reader.previous[id] != state {
			ids = append(ids, id)
		}
	}
	sort.Strings(ids)
	for _, id := range ids {
		event := targets[id]
		event.Visibility = current[id]
		reader.events = append(reader.events, event)
		reader.last[id] = event
	}
	reader.previous = current
}

// lastTarget rebuilds the target fields of a target that left the sample
// from the last event emitted for it.
func (reader *perceptionReader) lastTarget(id string, ref *artifactsv1.RecordRef) *artifactsv1.AlignedEvent {
	event := reader.last[id]
	return &artifactsv1.AlignedEvent{Kind: event.GetKind(), Source: ref, Dimension: event.GetDimension(), BlockPos: event.GetBlockPos(),
		EntityUuid: event.GetEntityUuid(), TypeId: event.GetTypeId()}
}

// presence copies the observer's sample at a divergence start. Only the
// latest sample at or before the start, and less than one sampling interval
// before it, counts; anything older says nothing about the start tick.
func (reader *perceptionReader) presence(sample *artifactsv1.PerceptionSample, query presenceQuery, interval uint32, lastSampled int64) *artifactsv1.CoPresence {
	if sample == nil || query.tick > lastSampled || query.tick-sample.GetServerTick() >= int64(interval) {
		return &artifactsv1.CoPresence{Status: artifactsv1.CoPresenceStatus_CO_PRESENCE_STATUS_NO_SAMPLE}
	}
	result := &artifactsv1.CoPresence{
		Status:    artifactsv1.CoPresenceStatus_CO_PRESENCE_STATUS_SAMPLED,
		Sample:    &artifactsv1.RecordRef{Stream: artifactsv1.RecordStream_RECORD_STREAM_PERCEPTION, ConnectionId: reader.actor.metadata.ConnectionID, ServerTick: sample.GetServerTick()},
		Container: artifactsv1.TargetVisibility_TARGET_VISIBILITY_NOT_VISIBLE,
	}
	for _, value := range sample.GetVisibleBlockEntities() {
		if keyOf(value.GetDimension(), value.GetBlockPos()) == query.key {
			result.Container = artifactsv1.TargetVisibility_TARGET_VISIBILITY_VISIBLE
		}
	}
	for _, value := range sample.GetUndeterminedBlockEntities() {
		if keyOf(value.GetDimension(), value.GetBlockPos()) == query.key {
			result.Container = artifactsv1.TargetVisibility_TARGET_VISIBILITY_UNDETERMINED
		}
	}
	add := func(values []*artifactsv1.PerceivedEntity, state artifactsv1.TargetVisibility) {
		for _, value := range values {
			entity := &artifactsv1.CoPresentEntity{Uuid: value.Uuid, TypeId: value.GetTypeId(), Visibility: state}
			reader.participantMenu(entity, query)
			result.Entities = append(result.Entities, entity)
		}
	}
	add(sample.GetVisibleEntities(), artifactsv1.TargetVisibility_TARGET_VISIBILITY_VISIBLE)
	add(sample.GetUndeterminedEntities(), artifactsv1.TargetVisibility_TARGET_VISIBILITY_UNDETERMINED)
	return result
}

// participantMenu records, for a player entity that is a participant whose
// Play covers the tick, whether that participant had a menu backed by the
// container open at the divergence start.
func (reader *perceptionReader) participantMenu(entity *artifactsv1.CoPresentEntity, query presenceQuery) {
	if entity.GetTypeId() != playerEntityType {
		return
	}
	for _, other := range reader.participants[entity.GetUuid()] {
		if other == reader.actor || query.tick < other.metadata.StartTick || query.tick > other.metadata.EndTick {
			continue
		}
		entity.ParticipantConnectionId = other.metadata.ConnectionID
		open := false
		for _, menu := range other.timeline.menus {
			if menu.openAt(query.tick, query.key) {
				open = true
				entity.ParticipantMenu = menu.opened
			}
		}
		entity.ParticipantContainerOpen = &open
		return
	}
}
