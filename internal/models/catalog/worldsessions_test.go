package catalog

import (
	"context"
	"os"
	"path/filepath"
	"testing"
	"time"

	apiv1 "github.com/proj-airi/recorder-minecraft/apis/sdk/go/recorder-minecraft/api/v1"
	artifactsv1 "github.com/proj-airi/recorder-minecraft/apis/sdk/go/recorder-minecraft/artifacts/v1"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
	"google.golang.org/protobuf/encoding/protojson"
	"google.golang.org/protobuf/proto"
	"google.golang.org/protobuf/types/known/timestamppb"
)

const (
	fixtureServerID = "e9fe419a-022b-451d-8598-806887b987b5"
	fixtureAlice    = "25ec515b-aea2-4d35-a305-873d5cfe849d"
	fixtureBob      = "8e289159-2034-3a16-96b9-9fa637848b3b"
	fixtureSession  = "d6dda5df-652f-4727-a081-a1853a4fb0be"
	fixtureOther    = "024245d6-b07a-4ca5-8811-247c0f7bf2b4"
	aliceConnection = "63af3daf-27a7-4b0d-a225-ee909c34fd22"
	bobConnection   = "adfab141-a8be-4724-aef8-eb5223b11cee"
)

var fixtureStart = time.Date(2026, time.October, 10, 8, 0, 3, 300648412, time.UTC)

func serverPath(root string) string {
	return filepath.Join(root, "v1", "server--"+fixtureServerID)
}

func writeWorldSession(t *testing.T, root, sessionID string, startedAt time.Time, closed bool) string {
	t.Helper()
	directory := filepath.Join(serverPath(root), "world", "sessions", startedAt.Format("20060102T150405.000Z")+"--"+sessionID)
	require.NoError(t, os.MkdirAll(directory, 0o750))
	metadata := &artifactsv1.WorldSessionMetadata{
		SchemaVersion: 1, LayoutVersion: "v1",
		Server:    &artifactsv1.ServerIdentity{Name: "server", InstanceId: fixtureServerID},
		SessionId: sessionID, Scope: "world", Provenance: "engine-reported",
		StartedAt: timestamppb.New(startedAt), StartServerTick: 10,
		KnownGaps: []string{"world_entities_not_recorded"}, Events: "world-events.jsonl",
	}
	if closed {
		metadata.EndedAt = timestamppb.New(startedAt.Add(time.Minute))
		metadata.EndServerTick = proto.Int64(1106)
		metadata.TerminalReason = proto.String("server_shutdown")
	}
	writeProtoJSON(t, filepath.Join(directory, "metadata.json"), metadata)
	require.NoError(t, os.WriteFile(filepath.Join(directory, "world-events.jsonl"), nil, 0o600))
	return directory
}

func writeAlignment(t *testing.T, sessionDirectory, name, sessionID string) {
	t.Helper()
	require.NoError(t, os.MkdirAll(filepath.Join(sessionDirectory, "alignments"), 0o750))
	header := &artifactsv1.SessionAlignmentRecord{SchemaVersion: 1, Record: &artifactsv1.SessionAlignmentRecord_Header{Header: &artifactsv1.SessionAlignmentHeader{
		Scope: "session", SessionId: sessionID, EventCount: 7, DivergenceCount: 2,
		Participants: []*artifactsv1.SessionParticipant{
			{
				PlayerUuid: fixtureAlice, PlayerName: "alice", ConnectionId: aliceConnection,
				Coverage: &artifactsv1.TickRange{FirstTick: 232, LastTick: 916},
				Inputs:   []*artifactsv1.PerceptionInput{{Role: "capture_metadata"}, {Role: "perception"}},
			},
			{PlayerUuid: fixtureBob, PlayerName: "bob", ConnectionId: bobConnection, Coverage: &artifactsv1.TickRange{FirstTick: 242, LastTick: 896}},
		},
	}}}
	raw, err := protojson.Marshal(header)
	require.NoError(t, err)
	content := append(raw, []byte("\n{\"schemaVersion\":1,\"event\":{}}\n")...)
	require.NoError(t, os.WriteFile(filepath.Join(sessionDirectory, "alignments", name), content, 0o600))
}

func writePlay(t *testing.T, root, playerName, playerID, connectionID, sessionID string, startedAt time.Time, truth *artifactsv1.WorldTruthReference) string {
	t.Helper()
	play := filepath.Join(serverPath(root), "players", playerName+"--"+playerID, "plays", startedAt.Format("20060102T150405.000Z")+"--"+connectionID)
	require.NoError(t, os.MkdirAll(filepath.Join(play, "capture"), 0o750))
	writeProtoJSON(t, filepath.Join(play, "metadata.json"), &artifactsv1.ServerMetadata{
		SchemaVersion: 1, LayoutVersion: "v1", SessionId: sessionID,
		Server:              &artifactsv1.ServerIdentity{Name: "server", InstanceId: fixtureServerID},
		Player:              &artifactsv1.PlayerIdentity{Name: playerName, Uuid: playerID},
		Connection:          &artifactsv1.Connection{Id: connectionID, StartedAt: timestamppb.New(startedAt), StartServerTick: 232},
		Capture:             &artifactsv1.CaptureMetadata{Events: "capture/events.jsonl", Replay: "capture/replay.zip", ReplayFormat: "flashback"},
		WorldContainerTruth: truth,
	})
	writeReplay(t, filepath.Join(play, "capture", "replay.zip"), "c9ed6af1-bf8d-4700-8e77-a04056be0932", playerID, connectionID)
	return play
}

func writeProtoJSON(t *testing.T, path string, message proto.Message) {
	t.Helper()
	raw, err := protojson.Marshal(message)
	require.NoError(t, err)
	require.NoError(t, os.WriteFile(path, raw, 0o600))
}

func assetPath(root, path string) string {
	relative, _ := filepath.Rel(root, path)
	return "/assets/" + filepath.ToSlash(relative)
}

func TestSnapshotListsWorldSessionsWithAlignmentsAndLinkedPlays(t *testing.T) {
	t.Parallel()

	root := t.TempDir()
	session := writeWorldSession(t, root, fixtureSession, fixtureStart, true)
	writeAlignment(t, session, "session-alignment.jsonl", fixtureSession)
	require.NoError(t, os.WriteFile(filepath.Join(session, "alignments", "notes.txt"), []byte("ignored"), 0o600))
	reference := &artifactsv1.WorldTruthReference{
		Metadata: "world/sessions/" + filepath.Base(session) + "/metadata.json",
		Events:   "world/sessions/" + filepath.Base(session) + "/world-events.jsonl",
	}
	writePlay(t, root, "alice", fixtureAlice, aliceConnection, fixtureSession, fixtureStart.Add(11*time.Second), reference)
	// Bob joined while the world stream was unhealthy, so his metadata has no reference.
	writePlay(t, root, "bob", fixtureBob, bobConnection, fixtureSession, fixtureStart.Add(12*time.Second), nil)

	service, err := New(root)
	require.NoError(t, err)
	servers, err := service.Snapshot(context.Background(), Filter{})
	require.NoError(t, err)
	require.Len(t, servers, 1)
	require.Len(t, servers[0].GetWorldSessions(), 1)
	world := servers[0].GetWorldSessions()[0]

	assert.Nil(t, world.ValidationError)
	assert.Equal(t, filepath.Base(session), world.GetId())
	assert.Equal(t, fixtureSession, world.GetSessionId())
	assert.Equal(t, fixtureServerID, world.GetServerInstanceId())
	assert.Equal(t, fixtureStart, world.GetStartedAt().AsTime())
	assert.Equal(t, int64(10), world.GetStartServerTick())
	assert.Equal(t, int64(1106), world.GetEndServerTick())
	assert.Equal(t, "server_shutdown", world.GetTerminalReason())
	assert.Equal(t, []string{"world_entities_not_recorded"}, world.GetKnownGaps())
	assert.Equal(t, assetPath(root, filepath.Join(session, "metadata.json")), world.GetMetadataUrl())
	assert.Equal(t, assetPath(root, filepath.Join(session, "world-events.jsonl")), world.GetEventsUrl())

	require.Len(t, world.GetAlignments(), 1)
	alignment := world.GetAlignments()[0]
	assert.Nil(t, alignment.ValidationError)
	assert.Equal(t, "session-alignment", alignment.GetName())
	assert.Equal(t, assetPath(root, filepath.Join(session, "alignments", "session-alignment.jsonl")), alignment.GetUrl())
	assert.Equal(t, uint64(7), alignment.GetEventCount())
	assert.Equal(t, uint64(2), alignment.GetDivergenceCount())
	require.Len(t, alignment.GetParticipants(), 2)
	assert.True(t, proto.Equal(&apiv1.AlignmentParticipant{
		PlayerUuid: fixtureAlice, PlayerName: "alice", ConnectionId: aliceConnection,
		StartServerTick: 232, EndServerTick: 916, PerceptionProvided: true,
	}, alignment.GetParticipants()[0]))
	assert.False(t, alignment.GetParticipants()[1].GetPerceptionProvided())

	require.Len(t, world.GetPlays(), 2)
	assert.Equal(t, aliceConnection, world.GetPlays()[0].GetConnectionId())
	assert.Equal(t, apiv1.WorldSessionLink_WORLD_SESSION_LINK_CONTAINER_TRUTH, world.GetPlays()[0].GetLink())
	assert.Equal(t, bobConnection, world.GetPlays()[1].GetConnectionId())
	assert.Equal(t, apiv1.WorldSessionLink_WORLD_SESSION_LINK_SESSION_ID, world.GetPlays()[1].GetLink())

	aliceReplay := servers[0].GetPlayers()[0].GetReplays()[0]
	assert.Equal(t, filepath.Base(session), aliceReplay.GetWorldSessionId())
	assert.Equal(t, apiv1.WorldSessionLink_WORLD_SESSION_LINK_CONTAINER_TRUTH, aliceReplay.GetWorldSessionLink())
	assert.Equal(t, "alice", aliceReplay.GetPlayerName())
	assert.Equal(t, fixtureAlice, aliceReplay.GetPlayerUuid())
	assert.Nil(t, aliceReplay.PerceptionUrl, "absent derived files have no URL")
}

func TestSnapshotListsDerivedPlayFiles(t *testing.T) {
	t.Parallel()

	root := t.TempDir()
	play := writePlay(t, root, "alice", fixtureAlice, aliceConnection, fixtureSession, fixtureStart, nil)
	require.NoError(t, os.MkdirAll(filepath.Join(play, "renders", "fpv_frames"), 0o750))
	for _, name := range []string{"perception.jsonl", "actions.jsonl", "scene.sqlite3", "renders/fpv_frames/frames.jsonl"} {
		require.NoError(t, os.WriteFile(filepath.Join(play, filepath.FromSlash(name)), []byte("{}\n"), 0o600))
	}

	service, err := New(root)
	require.NoError(t, err)
	servers, err := service.Snapshot(context.Background(), Filter{})
	require.NoError(t, err)
	replay := servers[0].GetPlayers()[0].GetReplays()[0]
	assert.Equal(t, assetPath(root, filepath.Join(play, "perception.jsonl")), replay.GetPerceptionUrl())
	assert.Equal(t, assetPath(root, filepath.Join(play, "actions.jsonl")), replay.GetActionsUrl())
	assert.Equal(t, assetPath(root, filepath.Join(play, "scene.sqlite3")), replay.GetSceneUrl())
	assert.Equal(t, assetPath(root, filepath.Join(play, "renders", "fpv_frames", "frames.jsonl")), replay.GetFramesIndexUrl())
	assert.Nil(t, replay.WorldSessionId, "no world session exists")
	assert.Equal(t, apiv1.WorldSessionLink_WORLD_SESSION_LINK_UNSPECIFIED, replay.GetWorldSessionLink())
}

func TestSnapshotSkipsSymlinkedDerivedFiles(t *testing.T) {
	t.Parallel()

	root := t.TempDir()
	play := writePlay(t, root, "alice", fixtureAlice, aliceConnection, fixtureSession, fixtureStart, nil)
	outside := filepath.Join(t.TempDir(), "perception.jsonl")
	require.NoError(t, os.WriteFile(outside, []byte("{}\n"), 0o600))
	require.NoError(t, os.Symlink(outside, filepath.Join(play, "perception.jsonl")))
	require.NoError(t, os.Mkdir(filepath.Join(play, "scene.sqlite3"), 0o750))

	service, err := New(root)
	require.NoError(t, err)
	servers, err := service.Snapshot(context.Background(), Filter{})
	require.NoError(t, err)
	replay := servers[0].GetPlayers()[0].GetReplays()[0]
	assert.Nil(t, replay.PerceptionUrl)
	assert.Nil(t, replay.SceneUrl)
}

func TestSnapshotKeepsInvalidWorldSessionsVisible(t *testing.T) {
	t.Parallel()

	root := t.TempDir()
	open := writeWorldSession(t, root, fixtureSession, fixtureStart, false)
	missingEvents := writeWorldSession(t, root, fixtureOther, fixtureStart.Add(time.Hour), true)
	require.NoError(t, os.Remove(filepath.Join(missingEvents, "world-events.jsonl")))
	noMetadata := filepath.Join(serverPath(root), "world", "sessions", "20261010T100000Z--5f33e770-0d8c-4dd4-8f2b-f5bdc5cc82a0")
	require.NoError(t, os.MkdirAll(noMetadata, 0o750))
	writeAlignment(t, open, "other.jsonl", fixtureOther)
	require.NoError(t, os.WriteFile(filepath.Join(open, "alignments", "empty.jsonl"), nil, 0o600))
	require.NoError(t, os.MkdirAll(filepath.Join(serverPath(root), "world", "sessions", "not-a-session"), 0o750))

	service, err := New(root)
	require.NoError(t, err)
	sessions, err := service.WorldSessions(context.Background(), "", "")
	require.NoError(t, err)
	require.Len(t, sessions, 3)
	byID := map[string]*apiv1.WorldSession{}
	for _, session := range sessions {
		byID[session.GetId()] = session
		require.NotNil(t, session.ValidationError, session.GetId())
		assert.NotContains(t, session.GetValidationError(), root)
	}
	assert.Contains(t, byID[filepath.Base(open)].GetValidationError(), "incomplete")
	assert.Equal(t, fixtureSession, byID[filepath.Base(open)].GetSessionId())
	assert.Contains(t, byID[filepath.Base(missingEvents)].GetValidationError(), "world-events.jsonl")
	assert.Equal(t, "5f33e770-0d8c-4dd4-8f2b-f5bdc5cc82a0", byID[filepath.Base(noMetadata)].GetSessionId())

	alignments := byID[filepath.Base(open)].GetAlignments()
	require.Len(t, alignments, 2)
	assert.Equal(t, "empty", alignments[0].GetName())
	assert.Contains(t, alignments[0].GetValidationError(), "no complete header")
	assert.Equal(t, "other", alignments[1].GetName())
	assert.Contains(t, alignments[1].GetValidationError(), "another session")
	assert.Empty(t, alignments[1].GetParticipants())
}

func TestWorldSessionsFilterAndLookup(t *testing.T) {
	t.Parallel()

	root := t.TempDir()
	first := writeWorldSession(t, root, fixtureSession, fixtureStart, true)
	writeWorldSession(t, root, fixtureOther, fixtureStart.Add(time.Hour), true)

	service, err := New(root)
	require.NoError(t, err)
	sessions, err := service.WorldSessions(context.Background(), fixtureServerID, fixtureSession)
	require.NoError(t, err)
	require.Len(t, sessions, 1)
	assert.Equal(t, filepath.Base(first), sessions[0].GetId())

	sessions, err = service.WorldSessions(context.Background(), "00000000-0000-4000-8000-000000000000", "")
	require.NoError(t, err)
	assert.Empty(t, sessions)

	session, found, err := service.WorldSession(context.Background(), fixtureServerID, filepath.Base(first))
	require.NoError(t, err)
	require.True(t, found)
	assert.Equal(t, fixtureSession, session.GetSessionId())
	_, found, err = service.WorldSession(context.Background(), fixtureServerID, "20200101T000000Z--missing")
	require.NoError(t, err)
	assert.False(t, found)
}

func TestSessionIDFallbackRejectsAmbiguousSessions(t *testing.T) {
	t.Parallel()

	root := t.TempDir()
	writeWorldSession(t, root, fixtureSession, fixtureStart, true)
	// A second directory with the same session_id makes the fallback ambiguous.
	writeWorldSession(t, root, fixtureSession, fixtureStart.Add(time.Hour), true)
	writePlay(t, root, "bob", fixtureBob, bobConnection, fixtureSession, fixtureStart.Add(12*time.Second), nil)
	// A reference to a session that does not exist falls back to session_id too.
	writePlay(t, root, "alice", fixtureAlice, aliceConnection, fixtureSession, fixtureStart.Add(11*time.Second),
		&artifactsv1.WorldTruthReference{Metadata: "world/sessions/../../escape/metadata.json"})

	service, err := New(root)
	require.NoError(t, err)
	servers, err := service.Snapshot(context.Background(), Filter{})
	require.NoError(t, err)
	for _, player := range servers[0].GetPlayers() {
		assert.Nil(t, player.GetReplays()[0].WorldSessionId, player.GetName())
	}
}

func TestSnapshotCacheIsBoundedByTTLAndRefresh(t *testing.T) {
	t.Parallel()

	root := t.TempDir()
	service, err := New(root)
	require.NoError(t, err)
	now := fixtureStart
	service.now = func() time.Time { return now }

	sessions, err := service.WorldSessions(context.Background(), "", "")
	require.NoError(t, err)
	assert.Empty(t, sessions)

	writeWorldSession(t, root, fixtureSession, fixtureStart, true)
	sessions, err = service.WorldSessions(context.Background(), "", "")
	require.NoError(t, err)
	assert.Empty(t, sessions, "a read inside the TTL reuses the snapshot")

	result, err := service.Refresh(context.Background())
	require.NoError(t, err)
	assert.Equal(t, 1, result.WorldSessionCount)
	assert.Equal(t, 1, result.ServerInstances)

	writeWorldSession(t, root, fixtureOther, fixtureStart.Add(time.Hour), true)
	now = now.Add(DefaultCacheTTL)
	sessions, err = service.WorldSessions(context.Background(), "", "")
	require.NoError(t, err)
	assert.Len(t, sessions, 2, "an expired snapshot is rebuilt")

	// Returned messages are copies; mutating them does not leak into the cache.
	sessions[0].SessionId = "mutated"
	again, err := service.WorldSessions(context.Background(), "", "")
	require.NoError(t, err)
	assert.Equal(t, fixtureSession, again[0].GetSessionId())
}
