package alignments

import (
	"math"
	"strings"

	artifactsv1 "github.com/proj-airi/recorder-minecraft/apis/sdk/go/recorder-minecraft/artifacts/v1"
	"github.com/proj-airi/recorder-minecraft/internal/models/captures"
)

// containerClickType is the serverbound packet type the recorder writes for a
// container click. Its handler acts on the player's open menu.
const containerClickType = "serverbound/minecraft:container_click"

type updateKind int

const (
	// A CONTENTS view: the complete contents of every container in the menu.
	updateReplace updateKind = iota
	// A SLOT view: one container slot.
	updateSlot
	// An applied own click: the observation becomes the world contents at the
	// end of the click's tick (see clickConfirmsAssumption).
	updateConfirm
)

// observationUpdate is one change to what an actor observed in one container.
type observationUpdate struct {
	ref      *artifactsv1.RecordRef
	key      containerKey
	kind     updateKind
	contents contents
	slot     int32
	stack    *artifactsv1.InventorySlot
}

// menuInterval is one block-backed menu the actor had open, from its OPENED
// view up to (not including) the tick of its close.
type menuInterval struct {
	opened    *artifactsv1.RecordRef
	keys      []containerKey
	openTick  int64
	closeTick int64
}

func (menu menuInterval) openAt(tick int64, key containerKey) bool {
	if tick < menu.openTick || tick >= menu.closeTick {
		return false
	}
	for _, candidate := range menu.keys {
		if candidate == key {
			return true
		}
	}
	return false
}

// actorTimeline is everything one Play contributes, read from its event stream.
type actorTimeline struct {
	connectionID string
	updates      []observationUpdate
	menus        []menuInterval
	// Source-linked container views and attributed clicks, for the index.
	events []*artifactsv1.AlignedEvent
}

// observer folds one Play's capture events into an actorTimeline.
type observer struct {
	timeline *actorTimeline
	open     map[int32]*openMenu
	// Container id of the latest OPENED view not yet closed. A container
	// click acts on the player's current menu.
	current int32
}

type openMenu struct {
	layout   menuLayout
	interval int
}

func newObserver(connectionID string) *observer {
	return &observer{timeline: &actorTimeline{connectionID: connectionID}, open: map[int32]*openMenu{}, current: -1}
}

func (o *observer) ref(event captures.Event) *artifactsv1.RecordRef {
	return &artifactsv1.RecordRef{
		Stream: artifactsv1.RecordStream_RECORD_STREAM_CAPTURE_EVENTS, ConnectionId: o.timeline.connectionID,
		ServerTick: event.ServerTick, Sequence: event.Sequence,
	}
}

func (o *observer) observe(event captures.Event) {
	if view := event.Message.GetContainerView(); view != nil {
		o.view(event, view)
		return
	}
	apply := event.Message.GetPacketApply()
	if apply == nil || !strings.HasSuffix(apply.GetPacket().GetIdentity().GetPacketType(), containerClickType) {
		return
	}
	menu := o.open[o.current]
	if menu == nil {
		return
	}
	ref := o.ref(event)
	for _, key := range menu.layout.keys() {
		o.timeline.updates = append(o.timeline.updates, observationUpdate{ref: ref, key: key, kind: updateConfirm})
	}
	o.timeline.events = append(o.timeline.events, containerEvent(artifactsv1.AlignedEventKind_ALIGNED_EVENT_KIND_ACTOR_CONTAINER_CLICK, ref, menu.layout.primary, 0))
}

func (o *observer) view(event captures.Event, view *artifactsv1.ContainerViewEvent) {
	id := view.GetContainerId()
	if view.GetKind() == artifactsv1.ContainerViewKind_CONTAINER_VIEW_KIND_CLOSED {
		if menu := o.open[id]; menu != nil {
			o.timeline.menus[menu.interval].closeTick = event.ServerTick
			delete(o.open, id)
		}
		if o.current == id {
			o.current = -1
		}
	}
	layout, linked := layoutOf(view)
	if !linked && view.GetKind() == artifactsv1.ContainerViewKind_CONTAINER_VIEW_KIND_OPENED {
		// A menu without a container block (a crafting table, an ender chest)
		// still replaces the current menu, so later clicks are not attributed
		// to a container that is no longer open.
		o.closeAll(event.ServerTick)
		o.current = id
	}
	if !linked || view.GetKind() == artifactsv1.ContainerViewKind_CONTAINER_VIEW_KIND_CARRIED {
		return
	}
	ref := o.ref(event)
	o.timeline.events = append(o.timeline.events, containerEvent(artifactsv1.AlignedEventKind_ALIGNED_EVENT_KIND_ACTOR_CONTAINER_VIEW, ref, layout.primary, view.GetKind()))
	switch view.GetKind() {
	case artifactsv1.ContainerViewKind_CONTAINER_VIEW_KIND_OPENED:
		o.openMenu(id, layout, ref)
	case artifactsv1.ContainerViewKind_CONTAINER_VIEW_KIND_CONTENTS:
		if o.open[id] == nil {
			o.openMenu(id, layout, ref)
		}
		split := layout.split(view.GetSlots())
		for _, key := range layout.keys() {
			o.timeline.updates = append(o.timeline.updates, observationUpdate{ref: ref, key: key, kind: updateReplace, contents: split[key]})
		}
	case artifactsv1.ContainerViewKind_CONTAINER_VIEW_KIND_SLOT:
		for _, stack := range view.GetSlots() {
			if key, slot, ok := layout.locate(stack.GetSlot()); ok {
				o.timeline.updates = append(o.timeline.updates, observationUpdate{ref: ref, key: key, kind: updateSlot, slot: slot, stack: stack})
			}
		}
	}
}

// openMenu starts a menu interval. A new menu replaces the previous one on
// the client, so an unclosed earlier menu ends here.
func (o *observer) openMenu(id int32, layout menuLayout, ref *artifactsv1.RecordRef) {
	o.closeAll(ref.GetServerTick())
	o.timeline.menus = append(o.timeline.menus, menuInterval{opened: ref, keys: layout.keys(), openTick: ref.GetServerTick(), closeTick: math.MaxInt64})
	o.open[id] = &openMenu{layout: layout, interval: len(o.timeline.menus) - 1}
	o.current = id
}

func (o *observer) closeAll(tick int64) {
	for id, menu := range o.open {
		o.timeline.menus[menu.interval].closeTick = tick
		delete(o.open, id)
	}
}

// finish closes menus still open when the Play ended: the menu cannot outlive
// the connection.
func (o *observer) finish(endTick int64) *actorTimeline {
	for _, menu := range o.open {
		o.timeline.menus[menu.interval].closeTick = endTick + 1
	}
	return o.timeline
}

func containerEvent(kind artifactsv1.AlignedEventKind, ref *artifactsv1.RecordRef, key containerKey, viewKind artifactsv1.ContainerViewKind) *artifactsv1.AlignedEvent {
	return &artifactsv1.AlignedEvent{Kind: kind, Source: ref, Dimension: key.dimension, BlockPos: key.position(), ContainerViewKind: viewKind}
}
