package alignments

import (
	"bufio"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"os"
	"path/filepath"
	"strings"
	"testing"

	artifactsv1 "github.com/proj-airi/recorder-minecraft/apis/sdk/go/recorder-minecraft/artifacts/v1"
	"github.com/proj-airi/recorder-minecraft/internal/models/captures"
	"github.com/proj-airi/recorder-minecraft/internal/models/perceptions"
	"github.com/proj-airi/recorder-minecraft/internal/models/worldcaptures"
	"google.golang.org/protobuf/encoding/protojson"
	"google.golang.org/protobuf/proto"
)

const (
	sessionID  = "6f9d1c1e-8f6a-4c55-9d0a-2b9f3c1e7a10"
	aliceUUID  = "40f5db53-a47a-33ee-b1f6-db0e20deded4"
	aliceConn  = "4c7c85a8-f8cc-4bc3-830f-5754c51b1ddc"
	bobUUID    = "8e289159-2034-3a16-96b9-9fa637848b3b"
	bobConn    = "adfab141-a8be-4724-aef8-eb5223b11cee"
	overworld  = "minecraft:overworld"
	chestType  = "minecraft:chest"
	diamondID  = "minecraft:diamond"
	emeraldID  = "minecraft:emerald"
	worldEnd   = 85
	aliceStart = 5
	aliceEnd   = 80
	bobStart   = 6
	bobEnd     = 90
)

var (
	posA  = &artifactsv1.BlockPosition{X: 4, Y: -60, Z: 0}
	posB  = &artifactsv1.BlockPosition{X: 4, Y: -60, Z: 4}
	posB2 = &artifactsv1.BlockPosition{X: 4, Y: -60, Z: 5}
)

func writeLines(t *testing.T, path string, messages ...proto.Message) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o750); err != nil {
		t.Fatal(err)
	}
	var builder strings.Builder
	for _, message := range messages {
		encoded, err := protojson.Marshal(message)
		if err != nil {
			t.Fatal(err)
		}
		builder.Write(encoded)
		builder.WriteByte('\n')
	}
	if err := os.WriteFile(path, []byte(builder.String()), 0o600); err != nil {
		t.Fatal(err)
	}
}

func worldEvent(tick int64, sequence uint64, snapshot *artifactsv1.ContainerSnapshot) *artifactsv1.WorldEvent {
	return &artifactsv1.WorldEvent{
		Identity: &artifactsv1.WorldEventIdentity{SchemaVersion: 1, SessionId: sessionID, ServerTick: tick, Sequence: sequence},
		Record:   &artifactsv1.WorldEvent_ContainerSnapshot{ContainerSnapshot: snapshot},
	}
}

func chest(position *artifactsv1.BlockPosition, reason artifactsv1.ContainerSnapshot_Reason, slots ...*artifactsv1.InventorySlot) *artifactsv1.ContainerSnapshot {
	return &artifactsv1.ContainerSnapshot{Dimension: overworld, BlockPos: position, BlockEntityType: chestType, Reason: reason,
		ContentsState: artifactsv1.ContainerSnapshot_CONTENTS_STATE_KNOWN, ContainerSize: 27, Slots: slots}
}

type playWriter struct {
	t          *testing.T
	uuid, name string
	connection string
	start, end int64
	events     []proto.Message
	sequence   uint64
}

func (play *playWriter) add(tick int64, message *artifactsv1.CaptureEvent) {
	play.sequence++
	message.Identity = &artifactsv1.EventIdentity{SchemaVersion: 1, SessionId: sessionID, ServerTick: tick, Sequence: play.sequence,
		PlayerUuid: play.uuid, PlayerName: play.name, ConnectionId: play.connection, ConnectionStartServerTick: play.start}
	play.events = append(play.events, message)
}

func (play *playWriter) view(tick int64, kind artifactsv1.ContainerViewKind, id int32, primary, secondary *artifactsv1.BlockPosition, slots ...*artifactsv1.InventorySlot) {
	count := int32(27)
	if secondary != nil {
		count = 54
	}
	play.add(tick, &artifactsv1.CaptureEvent{Record: &artifactsv1.CaptureEvent_ContainerView{ContainerView: &artifactsv1.ContainerViewEvent{
		Kind: kind, ContainerId: id, ContainerSlotCount: &count, Slots: slots,
		Source: &artifactsv1.ContainerViewSource{Dimension: overworld, BlockPos: primary, BlockEntityType: chestType, SecondaryBlockPos: secondary},
	}}})
}

func (play *playWriter) look(tick int64, id int32, primary, secondary *artifactsv1.BlockPosition, slots ...*artifactsv1.InventorySlot) {
	play.view(tick, artifactsv1.ContainerViewKind_CONTAINER_VIEW_KIND_OPENED, id, primary, secondary)
	play.view(tick, artifactsv1.ContainerViewKind_CONTAINER_VIEW_KIND_CONTENTS, id, primary, secondary, slots...)
}

func (play *playWriter) click(tick int64) {
	play.add(tick, &artifactsv1.CaptureEvent{Record: &artifactsv1.CaptureEvent_PacketApply{PacketApply: &artifactsv1.PacketApplyEvent{
		Packet: &artifactsv1.Packet{Identity: &artifactsv1.PacketIdentity{PacketType: containerClickType}}}}})
}

// write stores the Play under root and returns its metadata and events paths.
func (play *playWriter) write(root, session string) (string, string) {
	dir := filepath.Join(root, "players", play.name+"--"+play.uuid, "plays", "20261010T080000Z--"+play.connection)
	end := play.end
	writeLines(play.t, filepath.Join(dir, "metadata.json"), &artifactsv1.ServerMetadata{
		SchemaVersion: 1, LayoutVersion: "v1", SessionId: session,
		Player:     &artifactsv1.PlayerIdentity{Name: play.name, Uuid: play.uuid},
		Connection: &artifactsv1.Connection{Id: play.connection, StartServerTick: play.start, EndServerTick: &end, TerminalReason: proto.String("disconnect")},
		Capture:    &artifactsv1.CaptureMetadata{Events: "capture/events.jsonl", Replay: "capture/replay.zip", ReplayFormat: "flashback"},
	})
	writeLines(play.t, filepath.Join(dir, "capture", "events.jsonl"), play.events...)
	return filepath.Join(dir, "metadata.json"), filepath.Join(dir, "capture", "events.jsonl")
}

func digestOf(t *testing.T, path string) string {
	t.Helper()
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	sum := sha256.Sum256(raw)
	return hex.EncodeToString(sum[:])
}

func perceived(position *artifactsv1.BlockPosition) *artifactsv1.PerceivedBlockEntity {
	return &artifactsv1.PerceivedBlockEntity{Dimension: overworld, BlockPos: position, TypeId: chestType}
}

func bobEntity() *artifactsv1.PerceivedEntity {
	return &artifactsv1.PerceivedEntity{InstanceId: "e2", Uuid: proto.String(bobUUID), TypeId: playerEntityType}
}

// writePerception writes alice's samples every 5 ticks: bob and chest A are
// visible from 25 to 35, and nothing is visible afterwards.
func writePerception(t *testing.T, path, eventsPath string) {
	header := &artifactsv1.PerceptionHeader{
		Processor: &artifactsv1.PerceptionProcessor{Name: perceptions.ProcessorName, Version: perceptions.ProcessorVersion},
		Scope:     "actor perception", Provenance: "reconstructed", SessionId: sessionID, PlayerUuid: aliceUUID, ConnectionId: aliceConn,
		Ticks:       &artifactsv1.TickRange{FirstTick: aliceStart, LastTick: aliceEnd},
		Inputs:      []*artifactsv1.PerceptionInput{{Role: "capture_events", File: &artifactsv1.ArtifactFile{Sha256: digestOf(t, eventsPath)}}},
		Assumptions: &artifactsv1.PerceptionAssumptions{SamplingIntervalTicks: 5},
	}
	messages := []proto.Message{&artifactsv1.PerceptionRecord{SchemaVersion: 1, Record: &artifactsv1.PerceptionRecord_Header{Header: header}}}
	for tick := int64(aliceStart); tick <= aliceEnd; tick += 5 {
		sample := &artifactsv1.PerceptionSample{ServerTick: tick, Dimension: overworld}
		if tick >= 25 && tick <= 35 {
			sample.VisibleEntities = []*artifactsv1.PerceivedEntity{bobEntity()}
			sample.VisibleBlockEntities = []*artifactsv1.PerceivedBlockEntity{perceived(posA)}
			sample.UndeterminedBlockEntities = []*artifactsv1.PerceivedBlockEntity{perceived(posB2)}
		}
		messages = append(messages, &artifactsv1.PerceptionRecord{SchemaVersion: 1, Record: &artifactsv1.PerceptionRecord_Sample{Sample: sample}})
	}
	writeLines(t, path, messages...)
}

type fixture struct {
	worldMetadata, worldEvents string
	alice, bob                 PlayInput
}

// sallyAnne writes a session: alice observes chest A (a diamond) and double
// chest B (empty); bob moves the diamond from A into B's second half with
// container clicks; an emerald later appears in A without any actor.
func sallyAnne(t *testing.T, root string) fixture {
	worldDir := filepath.Join(root, "world", "sessions", "20261010T080000Z--"+sessionID)
	end := int64(worldEnd)
	writeLines(t, filepath.Join(worldDir, "metadata.json"), &artifactsv1.WorldSessionMetadata{
		SchemaVersion: 1, LayoutVersion: "v1", SessionId: sessionID, Scope: worldcaptures.Scope, Provenance: worldcaptures.Provenance,
		StartServerTick: 0, EndServerTick: &end, TerminalReason: proto.String("server_shutdown"), Events: worldcaptures.EventsFile,
	})
	diamond := &artifactsv1.InventorySlot{ItemId: diamondID, Count: 1}
	writeLines(t, filepath.Join(worldDir, worldcaptures.EventsFile),
		worldEvent(1, 1, chest(posA, artifactsv1.ContainerSnapshot_REASON_LOADED, diamond)),
		worldEvent(1, 2, chest(posB, artifactsv1.ContainerSnapshot_REASON_LOADED)),
		worldEvent(1, 3, chest(posB2, artifactsv1.ContainerSnapshot_REASON_LOADED)),
		// An unrelated container nobody opens.
		worldEvent(1, 4, chest(&artifactsv1.BlockPosition{X: 40, Y: -60, Z: 40}, artifactsv1.ContainerSnapshot_REASON_LOADED)),
		worldEvent(30, 5, chest(posA, artifactsv1.ContainerSnapshot_REASON_CHANGED)),
		worldEvent(45, 6, chest(posB2, artifactsv1.ContainerSnapshot_REASON_CHANGED, &artifactsv1.InventorySlot{Slot: 3, ItemId: diamondID, Count: 1})),
		worldEvent(70, 7, chest(posA, artifactsv1.ContainerSnapshot_REASON_CHANGED, &artifactsv1.InventorySlot{ItemId: emeraldID, Count: 1})),
	)

	alice := &playWriter{t: t, uuid: aliceUUID, name: "alice", connection: aliceConn, start: aliceStart, end: aliceEnd}
	alice.look(10, 1, posA, nil, &artifactsv1.InventorySlot{ItemId: diamondID, Count: 1})
	alice.view(11, artifactsv1.ContainerViewKind_CONTAINER_VIEW_KIND_CLOSED, 1, posA, nil)
	alice.look(12, 2, posB, posB2)
	alice.view(13, artifactsv1.ContainerViewKind_CONTAINER_VIEW_KIND_CLOSED, 2, posB, posB2)
	alice.look(60, 3, posA, nil)
	alice.view(61, artifactsv1.ContainerViewKind_CONTAINER_VIEW_KIND_CLOSED, 3, posA, nil)

	bob := &playWriter{t: t, uuid: bobUUID, name: "bob", connection: bobConn, start: bobStart, end: bobEnd}
	bob.look(20, 1, posA, nil, &artifactsv1.InventorySlot{ItemId: diamondID, Count: 1})
	bob.click(30)
	bob.view(31, artifactsv1.ContainerViewKind_CONTAINER_VIEW_KIND_CLOSED, 1, posA, nil)
	bob.look(40, 2, posB, posB2)
	bob.click(45)
	bob.view(46, artifactsv1.ContainerViewKind_CONTAINER_VIEW_KIND_CLOSED, 2, posB, posB2)

	aliceMetadata, aliceEvents := alice.write(root, sessionID)
	bobMetadata, bobEvents := bob.write(root, sessionID)
	alicePerception := filepath.Join(filepath.Dir(aliceMetadata), "perception.jsonl")
	writePerception(t, alicePerception, aliceEvents)
	return fixture{
		worldMetadata: filepath.Join(worldDir, "metadata.json"), worldEvents: filepath.Join(worldDir, worldcaptures.EventsFile),
		alice: PlayInput{Metadata: aliceMetadata, Events: aliceEvents, Perception: alicePerception},
		bob:   PlayInput{Metadata: bobMetadata, Events: bobEvents},
	}
}

func readOutput(t *testing.T, path string) (*artifactsv1.SessionAlignmentHeader, []*artifactsv1.AlignedEvent, []*artifactsv1.ContainerDivergence) {
	t.Helper()
	file, err := os.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = file.Close() }()
	var header *artifactsv1.SessionAlignmentHeader
	var events []*artifactsv1.AlignedEvent
	var divergences []*artifactsv1.ContainerDivergence
	scanner := bufio.NewScanner(file)
	scanner.Buffer(nil, 16*1024*1024)
	for scanner.Scan() {
		record := &artifactsv1.SessionAlignmentRecord{}
		if err := protojson.Unmarshal(scanner.Bytes(), record); err != nil {
			t.Fatal(err)
		}
		switch {
		case record.GetHeader() != nil:
			header = record.GetHeader()
		case record.GetEvent() != nil:
			events = append(events, record.GetEvent())
		case record.GetDivergence() != nil:
			divergences = append(divergences, record.GetDivergence())
		}
	}
	return header, events, divergences
}

func newTestService() *Service {
	return &Service{captures: &captures.Service{}, world: &worldcaptures.Service{}}
}

func TestAlignSallyAnneEndToEnd(t *testing.T) {
	root := t.TempDir()
	inputs := sallyAnne(t, root)
	output := filepath.Join(root, "derived", "session-alignment.jsonl")
	options := Options{WorldMetadata: inputs.worldMetadata, WorldEvents: inputs.worldEvents, Plays: []PlayInput{inputs.bob, inputs.alice}, Output: output}
	result, err := newTestService().Align(context.Background(), options)
	if err != nil {
		t.Fatal(err)
	}
	header, events, divergences := readOutput(t, output)

	if header.GetScope() != "session" || header.GetProvenance() != "deterministic transform" || !header.GetUsesFutureContext() || header.GetSessionId() != sessionID {
		t.Fatalf("header = %v", header)
	}
	if len(header.GetParticipants()) != 2 || header.GetParticipants()[0].GetConnectionId() != aliceConn {
		t.Fatalf("participants = %v, want alice first by start tick", header.GetParticipants())
	}
	aliceInfo, bobInfo := header.GetParticipants()[0], header.GetParticipants()[1]
	if len(aliceInfo.GetInputs()) != 3 || aliceInfo.GetPerceptionIntervalTicks() != 5 || len(bobInfo.GetInputs()) != 2 || bobInfo.GetPerceptionCoverage() != nil {
		t.Fatalf("participant inputs: alice %v, bob %v", aliceInfo, bobInfo)
	}
	// Four world containers: each actor observed A, B, and B2, never the fourth.
	for _, participant := range header.GetParticipants() {
		if participant.GetObservedContainerCount() != 3 || participant.GetUnobservedContainerCount() != 1 {
			t.Fatalf("participant %s observed %d, unobserved %d; want 3, 1", participant.GetPlayerName(),
				participant.GetObservedContainerCount(), participant.GetUnobservedContainerCount())
		}
	}
	if result.Divergences != uint64(len(divergences)) || header.GetEventCount() != uint64(len(events)) {
		t.Fatalf("counts do not match records: %+v", result)
	}

	type expected struct {
		connection string
		position   *artifactsv1.BlockPosition
		start      int64
		end        artifactsv1.DivergenceEnd
	}
	want := []expected{
		{aliceConn, posA, 30, artifactsv1.DivergenceEnd_DIVERGENCE_END_REOBSERVED},
		{aliceConn, posB2, 45, artifactsv1.DivergenceEnd_DIVERGENCE_END_ACTOR_COVERAGE_END},
		{aliceConn, posA, 70, artifactsv1.DivergenceEnd_DIVERGENCE_END_ACTOR_COVERAGE_END},
		{bobConn, posA, 70, artifactsv1.DivergenceEnd_DIVERGENCE_END_WORLD_COVERAGE_END},
	}
	if len(divergences) != len(want) {
		t.Fatalf("divergences = %v, want %d", divergences, len(want))
	}
	for index, value := range divergences {
		w := want[index]
		if value.GetConnectionId() != w.connection || !proto.Equal(value.GetBlockPos(), w.position) || value.GetStartTick() != w.start || value.GetEnd() != w.end {
			t.Fatalf("divergence %d = %v, want %+v", index, value, w)
		}
	}

	// The move at 30 happened in alice's view: the sample shows bob, chest A,
	// and that bob had A open.
	seenMove := divergences[0]
	presence := seenMove.GetCoPresence()
	if presence.GetStatus() != artifactsv1.CoPresenceStatus_CO_PRESENCE_STATUS_SAMPLED || presence.GetSample().GetServerTick() != 30 ||
		presence.GetContainer() != artifactsv1.TargetVisibility_TARGET_VISIBILITY_VISIBLE {
		t.Fatalf("co-presence at 30 = %v", presence)
	}
	if len(presence.GetEntities()) != 1 || presence.GetEntities()[0].GetParticipantConnectionId() != bobConn || !presence.GetEntities()[0].GetParticipantContainerOpen() ||
		presence.GetEntities()[0].GetParticipantMenu().GetServerTick() != 20 {
		t.Fatalf("co-present entities = %v, want bob with A open since 20", presence.GetEntities())
	}
	if seenMove.GetEndTick() != 60 || seenMove.GetLastTick() != 59 || seenMove.GetObserved().GetSlots()[0].GetItemId() != diamondID || len(seenMove.GetTruth().GetSlots()) != 0 {
		t.Fatalf("divergence A = %v", seenMove)
	}

	// The deposit into B's second half: the sample at 45 shows nothing.
	secondHalf := divergences[1]
	if secondHalf.EndTick != nil || secondHalf.GetLastTick() != aliceEnd || secondHalf.GetTruth().GetSlots()[0].GetSlot() != 3 {
		t.Fatalf("divergence B2 = %v", secondHalf)
	}
	if presence := secondHalf.GetCoPresence(); presence.GetContainer() != artifactsv1.TargetVisibility_TARGET_VISIBILITY_NOT_VISIBLE || len(presence.GetEntities()) != 0 {
		t.Fatalf("co-presence at 45 = %v", presence)
	}
	// Without perception, co-presence is unknown, not absent.
	if status := divergences[3].GetCoPresence().GetStatus(); status != artifactsv1.CoPresenceStatus_CO_PRESENCE_STATUS_PERCEPTION_NOT_PROVIDED {
		t.Fatalf("bob co-presence = %v", status)
	}
	if divergences[3].GetLastTick() != worldEnd {
		t.Fatalf("bob divergence must stop at the world end, got %v", divergences[3])
	}

	// The index references sources in tick order and includes visibility
	// transitions for observed containers and player entities only.
	var previous int64
	kinds := map[artifactsv1.AlignedEventKind]int{}
	for _, event := range events {
		if event.GetSource().GetServerTick() < previous {
			t.Fatalf("events out of tick order at %v", event)
		}
		previous = event.GetSource().GetServerTick()
		kinds[event.GetKind()]++
		if event.GetKind() == artifactsv1.AlignedEventKind_ALIGNED_EVENT_KIND_WORLD_CONTAINER_SNAPSHOT && event.GetBlockPos().GetX() == 40 {
			t.Fatal("unobserved containers must not be indexed")
		}
	}
	if kinds[artifactsv1.AlignedEventKind_ALIGNED_EVENT_KIND_ACTOR_JOINED] != 2 || kinds[artifactsv1.AlignedEventKind_ALIGNED_EVENT_KIND_ACTOR_CONTAINER_CLICK] != 2 ||
		kinds[artifactsv1.AlignedEventKind_ALIGNED_EVENT_KIND_WORLD_CONTAINER_SNAPSHOT] != 6 {
		t.Fatalf("event kinds = %v", kinds)
	}
	// A, B2 undetermined, and bob become visible at 25 and stop at 40.
	if kinds[artifactsv1.AlignedEventKind_ALIGNED_EVENT_KIND_ACTOR_BLOCK_ENTITY_VISIBILITY] != 4 || kinds[artifactsv1.AlignedEventKind_ALIGNED_EVENT_KIND_ACTOR_ENTITY_VISIBILITY] != 2 {
		t.Fatalf("visibility events = %v", kinds)
	}

	// Rerunning replaces only with --overwrite, and the result is identical.
	first, err := os.ReadFile(output)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := newTestService().Align(context.Background(), options); err == nil || !strings.Contains(err.Error(), "--overwrite") {
		t.Fatalf("second run without overwrite = %v", err)
	}
	options.Overwrite = true
	options.Plays = []PlayInput{inputs.alice, inputs.bob}
	if _, err := newTestService().Align(context.Background(), options); err != nil {
		t.Fatal(err)
	}
	second, err := os.ReadFile(output)
	if err != nil {
		t.Fatal(err)
	}
	if string(first) != string(second) {
		t.Fatal("output depends on the order of --play flags")
	}
}

func TestAlignRejectsMismatchedInputs(t *testing.T) {
	root := t.TempDir()
	inputs := sallyAnne(t, root)
	other := &playWriter{t: t, uuid: bobUUID, name: "bob", connection: "11111111-2222-4333-8444-555555555555", start: 1, end: 2}
	other.click(1)
	otherMetadata, otherEvents := other.write(filepath.Join(root, "other"), "a-different-session")
	service := newTestService()
	base := Options{WorldMetadata: inputs.worldMetadata, WorldEvents: inputs.worldEvents, Output: filepath.Join(root, "out.jsonl")}

	options := base
	options.Plays = []PlayInput{inputs.alice, {Metadata: otherMetadata, Events: otherEvents}}
	if _, err := service.Align(context.Background(), options); err == nil || !strings.Contains(err.Error(), "not world session") {
		t.Fatalf("session mismatch = %v", err)
	}
	options.Plays = []PlayInput{inputs.alice, inputs.alice}
	if _, err := service.Align(context.Background(), options); err == nil || !strings.Contains(err.Error(), "more than once") {
		t.Fatalf("duplicate play = %v", err)
	}
	// Perception derived from another event stream is rejected by digest.
	options.Plays = []PlayInput{{Metadata: inputs.bob.Metadata, Events: inputs.bob.Events, Perception: inputs.alice.Perception}}
	if _, err := service.Align(context.Background(), options); err == nil || !strings.Contains(err.Error(), "identity does not match") {
		t.Fatalf("foreign perception = %v", err)
	}
	if err := os.WriteFile(filepath.Join(root, "foreign.jsonl"), []byte("{}\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	options = base
	options.Plays = []PlayInput{inputs.bob}
	options.Output, options.Overwrite = filepath.Join(root, "foreign.jsonl"), true
	if _, err := service.Align(context.Background(), options); err == nil || !strings.Contains(err.Error(), "did not write") {
		t.Fatalf("foreign output = %v", err)
	}
	if _, err := os.Stat(base.Output); !os.IsNotExist(err) {
		t.Fatalf("a rejected run must not publish output: %v", err)
	}
}

func TestParsePlay(t *testing.T) {
	play, err := ParsePlay("metadata=a/metadata.json,events=a/capture/events.jsonl,perception=a/perception.jsonl")
	if err != nil || play.Perception != "a/perception.jsonl" {
		t.Fatalf("play = %+v, %v", play, err)
	}
	for _, spec := range []string{"metadata=a", "events=b", "metadata=a,events=b,scene=c", "metadata=a,metadata=b,events=c", "metadata"} {
		if _, err := ParsePlay(spec); err == nil {
			t.Fatalf("ParsePlay(%q) succeeded", spec)
		}
	}
}
