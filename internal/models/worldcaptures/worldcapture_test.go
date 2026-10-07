package worldcaptures

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	artifactsv1 "github.com/proj-airi/recorder-minecraft/apis/sdk/go/recorder-minecraft/artifacts/v1"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
	"google.golang.org/protobuf/encoding/protojson"
	"google.golang.org/protobuf/proto"
)

const session = "44444444-4444-4444-8444-444444444444"

func TestLoadMetadataAndScanEvents(t *testing.T) {
	t.Parallel()

	dir := sessionDir(t)
	metadataPath := writeMetadata(t, dir, nil)
	eventsPath := writeEvents(t, dir,
		snapshotEvent(1, 0, artifactsv1.ContainerSnapshot_REASON_SESSION_START, artifactsv1.ContainerSnapshot_CONTENTS_STATE_KNOWN),
		lootEvent(2, 0),
		snapshotEvent(3, 7, artifactsv1.ContainerSnapshot_REASON_CHANGED, artifactsv1.ContainerSnapshot_CONTENTS_STATE_KNOWN),
		removedEvent(4, 7, artifactsv1.ContainerRemoved_CAUSE_CHUNK_UNLOADED),
	)

	service := &Service{}
	metadata, err := service.LoadMetadata(metadataPath)
	require.NoError(t, err)
	assert.Equal(t, session, metadata.SessionID)
	assert.Equal(t, int64(0), metadata.StartTick)
	assert.Equal(t, int64(20), metadata.EndTick)
	assert.Empty(t, metadata.Failure)

	var kinds []string
	source, err := service.ScanEvents(eventsPath, metadata, func(event Event) error {
		kinds = append(kinds, event.RecordType)
		return nil
	})
	require.NoError(t, err)
	assert.Equal(t, uint64(4), source.RecordCount)
	assert.Len(t, source.SHA256, 64)
	assert.Equal(t, []string{RecordContainerSnapshot, RecordContainerSnapshot, RecordContainerSnapshot, RecordContainerRemoved}, kinds)
}

func TestLoadMetadataRejectsIncompleteOrForeignSessions(t *testing.T) {
	t.Parallel()

	cases := map[string]struct {
		edit    func(*artifactsv1.WorldSessionMetadata)
		message string
	}{
		"open stream":    {func(m *artifactsv1.WorldSessionMetadata) { m.EndServerTick = nil }, "incomplete"},
		"schema":         {func(m *artifactsv1.WorldSessionMetadata) { m.SchemaVersion = 2 }, "unsupported schema"},
		"scope":          {func(m *artifactsv1.WorldSessionMetadata) { m.Scope = "client_visible" }, "canonical world stream"},
		"other session":  {func(m *artifactsv1.WorldSessionMetadata) { m.SessionId = "other" }, "does not match its session_id"},
		"reversed range": {func(m *artifactsv1.WorldSessionMetadata) { m.StartServerTick = 30 }, "precedes its start"},
	}
	for name, test := range cases {
		t.Run(name, func(t *testing.T) {
			t.Parallel()
			_, err := (&Service{}).LoadMetadata(writeMetadata(t, sessionDir(t), test.edit))
			require.Error(t, err)
			assert.Contains(t, err.Error(), test.message)
		})
	}
}

func TestLoadMetadataExposesContainedFailure(t *testing.T) {
	t.Parallel()

	path := writeMetadata(t, sessionDir(t), func(m *artifactsv1.WorldSessionMetadata) {
		m.TerminalReason = proto.String("stream_failure")
		m.StreamFailure = proto.String("IOException: disk full")
	})
	metadata, err := (&Service{}).LoadMetadata(path)
	require.NoError(t, err)
	assert.Equal(t, "stream_failure", metadata.TerminalReason)
	assert.Contains(t, metadata.Failure, "disk full")
}

func TestScanEventsRejectsOrderingAndContentViolations(t *testing.T) {
	t.Parallel()

	known := artifactsv1.ContainerSnapshot_CONTENTS_STATE_KNOWN
	changed := artifactsv1.ContainerSnapshot_REASON_CHANGED
	cases := map[string]struct {
		events  []*artifactsv1.WorldEvent
		message string
	}{
		"repeated sequence": {
			[]*artifactsv1.WorldEvent{snapshotEvent(2, 1, changed, known), snapshotEvent(2, 1, changed, known)},
			"sequence is not strictly increasing",
		},
		"decreasing tick": {
			[]*artifactsv1.WorldEvent{snapshotEvent(1, 5, changed, known), snapshotEvent(2, 4, changed, known)},
			"tick decreases",
		},
		"tick after end": {
			[]*artifactsv1.WorldEvent{snapshotEvent(1, 21, changed, known)},
			"outside stream bounds",
		},
		"foreign session": {
			[]*artifactsv1.WorldEvent{withSession(snapshotEvent(1, 1, changed, known), "other")},
			"session does not match",
		},
		"missing record": {
			[]*artifactsv1.WorldEvent{{Identity: identity(1, 1)}},
			"unsupported world event schema",
		},
		"loot with slots": {
			[]*artifactsv1.WorldEvent{withSlot(lootEvent(1, 1), 0)},
			"ungenerated loot",
		},
		"slot outside container": {
			[]*artifactsv1.WorldEvent{withSlot(snapshotEvent(1, 1, changed, known), 27)},
			"outside the container",
		},
		"unspecified contents": {
			[]*artifactsv1.WorldEvent{snapshotEvent(1, 1, changed, artifactsv1.ContainerSnapshot_CONTENTS_STATE_UNSPECIFIED)},
			"no contents state",
		},
		"removal without cause": {
			[]*artifactsv1.WorldEvent{removedEvent(1, 1, artifactsv1.ContainerRemoved_CAUSE_UNSPECIFIED)},
			"no cause",
		},
	}
	for name, test := range cases {
		t.Run(name, func(t *testing.T) {
			t.Parallel()
			dir := sessionDir(t)
			service := &Service{}
			metadata, err := service.LoadMetadata(writeMetadata(t, dir, nil))
			require.NoError(t, err)
			_, err = service.ScanEvents(writeEvents(t, dir, test.events...), metadata, func(Event) error { return nil })
			require.Error(t, err)
			assert.Contains(t, err.Error(), test.message)
		})
	}
}

func TestScanEventsAcceptsEmptyStreamButRejectsUnterminatedLine(t *testing.T) {
	t.Parallel()

	dir := sessionDir(t)
	service := &Service{}
	metadata, err := service.LoadMetadata(writeMetadata(t, dir, nil))
	require.NoError(t, err)

	empty := writeEvents(t, dir)
	source, err := service.ScanEvents(empty, metadata, func(Event) error { return nil })
	require.NoError(t, err)
	assert.Zero(t, source.RecordCount)

	encoded, err := protojson.Marshal(removedEvent(1, 1, artifactsv1.ContainerRemoved_CAUSE_DESTROYED))
	require.NoError(t, err)
	require.NoError(t, os.WriteFile(empty, encoded, 0o600))
	_, err = service.ScanEvents(empty, metadata, func(Event) error { return nil })
	require.Error(t, err)
	assert.Contains(t, err.Error(), "LF-terminated")
}

func sessionDir(t *testing.T) string {
	t.Helper()
	dir := filepath.Join(t.TempDir(), "world", "sessions", "20260725T102030.125Z--"+session)
	require.NoError(t, os.MkdirAll(dir, 0o755))
	return dir
}

func writeMetadata(t *testing.T, dir string, edit func(*artifactsv1.WorldSessionMetadata)) string {
	t.Helper()
	value := &artifactsv1.WorldSessionMetadata{
		SchemaVersion:   1,
		LayoutVersion:   "v1",
		Server:          &artifactsv1.ServerIdentity{Name: "local-test", InstanceId: "11111111-1111-4111-8111-111111111111"},
		SessionId:       session,
		Scope:           Scope,
		Provenance:      Provenance,
		StartServerTick: 0,
		EndServerTick:   proto.Int64(20),
		TerminalReason:  proto.String("server_shutdown"),
		KnownGaps:       []string{"world_entities_not_recorded"},
		Events:          EventsFile,
	}
	if edit != nil {
		edit(value)
	}
	encoded, err := protojson.Marshal(value)
	require.NoError(t, err)
	path := filepath.Join(dir, "metadata.json")
	require.NoError(t, os.WriteFile(path, append(encoded, '\n'), 0o600))
	return path
}

func writeEvents(t *testing.T, dir string, events ...*artifactsv1.WorldEvent) string {
	t.Helper()
	var lines strings.Builder
	for _, event := range events {
		encoded, err := protojson.Marshal(event)
		require.NoError(t, err)
		lines.Write(encoded)
		lines.WriteByte('\n')
	}
	path := filepath.Join(dir, EventsFile)
	require.NoError(t, os.WriteFile(path, []byte(lines.String()), 0o600))
	return path
}

func identity(sequence uint64, tick int64) *artifactsv1.WorldEventIdentity {
	return &artifactsv1.WorldEventIdentity{SchemaVersion: 1, SessionId: session, ServerTick: tick, Sequence: sequence}
}

func snapshot(reason artifactsv1.ContainerSnapshot_Reason, state artifactsv1.ContainerSnapshot_ContentsState) *artifactsv1.ContainerSnapshot {
	return &artifactsv1.ContainerSnapshot{
		Dimension:       "minecraft:overworld",
		BlockPos:        &artifactsv1.BlockPosition{X: 1, Y: 64, Z: -3},
		BlockEntityType: "minecraft:chest",
		Reason:          reason,
		ContentsState:   state,
		ContainerSize:   27,
	}
}

func snapshotEvent(sequence uint64, tick int64, reason artifactsv1.ContainerSnapshot_Reason, state artifactsv1.ContainerSnapshot_ContentsState) *artifactsv1.WorldEvent {
	value := snapshot(reason, state)
	if state == artifactsv1.ContainerSnapshot_CONTENTS_STATE_KNOWN {
		value.Slots = []*artifactsv1.InventorySlot{{Slot: 0, ItemId: "minecraft:diamond", Count: 1}}
	}
	return &artifactsv1.WorldEvent{
		Identity: identity(sequence, tick),
		Record:   &artifactsv1.WorldEvent_ContainerSnapshot{ContainerSnapshot: value},
	}
}

func lootEvent(sequence uint64, tick int64) *artifactsv1.WorldEvent {
	value := snapshot(artifactsv1.ContainerSnapshot_REASON_SESSION_START, artifactsv1.ContainerSnapshot_CONTENTS_STATE_LOOT_UNGENERATED)
	value.LootTable = "minecraft:chests/simple_dungeon"
	return &artifactsv1.WorldEvent{
		Identity: identity(sequence, tick),
		Record:   &artifactsv1.WorldEvent_ContainerSnapshot{ContainerSnapshot: value},
	}
}

func removedEvent(sequence uint64, tick int64, cause artifactsv1.ContainerRemoved_Cause) *artifactsv1.WorldEvent {
	return &artifactsv1.WorldEvent{
		Identity: identity(sequence, tick),
		Record: &artifactsv1.WorldEvent_ContainerRemoved{ContainerRemoved: &artifactsv1.ContainerRemoved{
			Dimension:       "minecraft:overworld",
			BlockPos:        &artifactsv1.BlockPosition{X: 1, Y: 64, Z: -3},
			BlockEntityType: "minecraft:chest",
			Cause:           cause,
		}},
	}
}

func withSession(event *artifactsv1.WorldEvent, session string) *artifactsv1.WorldEvent {
	event.Identity.SessionId = session
	return event
}

func withSlot(event *artifactsv1.WorldEvent, slot int32) *artifactsv1.WorldEvent {
	snapshot := event.GetContainerSnapshot()
	snapshot.Slots = append(snapshot.Slots, &artifactsv1.InventorySlot{Slot: slot, ItemId: "minecraft:stone", Count: 1})
	return event
}
