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
