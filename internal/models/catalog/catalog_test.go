package catalog

import (
	"archive/zip"
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
	"time"

	apiv1 "github.com/proj-airi/recorder-minecraft/apis/sdk/go/recorder-minecraft/api/v1"
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

func TestSnapshotListsPlayExtensionAssets(t *testing.T) {
	t.Parallel()

	const (
		serverID     = "e9fe419a-022b-451d-8598-806887b987b5"
		playerID     = "25ec515b-aea2-4d35-a305-873d5cfe849d"
		connectionID = "63af3daf-27a7-4b0d-a225-ee909c34fd22"
	)
	startedAt := time.Date(2026, time.August, 11, 8, 0, 0, 0, time.UTC)
	root := t.TempDir()
	play := filepath.Join(root, "v1", "server--"+serverID, "players", "player--"+playerID, "plays", "20260811T080000Z--"+connectionID)
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
	writeReplay(t, filepath.Join(play, "capture", "replay.zip"), "c9ed6af1-bf8d-4700-8e77-a04056be0932", playerID, connectionID)

	extensionPath := filepath.Join(play, "extensions", "airicraft.planner")
	require.NoError(t, os.MkdirAll(extensionPath, 0o750))
	require.NoError(t, os.WriteFile(filepath.Join(extensionPath, "planner-calls.jsonl"), []byte("{}\n"), 0o600))
	manifest := &artifactsv1.PlayExtensionManifest{
		ManifestVersion: 1,
		ExtensionType:   "airicraft.planner",
		Play: &artifactsv1.PlayExtensionIdentity{
			ServerInstanceId: serverID, PlayerUuid: playerID, ConnectionId: connectionID,
		},
		TimeDomain: artifactsv1.PlayExtensionTimeDomain_PLAY_EXTENSION_TIME_DOMAIN_SERVER_TICK,
		Assets: []*artifactsv1.PlayExtensionAsset{{
			Role: "planner_calls", Path: "planner-calls.jsonl", MediaType: "application/x-ndjson", Schema: "airicraft.planner-call.v1",
		}},
	}
	raw, err = protojson.Marshal(manifest)
	require.NoError(t, err)
	require.NoError(t, os.WriteFile(filepath.Join(extensionPath, "manifest.json"), raw, 0o600))
	annotationPath := filepath.Join(play, "extensions", "llm.annotation")
	require.NoError(t, os.MkdirAll(annotationPath, 0o750))
	require.NoError(t, os.WriteFile(filepath.Join(annotationPath, "annotations.jsonl"), []byte("{}\n"), 0o600))
	manifest.ExtensionType = "llm.annotation"
	manifest.Assets = []*artifactsv1.PlayExtensionAsset{{
		Role: "annotations", Path: "annotations.jsonl", MediaType: "application/x-ndjson", Schema: "llm.annotation.v1",
	}}
	raw, err = protojson.Marshal(manifest)
	require.NoError(t, err)
	require.NoError(t, os.WriteFile(filepath.Join(annotationPath, "manifest.json"), raw, 0o600))
	invalidPath := filepath.Join(play, "extensions", "invalid.extension")
	require.NoError(t, os.MkdirAll(invalidPath, 0o750))
	require.NoError(t, os.WriteFile(filepath.Join(invalidPath, "manifest.json"), []byte("not JSON"), 0o600))

	service, err := New(root)
	require.NoError(t, err)
	servers, err := service.Snapshot(context.Background(), Filter{})
	require.NoError(t, err)
	replay := servers[0].GetPlayers()[0].GetReplays()[0]
	require.Len(t, replay.GetExtensions(), 2)
	assert.Equal(t, "airicraft.planner", replay.GetExtensions()[0].GetExtensionType())
	require.Len(t, replay.GetExtensions()[0].GetAssets(), 1)
	assert.Equal(t, "/assets/v1/server--"+serverID+"/players/player--"+playerID+"/plays/20260811T080000Z--"+connectionID+"/extensions/airicraft.planner/planner-calls.jsonl", replay.GetExtensions()[0].GetAssets()[0].GetUrl())
	assert.Equal(t, "llm.annotation", replay.GetExtensions()[1].GetExtensionType(), "keep an unsupported but well-formed extension visible")
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

func TestSnapshotReadsFpvVideoManifest(t *testing.T) {
	t.Parallel()

	root, play := writeVideoPlay(t)
	writeFpvManifest(t, play, fpvManifest(int64(len(videoBytes))))

	video := snapshotVideo(t, root)
	require.NotNil(t, video)
	assert.Equal(t, uint32(640), video.GetWidth())
	assert.Equal(t, uint32(360), video.GetHeight())
	assert.Equal(t, 20.0, video.GetFramesPerSecond())
	assert.Equal(t, uint64(9840), video.GetFrameCount())
	assert.Equal(t, 492.0, video.GetDurationSeconds())
	timing := video.GetTiming()
	require.NotNil(t, timing)
	assert.Equal(t, apiv1.VideoTiming_FORMAT_FPV_MANIFEST, timing.GetFormat())
	assert.Equal(t, playURL+"/renders/fpv.json", timing.GetUrl())
	assert.Equal(t, int64(22), timing.GetFirstServerTick())
	assert.Equal(t, int64(6337), timing.GetLastServerTick())
	assert.Equal(t, uint64(3), timing.GetAnchorCount())
	assert.True(t, timing.GetComplete())
}

func TestSnapshotIgnoresFpvManifestThatDoesNotDescribeTheVideo(t *testing.T) {
	t.Parallel()

	for name, change := range map[string]func(*artifactsv1.FpvVideoManifest){
		"stale size": func(manifest *artifactsv1.FpvVideoManifest) { manifest.SizeBytes++ },
		"other connection": func(manifest *artifactsv1.FpvVideoManifest) {
			manifest.ConnectionId = "4f0f3f3c-4c3e-4a43-9d84-2a3c11c4c111"
		},
		"unordered anchors": func(manifest *artifactsv1.FpvVideoManifest) {
			manifest.Frames[2].VideoSeconds = manifest.Frames[1].GetVideoSeconds()
		},
		"no anchors": func(manifest *artifactsv1.FpvVideoManifest) { manifest.Frames = nil },
	} {
		t.Run(name, func(t *testing.T) {
			t.Parallel()

			root, play := writeVideoPlay(t)
			manifest := fpvManifest(int64(len(videoBytes)))
			change(manifest)
			writeFpvManifest(t, play, manifest)

			video := snapshotVideo(t, root)
			require.NotNil(t, video, "the MP4 stays playable without a usable manifest")
			assert.Nil(t, video.GetTiming())
			assert.Zero(t, video.GetWidth())
		})
	}
}

func TestSnapshotReadsRenderResultTiming(t *testing.T) {
	t.Parallel()

	root, play := writeVideoPlay(t)
	require.NoError(t, os.MkdirAll(filepath.Join(play, "renders", "fpv_frames"), 0o750))
	require.NoError(t, os.WriteFile(filepath.Join(play, "renders", "fpv_frames", "frames.jsonl"), []byte("{}\n"), 0o600))
	raw, err := protojson.Marshal(&artifactsv1.RenderResult{
		SchemaVersion: 1, Status: artifactsv1.RenderResultStatus_RENDER_RESULT_STATUS_COMPLETE,
		GlobalTicks: &artifactsv1.TickRange{FirstTick: 6337, LastTick: 9545}, FramesPerSecond: 20,
		Width: 854, Height: 480, FrameCount: 3209,
	})
	require.NoError(t, err)
	require.NoError(t, os.WriteFile(filepath.Join(play, "renders", "result.json"), raw, 0o600))

	video := snapshotVideo(t, root)
	require.NotNil(t, video)
	assert.Equal(t, uint32(854), video.GetWidth())
	assert.InDelta(t, 160.45, video.GetDurationSeconds(), 1e-9)
	timing := video.GetTiming()
	require.NotNil(t, timing)
	assert.Equal(t, apiv1.VideoTiming_FORMAT_RENDER_FRAME_INDEX, timing.GetFormat())
	assert.Equal(t, playURL+"/renders/fpv_frames/frames.jsonl", timing.GetUrl())
	assert.Equal(t, int64(6337), timing.GetFirstServerTick())
	assert.Equal(t, int64(9545), timing.GetLastServerTick())
	assert.Equal(t, uint64(3209), timing.GetAnchorCount())
}

const (
	videoServerID     = "71b4bb44-1f22-44f0-81f7-d6d654b8d109"
	videoPlayerID     = "2575798e-2b63-3ebe-a39e-5e3a3eba2b3f"
	videoConnectionID = "afe03965-030f-469f-8c5b-0dcd6c185c92"
	playURL           = "/assets/v1/server--" + videoServerID + "/players/Airi--" + videoPlayerID + "/plays/20261010T181823.114Z--" + videoConnectionID
)

var videoBytes = []byte("not a real MP4, but the catalog only checks its size")

func writeVideoPlay(t *testing.T) (string, string) {
	t.Helper()
	root := t.TempDir()
	play := filepath.Join(root, "v1", "server--"+videoServerID, "players", "Airi--"+videoPlayerID, "plays", "20261010T181823.114Z--"+videoConnectionID)
	require.NoError(t, os.MkdirAll(filepath.Join(play, "capture"), 0o750))
	require.NoError(t, os.MkdirAll(filepath.Join(play, "renders"), 0o750))
	metadata := &artifactsv1.ServerMetadata{
		SchemaVersion: 1, LayoutVersion: "v1", SessionId: "session",
		Server: &artifactsv1.ServerIdentity{Name: "server", InstanceId: videoServerID},
		Player: &artifactsv1.PlayerIdentity{Name: "Airi", Uuid: videoPlayerID},
		Connection: &artifactsv1.Connection{
			Id: videoConnectionID, StartedAt: timestamppb.New(time.Date(2026, time.October, 10, 18, 18, 23, 114000000, time.UTC)), StartServerTick: 11,
		},
		Capture: &artifactsv1.CaptureMetadata{Events: "capture/events.jsonl", Replay: "capture/replay.zip", ReplayFormat: "flashback"},
	}
	raw, err := protojson.Marshal(metadata)
	require.NoError(t, err)
	require.NoError(t, os.WriteFile(filepath.Join(play, "metadata.json"), raw, 0o600))
	writeReplay(t, filepath.Join(play, "capture", "replay.zip"), "c6dd59e9-4638-4b31-80b9-a23363d7a179", videoPlayerID, videoConnectionID)
	require.NoError(t, os.WriteFile(filepath.Join(play, "renders", "fpv.mp4"), videoBytes, 0o600))
	return root, play
}

// fpvManifest mirrors renders/fpv.json of an Airicraft hosted playtest, with three of its anchors.
func fpvManifest(videoSize int64) *artifactsv1.FpvVideoManifest {
	return &artifactsv1.FpvVideoManifest{
		SchemaVersion: 1, ServerInstanceId: videoServerID, PlayerUuid: videoPlayerID, ConnectionId: videoConnectionID,
		Frames: []*artifactsv1.FpvVideoAnchor{
			{VideoSeconds: 0, ServerTick: 22}, {VideoSeconds: 315.6, ServerTick: 6335}, {VideoSeconds: 315.8, ServerTick: 6337},
		},
		Complete: true, Width: 640, Height: 360, FramesPerSecond: 20, FrameCount: 9840, DurationSeconds: 492,
		SizeBytes: uint64(videoSize),
	}
}

func writeFpvManifest(t *testing.T, play string, manifest *artifactsv1.FpvVideoManifest) {
	t.Helper()
	raw, err := protojson.Marshal(manifest)
	require.NoError(t, err)
	require.NoError(t, os.WriteFile(filepath.Join(play, "renders", "fpv.json"), raw, 0o600))
}

func snapshotVideo(t *testing.T, root string) *apiv1.VideoAsset {
	t.Helper()
	service, err := New(root)
	require.NoError(t, err)
	servers, err := service.Snapshot(context.Background(), Filter{})
	require.NoError(t, err)
	replay := servers[0].GetPlayers()[0].GetReplays()[0]
	require.Nil(t, replay.ValidationError)
	return replay.GetVideo()
}
