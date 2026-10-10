package perceptions

import (
	"context"
	"errors"
	"fmt"
	"io"
	"math"
	"path/filepath"
	"sort"

	artifactsv1 "github.com/proj-airi/recorder-minecraft/apis/sdk/go/recorder-minecraft/artifacts/v1"
	"github.com/proj-airi/recorder-minecraft/internal/datastore"
	"github.com/proj-airi/recorder-minecraft/internal/models/captures"
	"github.com/proj-airi/recorder-minecraft/internal/models/processorfiles"
	"github.com/samber/do/v2"
	"google.golang.org/protobuf/encoding/protojson"
)

const (
	// ProcessorName identifies outputs this processor owns.
	ProcessorName = "recorder-minecraft perception extract"
	// ProcessorVersion changes whenever the visibility algorithm or the
	// output contract changes, so consumers can tell results apart.
	ProcessorVersion = "1"

	scope      = "actor perception"
	provenance = "reconstructed"

	DefaultIntervalTicks      = 4
	DefaultVerticalFOVDegrees = 70.0
	DefaultAspectRatio        = 16.0 / 9.0
	DefaultMaxDistanceBlocks  = 64.0

	// NOTICE: 0.05 is vanilla's near clip plane (GameRenderer.PROJECTION_Z_NEAR
	// in Minecraft 1.21.8).
	nearPlaneBlocks = 0.05
)

type Options struct {
	Metadata  string
	Events    string
	Scene     string
	Output    string
	FromTick  *int64
	ToTick    *int64
	Overwrite bool

	IntervalTicks      int64
	VerticalFOVDegrees float64
	AspectRatio        float64
	MaxDistanceBlocks  float64
}

type Result struct {
	Output                  string
	SampleCount             uint64
	FirstTick               int64
	LastTick                int64
	VisibleEntities         uint64
	VisibleBlockEntities    uint64
	UndeterminedTargets     uint64
	DecodedSectionBlobs     int
	SectionVersionsInWindow int
}

type Service struct {
	captures *captures.Service
}

func NewService(injector do.Injector) (*Service, error) {
	captureService, err := do.Invoke[*captures.Service](injector)
	if err != nil {
		return nil, err
	}
	return &Service{captures: captureService}, nil
}

// Extract writes perception.jsonl for one completed play.
//
// The metadata, event stream, and scene must describe the same connection.
// Inputs are only read; the result is staged beside the output and published
// atomically after every sample has been written and synced.
func (service *Service) Extract(ctx context.Context, options Options) (Result, error) {
	if err := validateOptions(options); err != nil {
		return Result{}, err
	}
	metadata, err := service.captures.LoadMetadata(options.Metadata)
	if err != nil {
		return Result{}, err
	}
	destination, err := processorfiles.PrepareOutput("perception", options.Output, options.Overwrite, ownedOutput)
	if err != nil {
		return Result{}, err
	}

	viewDistances := viewDistanceTimeline{}
	events, err := service.captures.ScanEvents(options.Events, metadata, func(event captures.Event) error {
		viewDistances.observe(event)
		return nil
	})
	if err != nil {
		return Result{}, err
	}
	metadataFile, err := processorfiles.Digest(metadata.Path)
	if err != nil {
		return Result{}, err
	}
	sceneFile, err := processorfiles.Digest(options.Scene)
	if err != nil {
		return Result{}, err
	}

	client, err := datastore.OpenScene(sceneFile.Path)
	if err != nil {
		return Result{}, err
	}
	defer func() { _ = client.Close() }()
	schemaInfo, err := client.SchemaInfo.Query().Only(ctx)
	if err != nil {
		return Result{}, fmt.Errorf("read scene schema info: %w", err)
	}
	if schemaInfo.SchemaName != sceneSchemaName || schemaInfo.SchemaVersion != sceneSchemaVersion {
		return Result{}, fmt.Errorf("scene input is %s v%d, not Scene Store V2", schemaInfo.SchemaName, schemaInfo.SchemaVersion)
	}
	sceneMeta, err := client.SceneMeta.Query().Only(ctx)
	if err != nil {
		return Result{}, fmt.Errorf("read scene metadata: %w", err)
	}
	if sceneMeta.SessionID != metadata.SessionID || sceneMeta.PlayerUUID != metadata.PlayerUUID || sceneMeta.ConnectionID != metadata.ConnectionID {
		return Result{}, errors.New("scene identity does not match capture metadata")
	}
	first, last := sceneMeta.StartTick, sceneMeta.EndTick
	if options.FromTick != nil {
		first = max(first, *options.FromTick)
	}
	if options.ToTick != nil {
		last = min(last, *options.ToTick)
	}
	if first > last {
		return Result{}, fmt.Errorf("requested ticks do not overlap the scene ticks %d..%d", sceneMeta.StartTick, sceneMeta.EndTick)
	}

	view, err := loadScene(ctx, client, first, last)
	if err != nil {
		return Result{}, err
	}
	sampleCount := uint64((last-first)/options.IntervalTicks + 1)
	lastSample := first + int64(sampleCount-1)*options.IntervalTicks
	outputDirectory := filepath.Dir(destination)
	header := &artifactsv1.PerceptionHeader{
		Processor: &artifactsv1.PerceptionProcessor{Name: ProcessorName, Version: ProcessorVersion},
		Scope:     scope, Provenance: provenance, UsesFutureContext: false,
		SessionId: metadata.SessionID, PlayerUuid: metadata.PlayerUUID, ConnectionId: metadata.ConnectionID,
		Ticks: &artifactsv1.TickRange{FirstTick: first, LastTick: lastSample},
		Inputs: []*artifactsv1.PerceptionInput{
			metadataFile.Lineage("capture_metadata", "application/json", outputDirectory),
			{Role: "capture_events", File: &artifactsv1.ArtifactFile{Path: processorfiles.RelativeInput(events.Path, outputDirectory), Sha256: events.SHA256,
				SizeBytes: uint64(events.SizeBytes), MediaType: "application/x-ndjson"}},
			sceneFile.Lineage("scene_store", "application/vnd.sqlite3", outputDirectory),
		},
		Assumptions:      assumptions(options),
		KnownLimitations: knownLimitations(),
		SampleCount:      sampleCount,
	}

	extraction := &extraction{
		view: view, options: options, viewDistances: viewDistances,
		projection: perspective(options.VerticalFOVDegrees, options.AspectRatio),
		entities:   newActiveSet(view.entities, func(t *entityTarget) int64 { return t.start }, func(t *entityTarget) int64 { return t.end }),
		blocks:     newActiveSet(view.blocks, func(t blockEntityTarget) int64 { return t.start }, func(t blockEntityTarget) int64 { return t.end }),
	}
	result := Result{Output: destination, SampleCount: sampleCount, FirstTick: first, LastTick: lastSample}
	err = processorfiles.Publish("perception", destination, options.Overwrite, func(writer io.Writer) error {
		if err := writeRecord(writer, &artifactsv1.PerceptionRecord{SchemaVersion: 1, Record: &artifactsv1.PerceptionRecord_Header{Header: header}}); err != nil {
			return err
		}
		for tick := first; tick <= lastSample; tick += options.IntervalTicks {
			if err := ctx.Err(); err != nil {
				return err
			}
			sample, err := extraction.sample(tick)
			if err != nil {
				return err
			}
			result.VisibleEntities += uint64(len(sample.GetVisibleEntities()))
			result.VisibleBlockEntities += uint64(len(sample.GetVisibleBlockEntities()))
			result.UndeterminedTargets += uint64(len(sample.GetUndeterminedEntities()) + len(sample.GetUndeterminedBlockEntities()))
			if err := writeRecord(writer, &artifactsv1.PerceptionRecord{SchemaVersion: 1, Record: &artifactsv1.PerceptionRecord_Sample{Sample: sample}}); err != nil {
				return err
			}
		}
		// The scene must not have changed underneath the samples; the event
		// stream and metadata were already checked when they were read.
		return sceneFile.Unchanged()
	})
	if err != nil {
		return Result{}, err
	}
	result.DecodedSectionBlobs = len(view.world.decoded)
	result.SectionVersionsInWindow = view.world.spanCount()
	return result, nil
}

func validateOptions(options Options) error {
	switch {
	case options.Scene == "":
		return errors.New("scene input is required")
	case options.FromTick != nil && options.ToTick != nil && *options.FromTick > *options.ToTick:
		return errors.New("first tick cannot be greater than last tick")
	case options.IntervalTicks < 1:
		return errors.New("sampling interval must be at least one tick")
	case !(options.VerticalFOVDegrees > 0 && options.VerticalFOVDegrees < 180):
		return errors.New("vertical FOV must be between 0 and 180 degrees")
	case !(options.AspectRatio > 0) || math.IsInf(options.AspectRatio, 0):
		return errors.New("aspect ratio must be positive")
	case !(options.MaxDistanceBlocks > 0) || math.IsInf(options.MaxDistanceBlocks, 0):
		return errors.New("max distance must be positive")
	}
	return nil
}

// extraction holds the per-run state that advances one sample at a time.
type extraction struct {
	view          *sceneView
	options       Options
	viewDistances viewDistanceTimeline
	projection    projection
	entities      *activeSet[*entityTarget]
	blocks        *activeSet[blockEntityTarget]
}

func (run *extraction) sample(tick int64) (*artifactsv1.PerceptionSample, error) {
	state := run.view.states[tick]
	if state == nil {
		return nil, fmt.Errorf("scene has no player state at tick %d", tick)
	}
	dimension := run.view.dimension(state.Dimension)
	height := eyeHeight(state.Pose)
	feet := vec{state.PositionX, state.PositionY, state.PositionZ}
	cam := camera{eye: feet.add(vec{0, height, 0}), frame: cameraBasis(state.Yaw, state.Pitch), projection: run.projection, near: nearPlaneBlocks}
	sample := &artifactsv1.PerceptionSample{
		ServerTick: tick, Dimension: state.Dimension, MaxDistanceBlocks: run.options.MaxDistanceBlocks,
		SceneCoverageComplete: run.view.coverage[tick],
		Observer: &artifactsv1.PerceptionObserver{
			EntityId: int32(state.EntityID), Position: &artifactsv1.Vector3{X: feet.x, Y: feet.y, Z: feet.z},
			Eye: &artifactsv1.Vector3{X: cam.eye.x, Y: cam.eye.y, Z: cam.eye.z}, Rotation: &artifactsv1.Rotation{Yaw: state.Yaw, Pitch: state.Pitch, HeadYaw: state.HeadYaw},
			Pose: state.Pose, EyeHeight: height,
		},
	}
	if chunks, ok := run.viewDistances.at(tick); ok {
		sample.ViewDistanceChunks = &chunks
		sample.MaxDistanceBlocks = math.Min(sample.MaxDistanceBlocks, float64(chunks)*16)
	}
	run.view.world.at(tick)

	for _, target := range run.entities.at(tick) {
		if target.dimension != state.Dimension || target.instanceID == state.EntityInstanceID || target.networkID != nil && *target.networkID == state.EntityID {
			continue
		}
		distance := target.box.distanceTo(cam.eye)
		if distance > sample.MaxDistanceBlocks {
			continue
		}
		decision, support := observe(run.view.world, cam, dimension, target.box, target.box.samplePoints(entityCornerFactor))
		if decision == verdictHidden {
			continue
		}
		perceived, err := run.perceivedEntity(target, distance, support)
		if err != nil {
			return nil, err
		}
		if decision == verdictVisible {
			sample.VisibleEntities = append(sample.VisibleEntities, perceived)
		} else {
			sample.UndeterminedEntities = append(sample.UndeterminedEntities, perceived)
		}
	}
	for _, target := range run.blocks.at(tick) {
		if target.dimension != state.Dimension {
			continue
		}
		box := target.box()
		distance := box.distanceTo(cam.eye)
		if distance > sample.MaxDistanceBlocks {
			continue
		}
		decision, support := observe(run.view.world, cam, dimension, box, box.samplePoints(blockEntityCornerFactor))
		if decision == verdictHidden {
			continue
		}
		perceived := &artifactsv1.PerceivedBlockEntity{
			Dimension: target.dimension, BlockPos: &artifactsv1.BlockPosition{X: int32(target.x), Y: int32(target.y), Z: int32(target.z)},
			TypeId: target.typeID, Distance: roundDistance(distance), Support: support,
		}
		if decision == verdictVisible {
			sample.VisibleBlockEntities = append(sample.VisibleBlockEntities, perceived)
		} else {
			sample.UndeterminedBlockEntities = append(sample.UndeterminedBlockEntities, perceived)
		}
	}
	if err := run.view.world.err; err != nil {
		return nil, err
	}
	sortEntities(sample.VisibleEntities)
	sortEntities(sample.UndeterminedEntities)
	sortBlockEntities(sample.VisibleBlockEntities)
	sortBlockEntities(sample.UndeterminedBlockEntities)
	return sample, nil
}

func (run *extraction) perceivedEntity(target *entityTarget, distance float64, support *artifactsv1.RaySupport) (*artifactsv1.PerceivedEntity, error) {
	uuid, err := run.view.entityUUID(target)
	if err != nil {
		return nil, err
	}
	perceived := &artifactsv1.PerceivedEntity{InstanceId: target.instanceID, Uuid: uuid, TypeId: target.typeID, Distance: roundDistance(distance), Support: support}
	if target.networkID != nil {
		network := int32(*target.networkID)
		perceived.NetworkId = &network
	}
	return perceived, nil
}

// Nearest first, then a stable identity order so equal inputs give equal lines.
func sortEntities(values []*artifactsv1.PerceivedEntity) {
	sort.Slice(values, func(i, j int) bool {
		if values[i].GetDistance() != values[j].GetDistance() {
			return values[i].GetDistance() < values[j].GetDistance()
		}
		return values[i].GetInstanceId() < values[j].GetInstanceId()
	})
}

func sortBlockEntities(values []*artifactsv1.PerceivedBlockEntity) {
	sort.Slice(values, func(i, j int) bool {
		left, right := values[i], values[j]
		if left.GetDistance() != right.GetDistance() {
			return left.GetDistance() < right.GetDistance()
		}
		a, b := left.GetBlockPos(), right.GetBlockPos()
		if a.GetX() != b.GetX() {
			return a.GetX() < b.GetX()
		}
		if a.GetY() != b.GetY() {
			return a.GetY() < b.GetY()
		}
		return a.GetZ() < b.GetZ()
	})
}

// viewDistanceTimeline tracks the latest client-requested and server view
// distances by tick. A sample reads only values recorded at or before its
// tick, which keeps the reconstruction causal.
type viewDistanceTimeline struct {
	client, server stepSeries
}

func (timeline *viewDistanceTimeline) observe(event captures.Event) {
	if information := event.Message.GetClientInformation(); information != nil && information.GetViewDistance() > 0 {
		timeline.client.record(event.ServerTick, information.GetViewDistance())
	}
	if state := event.Message.GetPlayerState(); state != nil && state.GetReplayCoverage().GetViewDistanceChunks() > 0 {
		timeline.server.record(event.ServerTick, state.GetReplayCoverage().GetViewDistanceChunks())
	}
}

// at returns the effective view distance: the server sends chunks only within
// the smaller of the two, and the client draws nothing beyond its own.
func (timeline viewDistanceTimeline) at(tick int64) (int32, bool) {
	client, hasClient := timeline.client.at(tick)
	server, hasServer := timeline.server.at(tick)
	switch {
	case hasClient && hasServer:
		return min(client, server), true
	case hasClient:
		return client, true
	case hasServer:
		return server, true
	}
	return 0, false
}

type stepValue struct {
	tick  int64
	value int32
}

// stepSeries is a piecewise-constant value that changes at recorded ticks.
type stepSeries []stepValue

func (series *stepSeries) record(tick int64, value int32) {
	if count := len(*series); count > 0 && (*series)[count-1].value == value {
		return
	}
	*series = append(*series, stepValue{tick, value})
}

func (series stepSeries) at(tick int64) (int32, bool) {
	index := sort.Search(len(series), func(i int) bool { return series[i].tick > tick })
	if index == 0 {
		return 0, false
	}
	return series[index-1].value, true
}

func (w *voxelWorld) spanCount() int {
	count := 0
	for _, spans := range w.spans {
		count += len(spans)
	}
	return count
}

func writeRecord(writer io.Writer, record *artifactsv1.PerceptionRecord) error {
	encoded, err := protojson.Marshal(record)
	if err != nil {
		return fmt.Errorf("encode perception ProtoJSON: %w", err)
	}
	if _, err := writer.Write(append(encoded, '\n')); err != nil {
		return fmt.Errorf("write perception JSONL: %w", err)
	}
	return nil
}

// ownedOutput recognizes a previous result by its header line: the first
// record must be a schema 1 header written by this processor.
func ownedOutput(path string) bool {
	line, ok := processorfiles.FirstLine(path)
	if !ok {
		return false
	}
	record := &artifactsv1.PerceptionRecord{}
	if protojson.Unmarshal(line, record) != nil || record.GetSchemaVersion() != 1 {
		return false
	}
	header := record.GetHeader()
	return header != nil && header.GetProcessor().GetName() == ProcessorName && header.GetScope() == scope && header.GetProvenance() == provenance
}
