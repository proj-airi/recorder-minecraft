package captures

import (
	"os"
	"path/filepath"
	"testing"

	artifactsv1 "github.com/proj-airi/recorder-minecraft/apis/sdk/go/recorder-minecraft/artifacts/v1"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
	"google.golang.org/protobuf/encoding/protojson"
	"google.golang.org/protobuf/proto"
)

const (
	testPlayer     = "00000000-0000-4000-8000-000000000001"
	testConnection = "00000000-0000-4000-8000-000000000002"
)

func TestEventViewNamesEveryRecordKind(t *testing.T) {
	// A oneof case without a name fails validateEvent and makes the whole Play unreadable, so every
	// generated case must map to its record type.
	cases := map[string]*artifactsv1.CaptureEvent{
		"control_state":      {Record: &artifactsv1.CaptureEvent_ControlState{ControlState: &artifactsv1.ControlStateEvent{}}},
		"packet_arrival":     {Record: &artifactsv1.CaptureEvent_PacketArrival{PacketArrival: &artifactsv1.PacketArrivalEvent{}}},
		"packet_apply":       {Record: &artifactsv1.CaptureEvent_PacketApply{PacketApply: &artifactsv1.PacketApplyEvent{}}},
		"player_state":       {Record: &artifactsv1.CaptureEvent_PlayerState{PlayerState: &artifactsv1.PlayerStateEvent{}}},
		"replay_timeline":    {Record: &artifactsv1.CaptureEvent_ReplayTimeline{ReplayTimeline: &artifactsv1.ReplayTimelineEvent{}}},
		"client_information": {Record: &artifactsv1.CaptureEvent_ClientInformation{ClientInformation: &artifactsv1.ClientInformationEvent{}}},
		"container_view":     {Record: &artifactsv1.CaptureEvent_ContainerView{ContainerView: &artifactsv1.ContainerViewEvent{}}},
	}
	oneof := (&artifactsv1.CaptureEvent{}).ProtoReflect().Descriptor().Oneofs().ByName("record")
	require.Equal(t, oneof.Fields().Len(), len(cases), "every CaptureEvent record needs an eventView case")
	for want, message := range cases {
		assert.Equal(t, want, eventView(message).RecordType)
	}
}

func TestScanEventsAcceptsActorPerceptionRecords(t *testing.T) {
	root := t.TempDir()
	metadataPath := filepath.Join(root, "metadata.json")
	eventsPath := filepath.Join(root, "events.jsonl")
	end := int64(11)
	writeProtoJSONLines(t, metadataPath, &artifactsv1.ServerMetadata{
		SchemaVersion: 1, LayoutVersion: "v1", SessionId: "session-a",
		Player:     &artifactsv1.PlayerIdentity{Uuid: testPlayer},
		Connection: &artifactsv1.Connection{Id: testConnection, StartServerTick: 10, EndServerTick: &end},
		Capture:    &artifactsv1.CaptureMetadata{Events: "capture/events.jsonl", Replay: "capture/replay.zip", ReplayFormat: "flashback"},
	})
	stateID := int32(3)
	writeProtoJSONLines(t, eventsPath,
		&artifactsv1.CaptureEvent{Identity: identity(10, 1), Record: &artifactsv1.CaptureEvent_ClientInformation{ClientInformation: &artifactsv1.ClientInformationEvent{
			Source: artifactsv1.ClientInformationSource_CLIENT_INFORMATION_SOURCE_JOIN_SNAPSHOT, ViewDistance: 12,
		}}},
		&artifactsv1.CaptureEvent{Identity: identity(11, 2), Record: &artifactsv1.CaptureEvent_ContainerView{ContainerView: &artifactsv1.ContainerViewEvent{
			Kind: artifactsv1.ContainerViewKind_CONTAINER_VIEW_KIND_CONTENTS, Origin: artifactsv1.ContainerViewOrigin_CONTAINER_VIEW_ORIGIN_CLIENTBOUND,
			ContainerId: 4, StateId: &stateID, MenuType: "minecraft:generic_9x3",
			Source: &artifactsv1.ContainerViewSource{Dimension: "minecraft:overworld", BlockPos: &artifactsv1.Vector3{X: 1, Y: 64, Z: -2}},
			Slots:  []*artifactsv1.InventorySlot{{Slot: 0, ItemId: "minecraft:diamond", Count: 1}},
		}}},
	)

	service := &Service{}
	metadata, err := service.LoadMetadata(metadataPath)
	require.NoError(t, err)
	var seen []Event
	source, err := service.ScanEvents(eventsPath, metadata, func(event Event) error {
		seen = append(seen, event)
		return nil
	})
	require.NoError(t, err)
	assert.Equal(t, uint64(2), source.RecordCount)
	require.Len(t, seen, 2)
	assert.Equal(t, "client_information", seen[0].RecordType)
	assert.Equal(t, int32(12), seen[0].Message.GetClientInformation().GetViewDistance())
	assert.Equal(t, "container_view", seen[1].RecordType)
	assert.Equal(t, "minecraft:diamond", seen[1].Message.GetContainerView().GetSlots()[0].GetItemId())
	assert.Equal(t, float64(64), seen[1].Message.GetContainerView().GetSource().GetBlockPos().GetY())
}

func identity(tick int64, sequence uint64) *artifactsv1.EventIdentity {
	return &artifactsv1.EventIdentity{SchemaVersion: 1, SessionId: "session-a", ServerTick: tick, Sequence: sequence, PlayerUuid: testPlayer, ConnectionId: testConnection}
}

// writeProtoJSONLines writes each message as one LF-terminated ProtoJSON line, which is both the
// event stream framing and a valid single-message metadata file.
func writeProtoJSONLines(t *testing.T, path string, messages ...proto.Message) {
	t.Helper()
	var content []byte
	for _, message := range messages {
		encoded, err := protojson.Marshal(message)
		require.NoError(t, err)
		content = append(append(content, encoded...), '\n')
	}
	require.NoError(t, os.WriteFile(path, content, 0o600))
}
