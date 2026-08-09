package catalog

import (
	"archive/zip"
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
	"time"

	artifactsv1 "github.com/proj-airi/recorder-minecraft/apis/sdk/go/recorder-minecraft/artifacts/v1"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
	"google.golang.org/protobuf/encoding/protojson"
	"google.golang.org/protobuf/types/known/timestamppb"
)

func TestSnapshotReadsCanonicalPlay(t *testing.T) {
	t.Parallel()

	const (
		serverID     = "e9fe419a-022b-451d-8598-806887b987b5"
		playerID     = "25ec515b-aea2-4d35-a305-873d5cfe849d"
		connectionID = "63af3daf-27a7-4b0d-a225-ee909c34fd22"
	)
	startedAt := time.Date(2026, time.July, 28, 8, 46, 2, 587954623, time.UTC)
	root := t.TempDir()
	play := filepath.Join(root, "v1", "server--"+serverID, "players", "player--"+playerID, "plays", "20260728T084602.587Z--"+connectionID)
	require.NoError(t, os.MkdirAll(filepath.Join(play, "capture"), 0o750))
	metadata := &artifactsv1.ServerMetadata{
		SchemaVersion: 1,
		LayoutVersion: "v1",
		Server:        &artifactsv1.ServerIdentity{Name: "server", InstanceId: serverID},
		SessionId:     "session",
		Player:        &artifactsv1.PlayerIdentity{Name: "player", Uuid: playerID},
		Connection: &artifactsv1.Connection{
			Id: connectionID, StartedAt: timestamppb.New(startedAt), StartServerTick: 100,
		},
		Capture: &artifactsv1.CaptureMetadata{Events: "capture/events.jsonl", Replay: "capture/replay.zip", ReplayFormat: "flashback"},
	}
	raw, err := protojson.Marshal(metadata)
	require.NoError(t, err)
	require.NoError(t, os.WriteFile(filepath.Join(play, "metadata.json"), raw, 0o600))
	writeReplay(t, filepath.Join(play, "capture", "replay.zip"), "c9ed6af1-bf8d-4700-8e77-a04056be0932", playerID, connectionID)

	service, err := New(root)
	require.NoError(t, err)
	servers, err := service.Snapshot(context.Background(), Filter{StartedAtOrAfter: startedAt.Add(-time.Second), StartedBefore: startedAt.Add(time.Second)})
	require.NoError(t, err)
	require.Len(t, servers, 1)
	require.Len(t, servers[0].GetPlayers(), 1)
	require.Len(t, servers[0].GetPlayers()[0].GetReplays(), 1)
	replay := servers[0].GetPlayers()[0].GetReplays()[0]

	assert.Equal(t, connectionID, replay.GetConnectionId())
	assert.Equal(t, "/assets/v1/server--"+serverID+"/players/player--"+playerID+"/plays/20260728T084602.587Z--"+connectionID+"/capture/replay.zip", replay.GetReplayUrl())
	assert.Equal(t, "/assets/v1/server--"+serverID+"/players/player--"+playerID+"/plays/20260728T084602.587Z--"+connectionID+"/capture/events.jsonl", replay.GetEventsUrl())
	assert.Nil(t, replay.ValidationError)
}

func TestSnapshotKeepsInvalidReplayAsErrorResource(t *testing.T) {
	t.Parallel()

	const (
		serverID     = "e9fe419a-022b-451d-8598-806887b987b5"
		playerID     = "25ec515b-aea2-4d35-a305-873d5cfe849d"
		connectionID = "63af3daf-27a7-4b0d-a225-ee909c34fd22"
	)
	startedAt := time.Date(2026, time.July, 28, 8, 43, 10, 109000000, time.UTC)
	root := t.TempDir()
	play := filepath.Join(root, "v1", "server--"+serverID, "players", "player--"+playerID, "plays", "20260728T084310.109Z--"+connectionID)
	require.NoError(t, os.MkdirAll(filepath.Join(play, "capture"), 0o750))
	metadata := &artifactsv1.ServerMetadata{
		SchemaVersion: 1, LayoutVersion: "v1", SessionId: "session",
		Server:     &artifactsv1.ServerIdentity{Name: "server", InstanceId: serverID},
		Player:     &artifactsv1.PlayerIdentity{Name: "player", Uuid: playerID},
		Connection: &artifactsv1.Connection{Id: connectionID, StartedAt: timestamppb.New(startedAt), StartServerTick: 100},
		Capture:    &artifactsv1.CaptureMetadata{Events: "capture/events.jsonl", Replay: "capture/replay.zip", ReplayFormat: "flashback"},
	}
	raw, err := protojson.Marshal(metadata)
	require.NoError(t, err)
	require.NoError(t, os.WriteFile(filepath.Join(play, "metadata.json"), raw, 0o600))
	writeReplay(t, filepath.Join(play, "capture", "replay.zip"), "NOT-CANONICAL", playerID, connectionID)

	service, err := New(root)
	require.NoError(t, err)
	servers, err := service.Snapshot(context.Background(), Filter{})
	require.NoError(t, err)
	replay := servers[0].GetPlayers()[0].GetReplays()[0]
	require.NotNil(t, replay.ValidationError)
	assert.Contains(t, replay.GetValidationError(), "replay ID must use canonical UUID spelling")
	assert.Nil(t, replay.Video)
}

func TestSnapshotWithSummariesEnrichesCompletedPlay(t *testing.T) {
	t.Parallel()

	const (
		serverID     = "e9fe419a-022b-451d-8598-806887b987b5"
		playerID     = "25ec515b-aea2-4d35-a305-873d5cfe849d"
		connectionID = "63af3daf-27a7-4b0d-a225-ee909c34fd22"
	)
	startedAt := time.Date(2026, time.August, 9, 5, 0, 0, 0, time.UTC)
	root := t.TempDir()
	play := filepath.Join(root, "v1", "server--"+serverID, "players", "player--"+playerID, "plays", "20260809T050000Z--"+connectionID)
	require.NoError(t, os.MkdirAll(filepath.Join(play, "capture"), 0o750))
	endTick := int64(100)
	metadata := &artifactsv1.ServerMetadata{
		SchemaVersion: 1, LayoutVersion: "v1", SessionId: "session",
		Server: &artifactsv1.ServerIdentity{Name: "server", InstanceId: serverID},
		Player: &artifactsv1.PlayerIdentity{Name: "player", Uuid: playerID},
		Connection: &artifactsv1.Connection{
			Id: connectionID, StartedAt: timestamppb.New(startedAt), StartServerTick: 100, EndServerTick: &endTick,
		},
		Capture: &artifactsv1.CaptureMetadata{Events: "capture/events.jsonl", Replay: "capture/replay.zip", ReplayFormat: "flashback"},
	}
	raw, err := protojson.Marshal(metadata)
	require.NoError(t, err)
	require.NoError(t, os.WriteFile(filepath.Join(play, "metadata.json"), raw, 0o600))
	writeReplay(t, filepath.Join(play, "capture", "replay.zip"), "c9ed6af1-bf8d-4700-8e77-a04056be0932", playerID, connectionID)
	events := []*artifactsv1.CaptureEvent{
		{
			Identity: &artifactsv1.EventIdentity{SchemaVersion: 1, SessionId: "session", ServerTick: 100, Sequence: 1, PlayerUuid: playerID, ConnectionId: connectionID},
			Record: &artifactsv1.CaptureEvent_PlayerState{PlayerState: &artifactsv1.PlayerStateEvent{
				Dimension: "minecraft:overworld", Position: &artifactsv1.Vector3{}, Rotation: &artifactsv1.Rotation{},
				Velocity: &artifactsv1.Vector3{}, Abilities: &artifactsv1.Abilities{}, ReplayCoverage: &artifactsv1.ReplayCoverage{},
			}},
		},
		{
			Identity: &artifactsv1.EventIdentity{SchemaVersion: 1, SessionId: "session", ServerTick: 100, Sequence: 2, PlayerUuid: playerID, ConnectionId: connectionID},
			Record:   &artifactsv1.CaptureEvent_ControlState{ControlState: &artifactsv1.ControlStateEvent{State: &artifactsv1.ControlState{}}},
		},
	}
	eventFile, err := os.Create(filepath.Join(play, "capture", "events.jsonl"))
	require.NoError(t, err)
	for _, event := range events {
		raw, err := protojson.Marshal(event)
		require.NoError(t, err)
		_, err = eventFile.Write(append(raw, '\n'))
		require.NoError(t, err)
	}
	require.NoError(t, eventFile.Close())

	service, err := New(root)
	require.NoError(t, err)
	servers, err := service.SnapshotWithSummaries(context.Background(), Filter{})
	require.NoError(t, err)
	replay := servers[0].GetPlayers()[0].GetReplays()[0]
	require.NotNil(t, replay.GetSummary())
	assert.Equal(t, uint64(1), replay.GetSummary().GetDurationTicks())
	assert.Equal(t, 100.0, replay.GetSummary().GetIdlePercentage())
}

func TestSnapshotWithSummariesDoesNotOpenIncompletePlayEvents(t *testing.T) {
	t.Parallel()

	const (
		serverID     = "e9fe419a-022b-451d-8598-806887b987b5"
		playerID     = "25ec515b-aea2-4d35-a305-873d5cfe849d"
		connectionID = "63af3daf-27a7-4b0d-a225-ee909c34fd22"
	)
	startedAt := time.Date(2026, time.August, 9, 6, 0, 0, 0, time.UTC)
	root := t.TempDir()
	play := filepath.Join(root, "v1", "server--"+serverID, "players", "player--"+playerID, "plays", "20260809T060000Z--"+connectionID)
	require.NoError(t, os.MkdirAll(play, 0o750))
	metadata := &artifactsv1.ServerMetadata{
		SchemaVersion: 1, LayoutVersion: "v1", SessionId: "session",
		Server:     &artifactsv1.ServerIdentity{Name: "server", InstanceId: serverID},
		Player:     &artifactsv1.PlayerIdentity{Name: "player", Uuid: playerID},
		Connection: &artifactsv1.Connection{Id: connectionID, StartedAt: timestamppb.New(startedAt), StartServerTick: 100},
		Capture:    &artifactsv1.CaptureMetadata{Events: "capture/events.jsonl", Replay: "capture/replay.zip", ReplayFormat: "flashback"},
	}
	raw, err := protojson.Marshal(metadata)
	require.NoError(t, err)
	require.NoError(t, os.WriteFile(filepath.Join(play, "metadata.json"), raw, 0o600))

	service, err := New(root)
	require.NoError(t, err)
	servers, err := service.SnapshotWithSummaries(context.Background(), Filter{})
	require.NoError(t, err)
	replay := servers[0].GetPlayers()[0].GetReplays()[0]
	assert.Nil(t, replay.GetSummary())
}

func writeReplay(t *testing.T, path, replayID, playerID, connectionID string) {
	t.Helper()
	file, err := os.Create(path)
	require.NoError(t, err)
	archive := zip.NewWriter(file)
	flashback, err := archive.Create("metadata.json")
	require.NoError(t, err)
	_, err = flashback.Write([]byte(`{"chunks":{}}`))
	require.NoError(t, err)
	arcade, err := archive.Create("arcade_replay_meta.json")
	require.NoError(t, err)
	payload := map[string]any{"recorder-minecraft": map[string]string{
		"replay_id": replayID, "player_uuid": playerID, "connection_id": connectionID,
		"flashback_capture_contract": "client_visible_scene_v1",
	}}
	require.NoError(t, json.NewEncoder(arcade).Encode(payload))
	require.NoError(t, archive.Close())
	require.NoError(t, file.Close())
}
