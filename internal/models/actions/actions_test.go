package actions

import (
	"bufio"
	"os"
	"path/filepath"
	"testing"

	artifactsv1 "github.com/proj-airi/mc-play-recorder/apis/sdk/go/mc-play-recorder/artifacts/v1"
	"github.com/proj-airi/mc-play-recorder/internal/models/captures"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
	"google.golang.org/protobuf/encoding/protojson"
)

func TestExtractWritesSelectedActionRecords(t *testing.T) {
	root := t.TempDir()
	metadata := filepath.Join(root, "metadata.json")
	events := filepath.Join(root, "events.jsonl")
	output := filepath.Join(root, "actions.jsonl")
	writeFixture(t, metadata, events)
	service := &Service{captures: &captures.Service{}}
	result, err := service.Extract(Options{Metadata: metadata, Events: events, Output: output})
	require.NoError(t, err)
	assert.Equal(t, uint64(2), result.RecordCount)
	assert.Equal(t, int64(10), result.FirstTick)
	assert.Equal(t, int64(10), result.LastTick)
	file, err := os.Open(output)
	require.NoError(t, err)
	defer func() { _ = file.Close() }()
	var actions []*artifactsv1.PlayerAction
	scanner := bufio.NewScanner(file)
	for scanner.Scan() {
		action := &artifactsv1.PlayerAction{}
		require.NoError(t, protojson.Unmarshal(scanner.Bytes(), action))
		actions = append(actions, action)
	}
	require.NoError(t, scanner.Err())
	require.Len(t, actions, 2)
	assert.Equal(t, "control_state", actions[0].GetSourceRecordType())
	assert.Equal(t, "packet_apply", actions[1].GetSourceRecordType())
}

func TestPrepareOutputOverwritesOnlyPlayerActionJSONL(t *testing.T) {
	t.Parallel()

	root := t.TempDir()
	valid := filepath.Join(root, "actions.jsonl")
	action := &artifactsv1.PlayerAction{
		SchemaVersion: 1, SourceRecordType: "control_state", ConnectionId: "connection", PlayerUuid: "player",
		Payload: &artifactsv1.PlayerAction_ControlState{ControlState: &artifactsv1.ControlState{}},
	}
	encoded, err := protojson.Marshal(action)
	require.NoError(t, err)
	require.NoError(t, os.WriteFile(valid, append(encoded, '\n'), 0o600))

	t.Run("RecognizedOutput", func(t *testing.T) {
		resolved, err := prepareOutput(valid, true)
		require.NoError(t, err)
		assert.Equal(t, valid, resolved)
	})

	t.Run("OverwriteRequired", func(t *testing.T) {
		_, err := prepareOutput(valid, false)
		require.Error(t, err)
		assert.Contains(t, err.Error(), "pass --overwrite")
	})

	t.Run("UnownedFile", func(t *testing.T) {
		unowned := filepath.Join(root, "notes.txt")
		require.NoError(t, os.WriteFile(unowned, []byte("keep me\n"), 0o600))
		_, err := prepareOutput(unowned, true)
		require.Error(t, err)
		assert.Contains(t, err.Error(), "refusing to replace non-PlayerAction output")
	})
}

func writeFixture(t *testing.T, metadata, events string) {
	t.Helper()
	const player = "00000000-0000-4000-8000-000000000001"
	const connection = "00000000-0000-4000-8000-000000000002"
	end := int64(11)
	metadataValue := &artifactsv1.ServerMetadata{
		SchemaVersion: 1, LayoutVersion: "v1", SessionId: "session-a",
		Player:     &artifactsv1.PlayerIdentity{Uuid: player},
		Connection: &artifactsv1.Connection{Id: connection, StartServerTick: 10, EndServerTick: &end},
		Capture:    &artifactsv1.CaptureMetadata{Events: "capture/events.jsonl", Replay: "capture/replay.zip", ReplayFormat: "flashback"},
	}
	raw, err := protojson.Marshal(metadataValue)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(metadata, raw, 0o600); err != nil {
		t.Fatal(err)
	}
	file, err := os.Create(events)
	if err != nil {
		t.Fatal(err)
	}
	identity := func(tick int64, sequence uint64) *artifactsv1.EventIdentity {
		return &artifactsv1.EventIdentity{SchemaVersion: 1, SessionId: "session-a", ServerTick: tick, Sequence: sequence, PlayerUuid: player, ConnectionId: connection}
	}
	records := []*artifactsv1.CaptureEvent{
		{Identity: identity(10, 1), Record: &artifactsv1.CaptureEvent_ControlState{ControlState: &artifactsv1.ControlStateEvent{State: &artifactsv1.ControlState{Forward: true}}}},
		{Identity: identity(10, 2), Record: &artifactsv1.CaptureEvent_PacketApply{PacketApply: &artifactsv1.PacketApplyEvent{ApplySequence: 1, Packet: &artifactsv1.Packet{ActionKind: "swing"}}}},
		{Identity: identity(11, 3), Record: &artifactsv1.CaptureEvent_PlayerState{PlayerState: &artifactsv1.PlayerStateEvent{}}},
	}
	for _, record := range records {
		encoded, err := protojson.Marshal(record)
		if err != nil {
			t.Fatal(err)
		}
		if _, err := file.Write(append(encoded, '\n')); err != nil {
			t.Fatal(err)
		}
	}
	if err := file.Close(); err != nil {
		t.Fatal(err)
	}
}
