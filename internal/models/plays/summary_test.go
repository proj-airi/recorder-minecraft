package plays

import (
	"context"
	"math"
	"os"
	"path/filepath"
	"testing"
	"time"

	artifactsv1 "github.com/proj-airi/recorder-minecraft/apis/sdk/go/recorder-minecraft/artifacts/v1"
	"github.com/proj-airi/recorder-minecraft/internal/models/captures"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
	"google.golang.org/protobuf/encoding/protojson"
	"google.golang.org/protobuf/proto"
	"google.golang.org/protobuf/types/known/timestamppb"
)

const (
	testPlayerID     = "25ec515b-aea2-4d35-a305-873d5cfe849d"
	testConnectionID = "63af3daf-27a7-4b0d-a225-ee909c34fd22"
)

func TestSummarizeDerivesMetricsFromPrimitiveCaptureInputs(t *testing.T) {
	t.Parallel()

	playPath := writePlay(t, 100, 104, []*artifactsv1.CaptureEvent{
		playerState(101, 1, "minecraft:overworld", 0, 0, 0, nil),
		controlState(101, 2, &artifactsv1.ControlState{SelectedSlot: 0}),
		playerState(102, 3, "minecraft:overworld", 3, 4, 0, nil),
		controlState(102, 4, &artifactsv1.ControlState{Forward: true, SelectedSlot: 0}),
		playerState(103, 5, "minecraft:overworld", 3, 4, 0, []*artifactsv1.InventorySlot{{Slot: 8, ItemId: "minecraft:diamond_pickaxe", Count: 1, Damage: 7, MaxDamage: 1561, ComponentsSnbt: "private", ComponentsDebug: "private"}}),
		controlState(103, 6, &artifactsv1.ControlState{SelectedSlot: 1}),
		packetApply(104, 7, "swing"),
	})

	summary, err := New(&captures.Service{}).Summarize(context.Background(), playPath)
	require.NoError(t, err)

	assert.Equal(t, int64(5), summary.DurationTicks)
	assert.InDelta(t, 5, summary.ObservedPathDistanceBlocks, 0.000001)
	assert.InDelta(t, 40, summary.IdlePercentage, 0.000001)
	assert.Equal(t, uint64(3), summary.PlayerStateCount)
	assert.Equal(t, []InventoryItem{{Slot: 8, ItemID: "minecraft:diamond_pickaxe", Count: 1, Damage: 7, MaxDamage: 1561}}, summary.FinalInventory)
}

func TestSummarizeUsesInclusiveOneTickDurationAndEmptyInventory(t *testing.T) {
	t.Parallel()

	playPath := writePlay(t, 100, 100, []*artifactsv1.CaptureEvent{
		playerState(100, 1, "minecraft:overworld", 1, 2, 3, nil),
		controlState(100, 2, &artifactsv1.ControlState{}),
	})

	summary, err := New(&captures.Service{}).Summarize(context.Background(), playPath)
	require.NoError(t, err)
	assert.Equal(t, int64(1), summary.DurationTicks)
	assert.Equal(t, uint64(1), summary.PlayerStateCount)
	assert.Empty(t, summary.FinalInventory)
}

func TestSummarizeObservedPathDistanceSemantics(t *testing.T) {
	t.Parallel()

	playPath := writePlay(t, 100, 103, []*artifactsv1.CaptureEvent{
		playerState(100, 1, "minecraft:overworld", 0, 0, 0, nil),
		controlState(100, 2, &artifactsv1.ControlState{}),
		playerState(101, 3, "minecraft:overworld", 0, 0, 0, nil),
		controlState(101, 4, &artifactsv1.ControlState{}),
		playerState(102, 5, "minecraft:the_nether", 100, 100, 100, nil),
		controlState(102, 6, &artifactsv1.ControlState{}),
		playerState(103, 7, "minecraft:the_nether", 103, 104, 100, nil),
		controlState(103, 8, &artifactsv1.ControlState{}),
	})

	summary, err := New(&captures.Service{}).Summarize(context.Background(), playPath)
	require.NoError(t, err)
	assert.InDelta(t, 5, summary.ObservedPathDistanceBlocks, 0.000001, "exclude the dimension transition and include the same-dimension displacement")
}

func TestSummarizeIdlePercentageControlSemantics(t *testing.T) {
	t.Parallel()

	tests := []struct {
		name     string
		controls []*artifactsv1.ControlState
		wantIdle float64
	}{
		{name: "FullyIdle", controls: []*artifactsv1.ControlState{{}, {}}, wantIdle: 100},
		{name: "MovementHeld", controls: []*artifactsv1.ControlState{{Forward: true}, {}}, wantIdle: 50},
		{name: "CameraOnly", controls: []*artifactsv1.ControlState{{CameraDeltaYaw: 1}, {}}, wantIdle: 50},
		{name: "SelectedSlotChange", controls: []*artifactsv1.ControlState{{SelectedSlot: 1}, {SelectedSlot: 2}}, wantIdle: 50},
	}
	for _, test := range tests {
		test := test
		t.Run(test.name, func(t *testing.T) {
			t.Parallel()
			playPath := writePlay(t, 100, 101, []*artifactsv1.CaptureEvent{
				playerState(100, 1, "minecraft:overworld", 0, 0, 0, nil),
				controlState(100, 2, test.controls[0]),
				playerState(101, 3, "minecraft:overworld", 0, 0, 0, nil),
				controlState(101, 4, test.controls[1]),
			})

			summary, err := New(&captures.Service{}).Summarize(context.Background(), playPath)
			require.NoError(t, err)
			assert.InDelta(t, test.wantIdle, summary.IdlePercentage, 0.000001)
		})
	}
}

func TestSummarizeIdlePercentagePacketCategories(t *testing.T) {
	t.Parallel()

	tests := []struct {
		actionKind string
		active     bool
	}{
		{actionKind: "player_action", active: true},
		{actionKind: "interact", active: true},
		{actionKind: "use", active: true},
		{actionKind: "swing", active: true},
		{actionKind: "inventory", active: true},
		{actionKind: "stance", active: true},
		{actionKind: "text_redacted", active: true},
		{actionKind: "camera_or_position"},
		{actionKind: "movement_controls"},
		{actionKind: "protocol_ack"},
		{actionKind: "tick_boundary"},
		{actionKind: "custom_payload_redacted"},
		{actionKind: "future_unknown_kind"},
	}
	for _, test := range tests {
		test := test
		t.Run(test.actionKind, func(t *testing.T) {
			t.Parallel()
			playPath := writePlay(t, 100, 100, []*artifactsv1.CaptureEvent{
				playerState(100, 1, "minecraft:overworld", 0, 0, 0, nil),
				controlState(100, 2, &artifactsv1.ControlState{}),
				packetApply(100, 3, test.actionKind),
			})

			summary, err := New(&captures.Service{}).Summarize(context.Background(), playPath)
			require.NoError(t, err)
			wantIdle := 100.0
			if test.active {
				wantIdle = 0
			}
			assert.InDelta(t, wantIdle, summary.IdlePercentage, 0.000001)
		})
	}
}

func TestSummarizeCountsMultipleActionsInOneTickOnce(t *testing.T) {
	t.Parallel()

	playPath := writePlay(t, 100, 101, []*artifactsv1.CaptureEvent{
		playerState(100, 1, "minecraft:overworld", 0, 0, 0, nil),
		controlState(100, 2, &artifactsv1.ControlState{}),
		packetApply(100, 3, "interact"),
		packetApply(100, 4, "swing"),
		playerState(101, 5, "minecraft:overworld", 0, 0, 0, nil),
		controlState(101, 6, &artifactsv1.ControlState{}),
	})

	summary, err := New(&captures.Service{}).Summarize(context.Background(), playPath)
	require.NoError(t, err)
	assert.InDelta(t, 50, summary.IdlePercentage, 0.000001)
}

func TestSummarizeRejectsCompletedPlayInvariantViolations(t *testing.T) {
	t.Parallel()

	validPlayer100 := playerState(100, 1, "minecraft:overworld", 0, 0, 0, nil)
	wrongIdentity := playerState(100, 1, "minecraft:overworld", 0, 0, 0, nil)
	wrongIdentity.Identity.PlayerUuid = "c9ed6af1-bf8d-4700-8e77-a04056be0932"
	tests := []struct {
		name    string
		events  []*artifactsv1.CaptureEvent
		wantErr string
	}{
		{name: "MissingPlayerState", events: []*artifactsv1.CaptureEvent{controlState(100, 1, &artifactsv1.ControlState{})}, wantErr: "no player_state"},
		{name: "MissingControlState", events: []*artifactsv1.CaptureEvent{validPlayer100}, wantErr: "no control_state"},
		{name: "DuplicatePlayerState", events: []*artifactsv1.CaptureEvent{
			validPlayer100, playerState(100, 2, "minecraft:overworld", 0, 0, 0, nil), controlState(100, 3, &artifactsv1.ControlState{}),
		}, wantErr: "duplicate player_state"},
		{name: "DuplicateControlState", events: []*artifactsv1.CaptureEvent{
			validPlayer100, controlState(100, 2, &artifactsv1.ControlState{}), controlState(100, 3, &artifactsv1.ControlState{}),
		}, wantErr: "duplicate control_state"},
		{name: "MismatchedTickSets", events: []*artifactsv1.CaptureEvent{
			validPlayer100, controlState(101, 2, &artifactsv1.ControlState{}),
		}, wantErr: "tick sets differ"},
		{name: "InternalGap", events: []*artifactsv1.CaptureEvent{
			validPlayer100, controlState(100, 2, &artifactsv1.ControlState{}),
			playerState(102, 3, "minecraft:overworld", 0, 0, 0, nil), controlState(102, 4, &artifactsv1.ControlState{}),
		}, wantErr: "internal gap"},
		{name: "NonFinitePosition", events: []*artifactsv1.CaptureEvent{
			playerState(100, 1, "minecraft:overworld", math.NaN(), 0, 0, nil), controlState(100, 2, &artifactsv1.ControlState{}),
		}, wantErr: "non-finite position"},
		{name: "NonFinitePlayerValue", events: []*artifactsv1.CaptureEvent{
			playerStateWithMutation(100, 1, func(state *artifactsv1.PlayerStateEvent) { state.Health = math.Inf(1) }),
			controlState(100, 2, &artifactsv1.ControlState{}),
		}, wantErr: "non-finite numeric value"},
		{name: "NonFiniteControlValue", events: []*artifactsv1.CaptureEvent{
			validPlayer100, controlState(100, 2, &artifactsv1.ControlState{CameraYaw: math.NaN()}),
		}, wantErr: "non-finite camera value"},
		{name: "NonFinitePacketValue", events: []*artifactsv1.CaptureEvent{
			validPlayer100, controlState(100, 2, &artifactsv1.ControlState{}), packetApplyWithX(100, 3, math.Inf(1)),
		}, wantErr: "non-finite camera or position value"},
		{name: "IncompletePacketArrival", events: []*artifactsv1.CaptureEvent{
			validPlayer100, controlState(100, 2, &artifactsv1.ControlState{}),
			{Identity: identity(100, 3), Record: &artifactsv1.CaptureEvent_PacketArrival{PacketArrival: &artifactsv1.PacketArrivalEvent{}}},
		}, wantErr: "packet_arrival at tick 100 is incomplete"},
		{name: "IncompleteReplayTimeline", events: []*artifactsv1.CaptureEvent{
			validPlayer100, controlState(100, 2, &artifactsv1.ControlState{}),
			{Identity: identity(100, 3), Record: &artifactsv1.CaptureEvent_ReplayTimeline{ReplayTimeline: &artifactsv1.ReplayTimelineEvent{}}},
		}, wantErr: "replay_timeline at tick 100 is incomplete"},
		{name: "NonFiniteDisplacement", events: []*artifactsv1.CaptureEvent{
			playerState(100, 1, "minecraft:overworld", math.MaxFloat64, 0, 0, nil), controlState(100, 2, &artifactsv1.ControlState{}),
			playerState(101, 3, "minecraft:overworld", -math.MaxFloat64, 0, 0, nil), controlState(101, 4, &artifactsv1.ControlState{}),
		}, wantErr: "non-finite displacement"},
		{name: "IdentityMismatch", events: []*artifactsv1.CaptureEvent{
			wrongIdentity, controlState(100, 2, &artifactsv1.ControlState{}),
		}, wantErr: "event subject does not match metadata"},
		{name: "InvalidSequenceOrdering", events: []*artifactsv1.CaptureEvent{
			validPlayer100, controlState(100, 1, &artifactsv1.ControlState{}),
		}, wantErr: "sequence is not strictly increasing"},
	}
	for _, test := range tests {
		test := test
		t.Run(test.name, func(t *testing.T) {
			t.Parallel()
			playPath := writePlay(t, 100, 102, test.events)
			_, err := New(&captures.Service{}).Summarize(context.Background(), playPath)
			require.Error(t, err)
			assert.ErrorContains(t, err, test.wantErr)
		})
	}
}

func TestSummarizeHonorsCancellationBeforeOpeningInputs(t *testing.T) {
	t.Parallel()

	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	_, err := New(&captures.Service{}).Summarize(ctx, filepath.Join(t.TempDir(), "missing-play"))
	assert.ErrorIs(t, err, context.Canceled)
}

func writePlay(t *testing.T, startTick, endTick int64, events []*artifactsv1.CaptureEvent) string {
	t.Helper()
	playPath := t.TempDir()
	require.NoError(t, os.MkdirAll(filepath.Join(playPath, "capture"), 0o750))
	metadata := &artifactsv1.ServerMetadata{
		SchemaVersion: 1,
		LayoutVersion: "v1",
		Server:        &artifactsv1.ServerIdentity{Name: "server", InstanceId: "e9fe419a-022b-451d-8598-806887b987b5"},
		SessionId:     "session",
		Player:        &artifactsv1.PlayerIdentity{Name: "player", Uuid: testPlayerID},
		Connection: &artifactsv1.Connection{
			Id: testConnectionID, StartedAt: timestamppb.New(time.Date(2026, time.August, 9, 0, 0, 0, 0, time.UTC)),
			StartServerTick: startTick, EndServerTick: &endTick,
		},
		Capture: &artifactsv1.CaptureMetadata{Events: "capture/events.jsonl", Replay: "capture/replay.zip", ReplayFormat: "flashback"},
	}
	writeProtoJSON(t, filepath.Join(playPath, "metadata.json"), metadata, false)
	for _, event := range events {
		writeProtoJSON(t, filepath.Join(playPath, "capture", "events.jsonl"), event, true)
	}
	return playPath
}

func writeProtoJSON(t *testing.T, path string, message proto.Message, appendLine bool) {
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

func identity(tick int64, sequence uint64) *artifactsv1.EventIdentity {
	return &artifactsv1.EventIdentity{
		SchemaVersion: 1, SessionId: "session", ServerTick: tick, Sequence: sequence,
		PlayerUuid: testPlayerID, ConnectionId: testConnectionID,
	}
}

func playerState(tick int64, sequence uint64, dimension string, x, y, z float64, inventory []*artifactsv1.InventorySlot) *artifactsv1.CaptureEvent {
	return &artifactsv1.CaptureEvent{
		Identity: identity(tick, sequence),
		Record: &artifactsv1.CaptureEvent_PlayerState{PlayerState: &artifactsv1.PlayerStateEvent{
			Dimension: dimension, Position: &artifactsv1.Vector3{X: x, Y: y, Z: z}, Rotation: &artifactsv1.Rotation{},
			Velocity: &artifactsv1.Vector3{}, Abilities: &artifactsv1.Abilities{}, ReplayCoverage: &artifactsv1.ReplayCoverage{}, Inventory: inventory,
		}},
	}
}

func playerStateWithMutation(tick int64, sequence uint64, mutate func(*artifactsv1.PlayerStateEvent)) *artifactsv1.CaptureEvent {
	event := playerState(tick, sequence, "minecraft:overworld", 0, 0, 0, nil)
	mutate(event.GetPlayerState())
	return event
}

func controlState(tick int64, sequence uint64, state *artifactsv1.ControlState) *artifactsv1.CaptureEvent {
	return &artifactsv1.CaptureEvent{
		Identity: identity(tick, sequence),
		Record:   &artifactsv1.CaptureEvent_ControlState{ControlState: &artifactsv1.ControlStateEvent{State: state}},
	}
}

func packetApply(tick int64, sequence uint64, actionKind string) *artifactsv1.CaptureEvent {
	return &artifactsv1.CaptureEvent{
		Identity: identity(tick, sequence),
		Record: &artifactsv1.CaptureEvent_PacketApply{PacketApply: &artifactsv1.PacketApplyEvent{
			Packet: &artifactsv1.Packet{Identity: &artifactsv1.PacketIdentity{}, ActionKind: actionKind},
		}},
	}
}

func packetApplyWithX(tick int64, sequence uint64, x float64) *artifactsv1.CaptureEvent {
	event := packetApply(tick, sequence, "camera_or_position")
	event.GetPacketApply().Packet.CameraOrPosition = &artifactsv1.CameraOrPositionAction{X: &x}
	return event
}
