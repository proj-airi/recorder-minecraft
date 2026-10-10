package alignments

import (
	"sort"

	artifactsv1 "github.com/proj-airi/recorder-minecraft/apis/sdk/go/recorder-minecraft/artifacts/v1"
	"google.golang.org/protobuf/proto"
)

// containerKey identifies one container block entity. Both the world stream
// and container_view sources use dimension plus BlockPosition.
type containerKey struct {
	dimension string
	x, y, z   int32
}

func keyOf(dimension string, position *artifactsv1.BlockPosition) containerKey {
	return containerKey{dimension, position.GetX(), position.GetY(), position.GetZ()}
}

func (key containerKey) position() *artifactsv1.BlockPosition {
	return &artifactsv1.BlockPosition{X: key.x, Y: key.y, Z: key.z}
}

func (key containerKey) less(other containerKey) bool {
	if key.dimension != other.dimension {
		return key.dimension < other.dimension
	}
	if key.x != other.x {
		return key.x < other.x
	}
	if key.y != other.y {
		return key.y < other.y
	}
	return key.z < other.z
}

// contents maps container slot numbers to non-empty stacks. An absent slot is
// empty, the same rule CONTENTS views and world snapshots follow.
type contents map[int32]*artifactsv1.InventorySlot

func (value contents) clone() contents {
	copied := make(contents, len(value))
	for slot, stack := range value {
		copied[slot] = stack
	}
	return copied
}

// set applies one slot update; `minecraft:air` or a zero count empties it.
func (value contents) set(slot int32, stack *artifactsv1.InventorySlot) {
	if emptyStack(stack) {
		delete(value, slot)
		return
	}
	value[slot] = stack
}

func emptyStack(stack *artifactsv1.InventorySlot) bool {
	return stack == nil || stack.GetCount() <= 0 || stack.GetItemId() == "" || stack.GetItemId() == "minecraft:air"
}

// equal compares the slots below size; a non-positive size compares all.
// Menu slots beyond the container's own size (a crafter's result slot, for
// example) are not part of the block entity and must not create differences.
func (value contents) equal(other contents, size int32) bool {
	for slot, stack := range value {
		if size > 0 && slot >= size {
			continue
		}
		if !sameStack(stack, other[slot]) {
			return false
		}
	}
	for slot := range other {
		if size > 0 && slot >= size {
			continue
		}
		if _, ok := value[slot]; !ok {
			return false
		}
	}
	return true
}

// sameStack compares item, count, and damage, plus component SNBT when both
// records carry it. Both streams use the recorder's one InventorySlot
// encoding, but component data is optional in each, and a stream without it
// cannot contradict one with it.
func sameStack(left, right *artifactsv1.InventorySlot) bool {
	if left == nil || right == nil {
		return left == right
	}
	if left.GetItemId() != right.GetItemId() || left.GetCount() != right.GetCount() || left.GetDamage() != right.GetDamage() {
		return false
	}
	if left.GetComponentsSnbt() != "" && right.GetComponentsSnbt() != "" {
		return left.GetComponentsSnbt() == right.GetComponentsSnbt()
	}
	return true
}

// slots lists the contents in slot order. components_debug is dropped: it is
// a diagnostic rendering that would dominate the output size.
func (value contents) slots() []*artifactsv1.InventorySlot {
	numbers := make([]int32, 0, len(value))
	for slot := range value {
		numbers = append(numbers, slot)
	}
	sort.Slice(numbers, func(i, j int) bool { return numbers[i] < numbers[j] })
	listed := make([]*artifactsv1.InventorySlot, 0, len(numbers))
	for _, slot := range numbers {
		stack := proto.Clone(value[slot]).(*artifactsv1.InventorySlot)
		stack.Slot = slot
		stack.ComponentsDebug = ""
		listed = append(listed, stack)
	}
	return listed
}

// menuLayout maps menu slot numbers of one block-backed menu to containers.
//
// Vanilla menus place the opened container's slots first, in container slot
// order. A double chest's CompoundContainer puts the first half's slots
// before the second half's, and the recorder writes the first half as
// source.block_pos and the second as source.secondary_block_pos.
type menuLayout struct {
	primary   containerKey
	secondary *containerKey
	// Leading menu slots that belong to the container(s), not the player.
	containerSlots int32
}

func layoutOf(view *artifactsv1.ContainerViewEvent) (menuLayout, bool) {
	source := view.GetSource()
	if source == nil || source.GetBlockPos() == nil || source.GetDimension() == "" || view.ContainerSlotCount == nil {
		return menuLayout{}, false
	}
	layout := menuLayout{primary: keyOf(source.GetDimension(), source.GetBlockPos()), containerSlots: view.GetContainerSlotCount()}
	if source.GetSecondaryBlockPos() != nil {
		secondary := keyOf(source.GetDimension(), source.GetSecondaryBlockPos())
		layout.secondary = &secondary
	}
	return layout, true
}

func (layout menuLayout) keys() []containerKey {
	if layout.secondary == nil {
		return []containerKey{layout.primary}
	}
	return []containerKey{layout.primary, *layout.secondary}
}

// locate returns the container and container slot behind a menu slot, or
// false for a player-inventory slot.
func (layout menuLayout) locate(menuSlot int32) (containerKey, int32, bool) {
	if menuSlot < 0 || menuSlot >= layout.containerSlots {
		return containerKey{}, 0, false
	}
	if layout.secondary == nil {
		return layout.primary, menuSlot, true
	}
	// Both halves of a double chest have the same size.
	half := layout.containerSlots / 2
	if menuSlot < half {
		return layout.primary, menuSlot, true
	}
	return *layout.secondary, menuSlot - half, true
}

// split turns a CONTENTS view into the complete contents of each container.
func (layout menuLayout) split(slots []*artifactsv1.InventorySlot) map[containerKey]contents {
	result := make(map[containerKey]contents, 2)
	for _, key := range layout.keys() {
		result[key] = contents{}
	}
	for _, stack := range slots {
		if key, slot, ok := layout.locate(stack.GetSlot()); ok {
			result[key].set(slot, stack)
		}
	}
	return result
}
