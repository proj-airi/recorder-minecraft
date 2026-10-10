package artifactsv1

import (
	"context"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/grpc-ecosystem/grpc-gateway/v2/runtime"
	apiv1 "github.com/proj-airi/recorder-minecraft/apis/sdk/go/recorder-minecraft/api/v1"
	artifacts "github.com/proj-airi/recorder-minecraft/apis/sdk/go/recorder-minecraft/artifacts/v1"
	"github.com/proj-airi/recorder-minecraft/internal/models/catalog"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
	"google.golang.org/protobuf/encoding/protojson"
	"google.golang.org/protobuf/types/known/timestamppb"
)

func TestListReplaysMapsCompletedSummaryFailureToDataLoss(t *testing.T) {
	t.Parallel()

	const (
		serverID     = "e9fe419a-022b-451d-8598-806887b987b5"
		playerID     = "25ec515b-aea2-4d35-a305-873d5cfe849d"
		connectionID = "63af3daf-27a7-4b0d-a225-ee909c34fd22"
	)
	root := t.TempDir()
	playPath := filepath.Join(root, "v1", "server--"+serverID, "players", "player--"+playerID, "plays", "20260809T050000Z--"+connectionID)
	require.NoError(t, os.MkdirAll(playPath, 0o750))
	endTick := int64(101)
	metadata := &artifacts.ServerMetadata{
		SchemaVersion: 1, LayoutVersion: "v1", SessionId: "session",
		Server: &artifacts.ServerIdentity{Name: "server", InstanceId: serverID},
		Player: &artifacts.PlayerIdentity{Name: "player", Uuid: playerID},
		Connection: &artifacts.Connection{
			Id: connectionID, StartedAt: timestamppb.New(time.Date(2026, time.August, 9, 5, 0, 0, 0, time.UTC)),
			StartServerTick: 100, EndServerTick: &endTick,
		},
		Capture: &artifacts.CaptureMetadata{Events: "capture/events.jsonl", Replay: "capture/replay.zip", ReplayFormat: "flashback"},
	}
	raw, err := protojson.Marshal(metadata)
	require.NoError(t, err)
	require.NoError(t, os.WriteFile(filepath.Join(playPath, "metadata.json"), raw, 0o600))
	catalogService, err := catalog.New(root)
	require.NoError(t, err)
	service := New(catalogService)

	response, err := service.ListReplays(context.Background(), &apiv1.ListReplaysRequest{IncludeSummary: true})
	require.Error(t, err)
	assert.Nil(t, response)
	assert.Equal(t, codes.DataLoss, status.Code(err))
	assert.Contains(t, err.Error(), "server_instance_id="+serverID)
	assert.Contains(t, err.Error(), "player_uuid="+playerID)
	assert.Contains(t, err.Error(), "connection_id="+connectionID)
	assert.NotContains(t, err.Error(), root)

	response, err = service.ListReplays(context.Background(), &apiv1.ListReplaysRequest{})
	require.NoError(t, err)
	require.Len(t, response.GetReplays(), 1)
	assert.Nil(t, response.GetReplays()[0].GetSummary())

	mux := runtime.NewServeMux()
	require.NoError(t, apiv1.RegisterArtifactCatalogServiceHandlerServer(context.Background(), mux, service))
	request := httptest.NewRequest(http.MethodGet, "/api/v1/replays?include_summary=true", nil)
	recorder := httptest.NewRecorder()
	mux.ServeHTTP(recorder, request)
	assert.Equal(t, http.StatusInternalServerError, recorder.Code)
	assert.Contains(t, recorder.Body.String(), `"code":15`)
	assert.Contains(t, recorder.Body.String(), "server_instance_id="+serverID)
	assert.Contains(t, recorder.Body.String(), "player_uuid="+playerID)
	assert.Contains(t, recorder.Body.String(), "connection_id="+connectionID)
	assert.NotContains(t, recorder.Body.String(), root)
}

func TestWorldSessionEndpoints(t *testing.T) {
	t.Parallel()

	const (
		serverID  = "e9fe419a-022b-451d-8598-806887b987b5"
		sessionID = "d6dda5df-652f-4727-a081-a1853a4fb0be"
	)
	root := t.TempDir()
	startedAt := time.Date(2026, time.October, 10, 8, 0, 3, 300000000, time.UTC)
	directory := "20261010T080003.300Z--" + sessionID
	sessionPath := filepath.Join(root, "v1", "server--"+serverID, "world", "sessions", directory)
	require.NoError(t, os.MkdirAll(sessionPath, 0o750))
	endTick := int64(1106)
	raw, err := protojson.Marshal(&artifacts.WorldSessionMetadata{
		SchemaVersion: 1, LayoutVersion: "v1", SessionId: sessionID, Scope: "world", Provenance: "engine-reported",
		Server:    &artifacts.ServerIdentity{Name: "server", InstanceId: serverID},
		StartedAt: timestamppb.New(startedAt), EndServerTick: &endTick, Events: "world-events.jsonl",
	})
	require.NoError(t, err)
	require.NoError(t, os.WriteFile(filepath.Join(sessionPath, "metadata.json"), raw, 0o600))
	require.NoError(t, os.WriteFile(filepath.Join(sessionPath, "world-events.jsonl"), nil, 0o600))
	catalogService, err := catalog.New(root)
	require.NoError(t, err)
	service := New(catalogService)
	mux := runtime.NewServeMux()
	require.NoError(t, apiv1.RegisterArtifactCatalogServiceHandlerServer(context.Background(), mux, service))
	get := func(method, path string) *httptest.ResponseRecorder {
		recorder := httptest.NewRecorder()
		mux.ServeHTTP(recorder, httptest.NewRequest(method, path, nil))
		return recorder
	}

	list := get(http.MethodGet, "/api/v1/world-sessions?server_instance_id="+serverID)
	require.Equal(t, http.StatusOK, list.Code, list.Body.String())
	response := &apiv1.ListWorldSessionsResponse{}
	require.NoError(t, protojson.Unmarshal(list.Body.Bytes(), response))
	require.Len(t, response.GetWorldSessions(), 1)
	assert.Equal(t, directory, response.GetWorldSessions()[0].GetId())
	assert.Equal(t, "/assets/v1/server--"+serverID+"/world/sessions/"+directory+"/world-events.jsonl", response.GetWorldSessions()[0].GetEventsUrl())

	empty := get(http.MethodGet, "/api/v1/world-sessions?session_id=other")
	require.Equal(t, http.StatusOK, empty.Code)
	assert.JSONEq(t, `{"worldSessions":[]}`, empty.Body.String())

	one := get(http.MethodGet, "/api/v1/server-instances/"+serverID+"/world-sessions/"+directory)
	require.Equal(t, http.StatusOK, one.Code, one.Body.String())
	assert.Contains(t, one.Body.String(), `"sessionId":"`+sessionID+`"`)

	missing, err := service.GetWorldSession(context.Background(), &apiv1.GetWorldSessionRequest{ServerInstanceId: serverID, WorldSessionId: "20200101T000000Z--missing"})
	assert.Nil(t, missing)
	assert.Equal(t, codes.NotFound, status.Code(err))

	summaries, err := service.ListServerInstances(context.Background(), &apiv1.ListServerInstancesRequest{})
	require.NoError(t, err)
	require.Len(t, summaries.GetServerInstances(), 1)
	assert.Equal(t, uint64(1), summaries.GetServerInstances()[0].GetWorldSessionCount())

	refresh := get(http.MethodPost, "/api/v1/catalog:refresh")
	require.Equal(t, http.StatusOK, refresh.Code, refresh.Body.String())
	refreshed := &apiv1.RefreshCatalogResponse{}
	require.NoError(t, protojson.Unmarshal(refresh.Body.Bytes(), refreshed))
	assert.Equal(t, uint64(1), refreshed.GetWorldSessionCount())
	assert.Equal(t, uint64(1), refreshed.GetServerInstanceCount())
	assert.NotNil(t, refreshed.GetRefreshedAt())
}
