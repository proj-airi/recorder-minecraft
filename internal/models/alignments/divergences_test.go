package alignments

import (
	"testing"

	artifactsv1 "github.com/proj-airi/recorder-minecraft/apis/sdk/go/recorder-minecraft/artifacts/v1"
	"github.com/proj-airi/recorder-minecraft/internal/models/captures"
)

var chestA = containerKey{"minecraft:overworld", 4, -60, 0}

func stack(slot int32, item string, count int32) *artifactsv1.InventorySlot {
	return &artifactsv1.InventorySlot{Slot: slot, ItemId: item, Count: count}
}

func items(stacks ...*artifactsv1.InventorySlot) contents {
	value := contents{}
	for _, item := range stacks {
		value.set(item.GetSlot(), item)
	}
	return value
}

func worldRef(tick int64, sequence uint64) *artifactsv1.RecordRef {
	return &artifactsv1.RecordRef{Stream: artifactsv1.RecordStream_RECORD_STREAM_WORLD_EVENTS, ServerTick: tick, Sequence: sequence}
}

func actorRef(tick int64, sequence uint64) *artifactsv1.RecordRef {
	return &artifactsv1.RecordRef{Stream: artifactsv1.RecordStream_RECORD_STREAM_CAPTURE_EVENTS, ConnectionId: "actor", ServerTick: tick, Sequence: sequence}
}

func snapshot(tick int64, value contents) worldRecord {
	return worldRecord{ref: worldRef(tick, uint64(tick)), known: true, size: 27, contents: value}
}

func removal(tick int64) worldRecord {
	return worldRecord{ref: worldRef(tick, uint64(tick)), removed: true}
}

func sees(tick int64, value contents) observationUpdate {
	return observationUpdate{ref: actorRef(tick, uint64(tick)), key: chestA, kind: updateReplace, contents: value}
}

func diamond() contents { return items(stack(0, "minecraft:diamond", 1)) }

func onlyDivergence(t *testing.T, history containerHistory) divergence {
	t.Helper()
	if len(history.divergences) != 1 {
		t.Fatalf("divergences = %d, want 1: %+v", len(history.divergences), history.divergences)
	}
	return history.divergences[0]
}

func TestDivergenceStartsAtFirstChangeAfterObservationAndEndsOnReobservation(t *testing.T) {
	records := []worldRecord{snapshot(1, diamond()), snapshot(30, contents{})}
	updates := []observationUpdate{sees(10, diamond()), sees(60, contents{})}
	value := onlyDivergence(t, diverge(chestA, records, updates, coverage{5, 100}, coverage{0, 200}))
	if value.start != 30 || value.last != 59 || value.end == nil || *value.end != 60 {
		t.Fatalf("interval = %d..%d end %v, want 30..59 end 60", value.start, value.last, value.end)
	}
	if value.reason != artifactsv1.DivergenceEnd_DIVERGENCE_END_REOBSERVED || value.endSource.GetServerTick() != 60 {
		t.Fatalf("end = %v from %v", value.reason, value.endSource)
	}
	if len(value.observed.GetSlots()) != 1 || value.observed.GetSources()[0].GetServerTick() != 10 {
		t.Fatalf("observed = %v, want the diamond seen at tick 10", value.observed)
	}
	if len(value.truth.GetSlots()) != 0 || value.truth.GetSources()[0].GetServerTick() != 30 {
		t.Fatalf("truth = %v, want empty from tick 30", value.truth)
	}
}

func TestChangeBeforeFirstObservationIsNotADivergence(t *testing.T) {
	records := []worldRecord{snapshot(1, diamond()), snapshot(5, contents{})}
	updates := []observationUpdate{sees(10, contents{})}
	if history := diverge(chestA, records, updates, coverage{0, 100}, coverage{0, 100}); len(history.divergences) != 0 {
		t.Fatalf("divergences = %+v, want none", history.divergences)
	}
}

func TestMultipleChangesStayInOneInterval(t *testing.T) {
	records := []worldRecord{
		snapshot(1, diamond()),
		snapshot(20, contents{}),
		snapshot(25, items(stack(0, "minecraft:emerald", 1))),
		snapshot(28, items(stack(3, "minecraft:emerald", 2))),
	}
	updates := []observationUpdate{sees(10, diamond()), sees(50, items(stack(3, "minecraft:emerald", 2)))}
	value := onlyDivergence(t, diverge(chestA, records, updates, coverage{0, 100}, coverage{0, 100}))
	if value.start != 20 || value.truthChanges != 2 || value.reason != artifactsv1.DivergenceEnd_DIVERGENCE_END_REOBSERVED {
		t.Fatalf("divergence = %+v, want start 20 with 2 later changes", value)
	}
}

func TestReobservationThatStillDiffersStartsANewInterval(t *testing.T) {
	// The contents view at 40 is sent before the world change in the same
	// tick, so the end-of-tick world contents already differ again.
	records := []worldRecord{snapshot(1, diamond()), snapshot(20, contents{}), snapshot(40, diamond())}
	updates := []observationUpdate{sees(10, diamond()), sees(40, contents{})}
	history := diverge(chestA, records, updates, coverage{0, 100}, coverage{0, 100})
	if len(history.divergences) != 2 {
		t.Fatalf("divergences = %+v, want 2", history.divergences)
	}
	second := history.divergences[1]
	if history.divergences[0].reason != artifactsv1.DivergenceEnd_DIVERGENCE_END_REOBSERVED || second.start != 40 ||
		second.reason != artifactsv1.DivergenceEnd_DIVERGENCE_END_ACTOR_COVERAGE_END || second.end != nil || second.last != 100 {
		t.Fatalf("divergences = %+v", history.divergences)
	}
}

func TestTruthReturningToObservedContentsEndsInterval(t *testing.T) {
	records := []worldRecord{snapshot(1, diamond()), snapshot(20, contents{}), snapshot(35, diamond())}
	updates := []observationUpdate{sees(10, diamond())}
	value := onlyDivergence(t, diverge(chestA, records, updates, coverage{0, 100}, coverage{0, 100}))
	if value.reason != artifactsv1.DivergenceEnd_DIVERGENCE_END_TRUTH_MATCHES || *value.end != 35 || value.endSource.GetServerTick() != 35 {
		t.Fatalf("divergence = %+v, want TRUTH_MATCHES at 35", value)
	}
}

func TestRemovalEndsInterval(t *testing.T) {
	records := []worldRecord{snapshot(1, diamond()), snapshot(20, contents{}), removal(45)}
	updates := []observationUpdate{sees(10, diamond())}
	value := onlyDivergence(t, diverge(chestA, records, updates, coverage{0, 100}, coverage{0, 100}))
	if value.reason != artifactsv1.DivergenceEnd_DIVERGENCE_END_CONTAINER_REMOVED || value.last != 44 || *value.end != 45 {
		t.Fatalf("divergence = %+v, want CONTAINER_REMOVED at 45", value)
	}
}

func TestLootUngeneratedTruthEndsInterval(t *testing.T) {
	records := []worldRecord{snapshot(1, diamond()), snapshot(20, contents{}), {ref: worldRef(45, 45), size: 27}}
	updates := []observationUpdate{sees(10, diamond())}
	value := onlyDivergence(t, diverge(chestA, records, updates, coverage{0, 100}, coverage{0, 100}))
	if value.reason != artifactsv1.DivergenceEnd_DIVERGENCE_END_TRUTH_UNDETERMINED {
		t.Fatalf("end = %v, want TRUTH_UNDETERMINED", value.reason)
	}
}

func TestCoverageEndCutsIntervalWithoutEndTick(t *testing.T) {
	records := []worldRecord{snapshot(1, diamond()), snapshot(20, contents{})}
	updates := []observationUpdate{sees(10, diamond()), sees(90, contents{})}

	// The actor leaves at 50: the later re-observation cannot exist, and the
	// world after 50 is not this actor's concern.
	actorEnds := onlyDivergence(t, diverge(chestA, records, updates[:1], coverage{0, 50}, coverage{0, 100}))
	if actorEnds.reason != artifactsv1.DivergenceEnd_DIVERGENCE_END_ACTOR_COVERAGE_END || actorEnds.end != nil || actorEnds.last != 50 {
		t.Fatalf("divergence = %+v, want ACTOR_COVERAGE_END at 50", actorEnds)
	}
	// The world stream stops at 70, before the actor re-observes at 90:
	// nothing after 70 may be claimed, including the re-observation.
	worldEnds := onlyDivergence(t, diverge(chestA, records, updates, coverage{0, 100}, coverage{0, 70}))
	if worldEnds.reason != artifactsv1.DivergenceEnd_DIVERGENCE_END_WORLD_COVERAGE_END || worldEnds.end != nil || worldEnds.last != 70 {
		t.Fatalf("divergence = %+v, want WORLD_COVERAGE_END at 70", worldEnds)
	}
}

func TestChangeOutsideActorCoverageIsNotEvaluated(t *testing.T) {
	// A second Play of the same player has no observations of its own, so a
	// change while it is connected is not a divergence for it.
	records := []worldRecord{snapshot(1, diamond()), snapshot(150, contents{})}
	if history := diverge(chestA, records, nil, coverage{120, 200}, coverage{0, 300}); len(history.divergences) != 0 {
		t.Fatalf("divergences = %+v, want none", history.divergences)
	}
}

func TestSlotUpdatesApplyToLastObservedContents(t *testing.T) {
	records := []worldRecord{snapshot(1, diamond()), snapshot(20, items(stack(0, "minecraft:diamond", 1), stack(5, "minecraft:stick", 2)))}
	updates := []observationUpdate{
		sees(10, diamond()),
		{ref: actorRef(20, 21), key: chestA, kind: updateSlot, slot: 5, stack: stack(5, "minecraft:stick", 2)},
	}
	history := diverge(chestA, records, updates, coverage{0, 100}, coverage{0, 100})
	if len(history.divergences) != 0 || history.observations != 2 {
		t.Fatalf("history = %+v, want two observations and no divergence", history)
	}

	// A slot update emptying the slot uses minecraft:air with count 0.
	records = append(records, snapshot(30, items(stack(5, "minecraft:stick", 2))))
	updates = append(updates, observationUpdate{ref: actorRef(30, 31), key: chestA, kind: updateSlot, slot: 0, stack: stack(0, "minecraft:air", 0)})
	if history := diverge(chestA, records, updates, coverage{0, 100}, coverage{0, 100}); len(history.divergences) != 0 {
		t.Fatalf("divergences = %+v, want none after an emptying slot update", history.divergences)
	}
}

func TestSlotUpdateWithoutContentsIsNotAnObservation(t *testing.T) {
	records := []worldRecord{snapshot(1, diamond()), snapshot(20, contents{})}
	updates := []observationUpdate{{ref: actorRef(10, 10), key: chestA, kind: updateSlot, slot: 0, stack: stack(0, "minecraft:diamond", 1)}}
	if history := diverge(chestA, records, updates, coverage{0, 100}, coverage{0, 100}); history.observations != 0 || len(history.divergences) != 0 {
		t.Fatalf("history = %+v, want nothing", history)
	}
}

func TestOwnClickConfirmsWorldContentsOfItsTick(t *testing.T) {
	records := []worldRecord{snapshot(1, diamond()), snapshot(30, contents{})}
	updates := []observationUpdate{sees(10, diamond()), {ref: actorRef(30, 30), key: chestA, kind: updateConfirm}}
	history := diverge(chestA, records, updates, coverage{0, 100}, coverage{0, 100})
	if len(history.divergences) != 0 {
		t.Fatalf("divergences = %+v, want none: the actor made the change", history.divergences)
	}
}

func TestComponentsCompareOnlyWhenBothSidesCarryThem(t *testing.T) {
	withComponents := stack(0, "minecraft:diamond", 1)
	withComponents.ComponentsSnbt = `{count:1,id:"minecraft:diamond"}`
	renamed := stack(0, "minecraft:diamond", 1)
	renamed.ComponentsSnbt = `{components:{"minecraft:custom_name":"x"},count:1,id:"minecraft:diamond"}`
	if !sameStack(withComponents, stack(0, "minecraft:diamond", 1)) {
		t.Fatal("a stack without components must not contradict one with components")
	}
	if sameStack(withComponents, renamed) {
		t.Fatal("different components must differ")
	}
	if !items(stack(30, "minecraft:stick", 1)).equal(contents{}, 27) {
		t.Fatal("slots beyond the container size must not be compared")
	}
}

func containerView(kind artifactsv1.ContainerViewKind, id int32, slots ...*artifactsv1.InventorySlot) *artifactsv1.CaptureEvent {
	count := int32(54)
	return &artifactsv1.CaptureEvent{Record: &artifactsv1.CaptureEvent_ContainerView{ContainerView: &artifactsv1.ContainerViewEvent{
		Kind: kind, ContainerId: id, ContainerSlotCount: &count, Slots: slots,
		Source: &artifactsv1.ContainerViewSource{
			Dimension: "minecraft:overworld", BlockPos: &artifactsv1.BlockPosition{X: 4, Y: -60, Z: 0},
			BlockEntityType: "minecraft:chest", SecondaryBlockPos: &artifactsv1.BlockPosition{X: 4, Y: -60, Z: 1},
		},
	}}}
}

func TestDoubleChestViewsSplitIntoBothHalves(t *testing.T) {
	observer := newObserver("actor")
	feed := func(tick int64, sequence uint64, message *artifactsv1.CaptureEvent) {
		observer.observe(captures.Event{Message: message, ServerTick: tick, Sequence: sequence})
	}
	feed(10, 1, containerView(artifactsv1.ContainerViewKind_CONTAINER_VIEW_KIND_OPENED, 3))
	// Menu slot 2 is the first half's slot 2, menu slot 30 the second half's
	// slot 3, and menu slot 60 the player's inventory.
	feed(10, 2, containerView(artifactsv1.ContainerViewKind_CONTAINER_VIEW_KIND_CONTENTS, 3,
		stack(2, "minecraft:diamond", 1), stack(30, "minecraft:emerald", 4), stack(60, "minecraft:dirt", 64)))
	feed(12, 3, containerView(artifactsv1.ContainerViewKind_CONTAINER_VIEW_KIND_SLOT, 3, stack(31, "minecraft:stick", 1)))
	feed(15, 4, &artifactsv1.CaptureEvent{Record: &artifactsv1.CaptureEvent_PacketApply{PacketApply: &artifactsv1.PacketApplyEvent{
		Packet: &artifactsv1.Packet{Identity: &artifactsv1.PacketIdentity{PacketType: containerClickType}}}}})
	feed(20, 5, containerView(artifactsv1.ContainerViewKind_CONTAINER_VIEW_KIND_CLOSED, 3))
	timeline := observer.finish(100)

	secondary := containerKey{"minecraft:overworld", 4, -60, 1}
	updates := updatesByContainer(timeline.updates)
	if len(updates[chestA]) != 2 || len(updates[secondary]) != 3 {
		t.Fatalf("updates = %d primary, %d secondary; want contents+click and contents+slot+click", len(updates[chestA]), len(updates[secondary]))
	}
	primaryContents := updates[chestA][0].contents
	if len(primaryContents) != 1 || primaryContents[2].GetItemId() != "minecraft:diamond" {
		t.Fatalf("primary contents = %v", primaryContents)
	}
	secondaryContents := updates[secondary][0].contents
	if len(secondaryContents) != 1 || secondaryContents[3].GetItemId() != "minecraft:emerald" {
		t.Fatalf("secondary contents = %v", secondaryContents)
	}
	if slot := updates[secondary][1]; slot.kind != updateSlot || slot.slot != 4 {
		t.Fatalf("slot update = %+v, want secondary slot 4", slot)
	}
	if len(timeline.menus) != 1 || !timeline.menus[0].openAt(15, secondary) || timeline.menus[0].openAt(20, chestA) {
		t.Fatalf("menus = %+v, want one menu open over 10..19", timeline.menus)
	}
}

func TestClickWithoutContainerMenuIsIgnored(t *testing.T) {
	observer := newObserver("actor")
	observer.observe(captures.Event{ServerTick: 5, Sequence: 1, Message: &artifactsv1.CaptureEvent{Record: &artifactsv1.CaptureEvent_PacketApply{
		PacketApply: &artifactsv1.PacketApplyEvent{Packet: &artifactsv1.Packet{Identity: &artifactsv1.PacketIdentity{PacketType: containerClickType}}}}}})
	if timeline := observer.finish(10); len(timeline.updates) != 0 || len(timeline.events) != 0 {
		t.Fatalf("timeline = %+v, want nothing", timeline)
	}
}
