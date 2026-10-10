package alignments

import (
	"sort"

	artifactsv1 "github.com/proj-airi/recorder-minecraft/apis/sdk/go/recorder-minecraft/artifacts/v1"
)

// worldRecord is one world stream record for a container: a snapshot or a
// removal. Contents are known only for a KNOWN snapshot.
type worldRecord struct {
	ref      *artifactsv1.RecordRef
	removed  bool
	known    bool
	size     int32
	contents contents
}

func (record *worldRecord) comparable() bool {
	return record != nil && !record.removed && record.known
}

// coverage is an inclusive server tick interval.
type coverage struct {
	first, last int64
}

// observation is what an actor last observed in one container and the
// records that produced it, oldest first.
type observation struct {
	contents contents
	sources  []*artifactsv1.RecordRef
}

func (value *observation) slots() *artifactsv1.ContainerSlots {
	return &artifactsv1.ContainerSlots{Sources: value.sources, Slots: value.contents.slots()}
}

type divergence struct {
	key          containerKey
	start, last  int64
	end          *int64
	reason       artifactsv1.DivergenceEnd
	endSource    *artifactsv1.RecordRef
	observed     *artifactsv1.ContainerSlots
	truth        *artifactsv1.ContainerSlots
	truthChanges uint32
}

// containerHistory is the outcome for one (actor, container) pair.
type containerHistory struct {
	// Applied observation updates of the container.
	observations int
	divergences  []divergence
}

// diverge walks one container's world records and one actor's observation
// updates for it in tick order, and returns the divergence intervals.
//
// State is compared at the end of each tick, after every record of that tick
// is applied: world records first, then the actor's updates in stream order.
// A click confirmation reads the world contents at the end of its own tick,
// which is why world records of the tick come first.
//
// Ticks outside both coverages are never evaluated. World records before the
// actor joined still set the truth, but an interval can only start once the
// actor has observed the container. After the earlier of the two coverage
// ends, nothing is known, so an open interval is cut there and marked with
// which coverage ended.
func diverge(key containerKey, records []worldRecord, updates []observationUpdate, actor, world coverage) containerHistory {
	first := max(actor.first, world.first)
	censor := min(actor.last, world.last)
	var history containerHistory
	var truth *worldRecord
	var seen *observation
	var open *divergence
	closeOpen := func(tick int64, reason artifactsv1.DivergenceEnd, source *artifactsv1.RecordRef) {
		end := tick
		open.last, open.end, open.reason, open.endSource = tick-1, &end, reason, source
		history.divergences = append(history.divergences, *open)
		open = nil
	}

	r, u := 0, 0
	for r < len(records) || u < len(updates) {
		tick := nextTick(records, updates, r, u)
		if tick > censor {
			break
		}
		worldChanged := false
		for ; r < len(records) && records[r].ref.GetServerTick() == tick; r++ {
			truth = &records[r]
			worldChanged = true
		}
		var observedBy *artifactsv1.RecordRef
		for ; u < len(updates) && updates[u].ref.GetServerTick() == tick; u++ {
			if next, ok := apply(seen, updates[u], truth); ok {
				seen = next
				observedBy = updates[u].ref
				history.observations++
			}
		}
		if tick < first {
			continue
		}
		if open != nil {
			switch {
			case observedBy != nil:
				closeOpen(tick, artifactsv1.DivergenceEnd_DIVERGENCE_END_REOBSERVED, observedBy)
			case truth.removed:
				closeOpen(tick, artifactsv1.DivergenceEnd_DIVERGENCE_END_CONTAINER_REMOVED, truth.ref)
			case !truth.known:
				closeOpen(tick, artifactsv1.DivergenceEnd_DIVERGENCE_END_TRUTH_UNDETERMINED, truth.ref)
			case seen.contents.equal(truth.contents, truth.size):
				closeOpen(tick, artifactsv1.DivergenceEnd_DIVERGENCE_END_TRUTH_MATCHES, truth.ref)
			case worldChanged:
				open.truthChanges++
			}
		}
		if open == nil && seen != nil && truth.comparable() && !seen.contents.equal(truth.contents, truth.size) {
			open = &divergence{
				key: key, start: tick,
				observed: seen.slots(),
				truth:    &artifactsv1.ContainerSlots{Sources: []*artifactsv1.RecordRef{truth.ref}, Slots: truth.contents.slots()},
			}
		}
	}
	if open != nil {
		open.last = censor
		open.reason = artifactsv1.DivergenceEnd_DIVERGENCE_END_ACTOR_COVERAGE_END
		if world.last < actor.last {
			open.reason = artifactsv1.DivergenceEnd_DIVERGENCE_END_WORLD_COVERAGE_END
		}
		history.divergences = append(history.divergences, *open)
	}
	return history
}

func nextTick(records []worldRecord, updates []observationUpdate, r, u int) int64 {
	switch {
	case r >= len(records):
		return updates[u].ref.GetServerTick()
	case u >= len(updates):
		return records[r].ref.GetServerTick()
	}
	return min(records[r].ref.GetServerTick(), updates[u].ref.GetServerTick())
}

// apply returns the observation after one update, or false when the update
// carries no observation of this container.
func apply(seen *observation, update observationUpdate, truth *worldRecord) (*observation, bool) {
	switch update.kind {
	case updateReplace:
		return &observation{contents: update.contents.clone(), sources: []*artifactsv1.RecordRef{update.ref}}, true
	case updateSlot:
		// A slot update without an earlier complete view says nothing about
		// the other slots, so it does not start an observation.
		if seen == nil {
			return nil, false
		}
		next := &observation{contents: seen.contents.clone(), sources: append(append([]*artifactsv1.RecordRef{}, seen.sources...), update.ref)}
		next.contents.set(update.slot, update.stack)
		return next, true
	case updateConfirm:
		if !truth.comparable() {
			return nil, false
		}
		return &observation{contents: truth.contents.clone(), sources: []*artifactsv1.RecordRef{update.ref, truth.ref}}, true
	}
	return nil, false
}

// worldTimeline groups world records by container, keeping stream order.
type worldTimeline map[containerKey][]worldRecord

func updatesByContainer(updates []observationUpdate) map[containerKey][]observationUpdate {
	grouped := map[containerKey][]observationUpdate{}
	for _, update := range updates {
		grouped[update.key] = append(grouped[update.key], update)
	}
	return grouped
}

func sortedKeys[V any](values map[containerKey]V) []containerKey {
	keys := make([]containerKey, 0, len(values))
	for key := range values {
		keys = append(keys, key)
	}
	sort.Slice(keys, func(i, j int) bool { return keys[i].less(keys[j]) })
	return keys
}
