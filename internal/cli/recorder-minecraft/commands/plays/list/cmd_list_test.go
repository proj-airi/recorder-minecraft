package list

import (
	"bytes"
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
	"time"

	apiv1 "github.com/proj-airi/recorder-minecraft/apis/sdk/go/recorder-minecraft/api/v1"
	artifactsv1 "github.com/proj-airi/recorder-minecraft/apis/sdk/go/recorder-minecraft/artifacts/v1"
	catalogv1 "github.com/proj-airi/recorder-minecraft/apis/sdk/go/recorder-minecraft/catalog/v1"
	"github.com/proj-airi/recorder-minecraft/internal/configs"
	"github.com/spf13/cobra"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
	"google.golang.org/protobuf/encoding/protojson"
	"google.golang.org/protobuf/proto"
	"google.golang.org/protobuf/types/known/timestamppb"
)

const (
	cliServerA     = "e9fe419a-022b-451d-8598-806887b987b5"
	cliServerB     = "c9ed6af1-bf8d-4700-8e77-a04056be0932"
	cliPlayerA     = "25ec515b-aea2-4d35-a305-873d5cfe849d"
	cliPlayerB     = "56a50a28-2026-3b27-8279-243e3ff82a5a"
	cliConnectionA = "63af3daf-27a7-4b0d-a225-ee909c34fd22"
	cliConnectionB = "563360f9-5314-4e26-9091-083090b39932"
)

func TestFormatDurationUsesTwentyTicksPerSecond(t *testing.T) {
	t.Parallel()

	assert.Equal(t, "01:05 (1300 ticks)", formatDuration(1300))
	assert.Equal(t, "00:00 (19 ticks)", formatDuration(19))
}

func TestFlattenOrdersPlaysNewestFirst(t *testing.T) {
	t.Parallel()

	older := &apiv1.Replay{ConnectionId: "older", StartedAt: timestamppb.New(time.Date(2026, time.August, 8, 0, 0, 0, 0, time.UTC))}
	newer := &apiv1.Replay{ConnectionId: "newer", StartedAt: timestamppb.New(time.Date(2026, time.August, 9, 0, 0, 0, 0, time.UTC))}
	servers := []*apiv1.ServerInstance{{Players: []*apiv1.Player{{Replays: []*apiv1.Replay{older, newer}}}}}

	replays := flatten(servers)
	require.Len(t, replays, 2)
	assert.Equal(t, "newer", replays[0].GetConnectionId())
	assert.Equal(t, "older", replays[1].GetConnectionId())
}

func TestWritersPreserveExactJSONAndFormatCompactTable(t *testing.T) {
	t.Parallel()

	endTick := int64(120)
	replay := &apiv1.Replay{
		ConnectionId: "connection", ServerName: "server", ServerInstanceId: "server-id", PlayerName: "player", PlayerUuid: "player-id",
		StartedAt: timestamppb.New(time.Date(2026, time.August, 9, 5, 0, 0, 0, time.UTC)), StartServerTick: 100, EndServerTick: &endTick,
		Summary: &catalogv1.PlaySummary{
			DurationTicks: 21, ObservedPathDistanceBlocks: 3.456, IdlePercentage: 50.25, PlayerStateCount: 2,
			FinalInventory: []*catalogv1.FinalInventoryItem{{Slot: 8, ItemId: "minecraft:diamond", Count: 4, Damage: 0, MaxDamage: 0}},
		},
	}

	var table bytes.Buffer
	require.NoError(t, writeTable(&table, []*apiv1.Replay{replay}))
	assert.Contains(t, table.String(), "00:01 (21 ticks)")
	assert.Contains(t, table.String(), "3.5 blocks")
	assert.Contains(t, table.String(), "50.2%")
	assert.Contains(t, table.String(), "8:minecraft:diamond x4")

	var jsonOutput bytes.Buffer
	require.NoError(t, writeJSON(&jsonOutput, []*apiv1.Replay{replay}))
	assert.JSONEq(t, `[{"connection_id":"connection","server_name":"server","server_instance_id":"server-id","player_name":"player","player_uuid":"player-id","started_at":"2026-08-09T05:00:00Z","start_server_tick":100,"end_server_tick":120,"summary":{"duration_ticks":21,"observed_path_distance_blocks":3.456,"idle_percentage":50.25,"player_state_count":2,"final_inventory":[{"slot":8,"item_id":"minecraft:diamond","count":4,"damage":0,"max_damage":0}]}}]`, jsonOutput.String())
}

func TestOptionsBuildCatalogFilters(t *testing.T) {
	t.Parallel()

	filter, err := (options{
		ServerInstance:   "e9fe419a-022b-451d-8598-806887b987b5",
		Player:           "25ec515b-aea2-4d35-a305-873d5cfe849d",
		StartedAtOrAfter: "2026-08-09T05:00:00.123456789Z",
		StartedBefore:    "2026-08-10T05:00:00+08:00",
	}).filter()
	require.NoError(t, err)
	assert.Equal(t, "e9fe419a-022b-451d-8598-806887b987b5", filter.ServerInstanceID)
	assert.Equal(t, "25ec515b-aea2-4d35-a305-873d5cfe849d", filter.PlayerUUID)
	assert.Equal(t, int64(123456789), int64(filter.StartedAtOrAfter.Nanosecond()))
	assert.Equal(t, "2026-08-10T05:00:00+08:00", filter.StartedBefore.Format(time.RFC3339))

	_, err = (options{ServerInstance: "not-a-uuid"}).filter()
	assert.EqualError(t, err, "--server-instance must be a canonical UUID")
	_, err = (options{StartedBefore: "yesterday"}).filter()
	assert.ErrorContains(t, err, "--started-before must be an RFC 3339 timestamp")
}

func TestCommandListsFiltersAndKeepsIncompletePlays(t *testing.T) {
	t.Parallel()

	root := t.TempDir()
	configPath := filepath.Join(root, "recorder.toml")
	require.NoError(t, os.WriteFile(configPath, []byte(configs.DefaultText(false, cliServerA)), 0o600))
	artifacts := filepath.Join(root, "artifacts")
	completePath := writeCLIPlay(
		t, artifacts, "alpha", cliServerA, "Alex", cliPlayerA, cliConnectionA,
		time.Date(2026, time.August, 9, 5, 0, 0, 0, time.UTC), true, true,
	)
	assert.NotEmpty(t, completePath)
	writeCLIPlay(
		t, artifacts, "beta", cliServerB, "Steve", cliPlayerB, cliConnectionB,
		time.Date(2026, time.August, 9, 6, 0, 0, 0, time.UTC), false, false,
	)

	output, err := runListCommand(t, configPath, "--output", "json")
	require.NoError(t, err)
	var plays []jsonPlay
	require.NoError(t, json.Unmarshal([]byte(output), &plays))
	require.Len(t, plays, 2)
	assert.Equal(t, cliConnectionB, plays[0].ConnectionID, "newest Play must be first")
	assert.Nil(t, plays[0].Summary, "incomplete Play remains visible without opening its Event stream")
	assert.Equal(t, cliConnectionA, plays[1].ConnectionID)
	require.NotNil(t, plays[1].Summary)
	assert.Equal(t, uint64(1), plays[1].Summary.DurationTicks)

	filters := []struct {
		name string
		args []string
		want string
	}{
		{name: "ServerInstance", args: []string{"--server-instance", cliServerA}, want: cliConnectionA},
		{name: "Player", args: []string{"--player", cliPlayerB}, want: cliConnectionB},
		{name: "StartedAtOrAfter", args: []string{"--started-at-or-after", "2026-08-09T05:30:00Z"}, want: cliConnectionB},
		{name: "StartedBefore", args: []string{"--started-before", "2026-08-09T05:30:00Z"}, want: cliConnectionA},
	}
	for _, test := range filters {
		test := test
		t.Run(test.name, func(t *testing.T) {
			output, err := runListCommand(t, configPath, append([]string{"--output", "json"}, test.args...)...)
			require.NoError(t, err)
			var filtered []jsonPlay
			require.NoError(t, json.Unmarshal([]byte(output), &filtered))
			require.Len(t, filtered, 1)
			assert.Equal(t, test.want, filtered[0].ConnectionID)
		})
	}

	table, err := runListCommand(t, configPath)
	require.NoError(t, err)
	assert.Contains(t, table, "00:00 (1 ticks)")
	assert.Contains(t, table, cliConnectionB)
}

func TestCommandReturnsActionableCompletedPlayPath(t *testing.T) {
	t.Parallel()

	root := t.TempDir()
	configPath := filepath.Join(root, "recorder.toml")
	require.NoError(t, os.WriteFile(configPath, []byte(configs.DefaultText(false, cliServerA)), 0o600))
	playPath := writeCLIPlay(
		t, filepath.Join(root, "artifacts"), "alpha", cliServerA, "Alex", cliPlayerA, cliConnectionA,
		time.Date(2026, time.August, 9, 5, 0, 0, 0, time.UTC), true, false,
	)

	_, err := runListCommand(t, configPath, "--output", "json")
	require.Error(t, err)
	assert.ErrorContains(t, err, playPath)
}

func runListCommand(t *testing.T, configPath string, args ...string) (string, error) {
	t.Helper()
	root := &cobra.Command{Use: "recorder-minecraft", SilenceUsage: true, SilenceErrors: true}
	root.PersistentFlags().String("config", configPath, "configuration path")
	root.AddCommand(NewCommand())
	root.SetArgs(append([]string{"list"}, args...))
	var output bytes.Buffer
	root.SetOut(&output)
	root.SetErr(&output)
	err := root.ExecuteContext(context.Background())
	return output.String(), err
}

func writeCLIPlay(
	t *testing.T,
	artifacts, serverName, serverID, playerName, playerID, connectionID string,
	startedAt time.Time,
	completed, writeEvents bool,
) string {
	t.Helper()
	playPath := filepath.Join(
		artifacts, "v1", serverName+"--"+serverID, "players", playerName+"--"+playerID,
		"plays", startedAt.UTC().Format("20060102T150405.999999999Z")+"--"+connectionID,
	)
	require.NoError(t, os.MkdirAll(filepath.Join(playPath, "capture"), 0o750))
	metadata := &artifactsv1.ServerMetadata{
		SchemaVersion: 1, LayoutVersion: "v1", SessionId: "session-" + connectionID,
		Server: &artifactsv1.ServerIdentity{Name: serverName, InstanceId: serverID},
		Player: &artifactsv1.PlayerIdentity{Name: playerName, Uuid: playerID},
		Connection: &artifactsv1.Connection{
			Id: connectionID, StartedAt: timestamppb.New(startedAt), StartServerTick: 100,
		},
		Capture: &artifactsv1.CaptureMetadata{Events: "capture/events.jsonl", Replay: "capture/replay.zip", ReplayFormat: "flashback"},
	}
	if completed {
		endTick := int64(100)
		metadata.Connection.EndServerTick = &endTick
	}
	writeCLIProtoJSON(t, filepath.Join(playPath, "metadata.json"), metadata, false)
	if writeEvents {
		events := []*artifactsv1.CaptureEvent{
			{
				Identity: cliEventIdentity(metadata, playerID, connectionID, 1),
				Record: &artifactsv1.CaptureEvent_PlayerState{PlayerState: &artifactsv1.PlayerStateEvent{
					Dimension: "minecraft:overworld", Position: &artifactsv1.Vector3{}, Rotation: &artifactsv1.Rotation{},
					Velocity: &artifactsv1.Vector3{}, Abilities: &artifactsv1.Abilities{}, ReplayCoverage: &artifactsv1.ReplayCoverage{},
				}},
			},
			{
				Identity: cliEventIdentity(metadata, playerID, connectionID, 2),
				Record:   &artifactsv1.CaptureEvent_ControlState{ControlState: &artifactsv1.ControlStateEvent{State: &artifactsv1.ControlState{}}},
			},
		}
		for _, event := range events {
			writeCLIProtoJSON(t, filepath.Join(playPath, "capture", "events.jsonl"), event, true)
		}
	}
	return playPath
}

func cliEventIdentity(metadata *artifactsv1.ServerMetadata, playerID, connectionID string, sequence uint64) *artifactsv1.EventIdentity {
	return &artifactsv1.EventIdentity{
		SchemaVersion: 1, SessionId: metadata.GetSessionId(), ServerTick: 100, Sequence: sequence,
		PlayerUuid: playerID, ConnectionId: connectionID,
	}
}

func writeCLIProtoJSON(t *testing.T, path string, message proto.Message, appendLine bool) {
	t.Helper()
	raw, err := protojson.Marshal(message)
	require.NoError(t, err)
	if appendLine {
		raw = append(raw, '\n')
		file, err := os.OpenFile(path, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o600)
		require.NoError(t, err)
		_, err = file.Write(raw)
		require.NoError(t, err)
		require.NoError(t, file.Close())
		return
	}
	require.NoError(t, os.WriteFile(path, raw, 0o600))
}
