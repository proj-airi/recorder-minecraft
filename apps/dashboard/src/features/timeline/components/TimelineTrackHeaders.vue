<script setup lang="ts">
import type { EpisodeSession } from '../domain'
import type { EmptyDataTracks, TimelineLayout, TimelineLayoutRow } from '../layout'

import { defaultTimelineRendererTheme } from '@proj-airi/canvas-timeline-renderer'
import { useElementSize } from '@vueuse/core'
import { computed, onBeforeUnmount, shallowRef, toRef, useTemplateRef } from 'vue'

import { rowAtOffset } from '../layout'

const props = defineProps<{
  alignmentLabel: string
  editable: boolean
  layout: TimelineLayout
  scrollContainer: HTMLDivElement | null
  scrollTop: number
  /** Player lane or session header selected in the session; see `TimelineSession.selectedTrackId`. */
  selectedTrackId: null | string
  sessions: readonly EpisodeSession[]
  /** Identity color of each player lane, shared with the other panels. */
  trackColors: ReadonlyMap<string, string>
}>()

const emit = defineEmits<{
  reload: [trackId: string]
  reorder: [sourceTrackId: string, targetTrackId: string]
  select: [trackId: string]
  toggleEmpty: [targetKey: string]
  toggleGroup: [groupId: string]
}>()

const BUFFER_PX = 192
const rulerHeight = defaultTimelineRendererTheme.metrics.rulerHeight
const rowList = useTemplateRef<HTMLDivElement>('rowList')
const { height: viewportHeight } = useElementSize(toRef(props, 'scrollContainer'))
const sessionsByKey = computed(() => new Map(props.sessions.map(session => [session.key, session])))
const multipleSessions = computed(() => props.sessions.length > 1)

// NOTICE: Rows have different heights (session header, world slot, player lane, data track) and
// the canvas uses the exact same offsets, so the list is virtualized from known offsets instead of
// measured sizes. Only rows that intersect the viewport plus a buffer are mounted.
const visibleRows = computed(() => {
  const rows = props.layout.rows
  if (rows.length === 0)
    return []
  const top = Math.max(0, props.scrollTop - rulerHeight - BUFFER_PX)
  const bottom = props.scrollTop + Math.max(viewportHeight.value, 480) + BUFFER_PX
  const first = rowAtOffset(props.layout, top) ?? rows[0]!
  const result: TimelineLayoutRow[] = []
  for (let index = rows.indexOf(first); index < rows.length; index += 1) {
    const row = rows[index]!
    if (row.top > bottom)
      break
    result.push(row)
  }
  return result
})

// Left padding per row role, in px. A chevron column is 16px, so a data track indented by the
// player's indent plus that column puts its icon exactly under the player's icon.
const ROW_INDENT_PX = { data: 4 + 14 + 16, primary: 4 + 14, session: 4, world: 4 + 14 } as const

type ExpandableRow = Extract<TimelineLayoutRow, { role: 'primary' | 'session' | 'world' }>

function indentOf(row: TimelineLayoutRow): number {
  return ROW_INDENT_PX[row.role]
}

function isExpandable(row: TimelineLayoutRow): row is ExpandableRow {
  return row.role === 'session' || ((row.role === 'primary' || row.role === 'world') && row.dataTrackCount > 0)
}

function toggleLabel(row: ExpandableRow): string {
  if (row.role === 'session')
    return `${row.collapsed ? 'Expand' : 'Collapse'} ${row.label}`
  const name = row.role === 'world' ? 'the world' : row.label
  return `${row.collapsed ? 'Show' : 'Hide'} data tracks of ${name}`
}

function emptyToggleLabel(empty: EmptyDataTracks): string {
  return `${empty.shown ? 'Hide' : 'Show'} ${empty.count} data ${empty.count === 1 ? 'track' : 'tracks'} with no items`
}

function rowIcon(row: TimelineLayoutRow): string {
  switch (row.role) {
    case 'session':
      return 'i-mingcute-server-line'
    case 'world':
      return 'i-mingcute-earth-2-line'
    case 'primary':
      return row.kind === 'video' ? 'i-mingcute-video-line' : 'i-mingcute-user-3-line'
    case 'data':
      return row.dataTrack.icon ?? 'i-mingcute-list-check-line'
  }
}

function rowDetail(row: Extract<TimelineLayoutRow, { role: 'primary' | 'world' }>): string {
  if (row.role === 'world')
    return sessionsByKey.value.get(row.sessionKey)?.world ? 'World session' : 'Session span'
  return `${row.playCount} ${row.playCount === 1 ? 'Play' : 'Plays'} · ${row.kind}`
}

const dragSourceId = shallowRef<null | string>(null)
const dropTargetId = shallowRef<null | string>(null)

/** A pointer that went down on a selectable row: a click selects it, a drag reorders it. */
interface RowPointer {
  dragging: boolean
  /** Pending touch long-press that turns the press into a drag. */
  longPressTimer: number
  pointerId: number
  pointerType: string
  rowId: string
  startX: number
  startY: number
}

// NOTICE: A press only becomes a drag after this distance, so a slightly shaky click still
// selects instead of starting a reorder that drops on the same row.
const DRAG_THRESHOLD_PX = 4
// NOTICE: On touch, a swipe over the rows must keep scrolling the list, so a drag starts only after
// the finger rests on a row (the long-press convention of iOS and Android lists). Moving further
// than the slop before then is a scroll, and the press is dropped.
const LONG_PRESS_MS = 300
const LONG_PRESS_SLOP_PX = 8
let rowPointer: null | RowPointer = null

/** Ghost position and insertion line while a row is dragged, in list pixels. */
interface DragView {
  ghostTop: number
  lineTop: null | number
}

const dragView = shallowRef<DragView | null>(null)
const dragSourceRow = computed(() => dragSourceId.value ? props.layout.rows.find(row => row.id === dragSourceId.value) ?? null : null)
let autoScrollFrame = 0
let grabOffset = 0
let lastClientY = 0

onBeforeUnmount(endDrag)

// NOTICE: Not a type predicate. In the template, vue-tsc carries the narrowing of a `v-if` /
// `v-else` pair into the later sibling branches, which then lose the 'primary' role.
function isSelectable(row: TimelineLayoutRow): boolean {
  return row.role === 'primary' || row.role === 'session'
}

/** True when the row is the selected track or belongs to it (its data tracks, or its session). */
function inSelection(row: TimelineLayoutRow): boolean {
  const selected = props.selectedTrackId
  if (!selected)
    return false
  if (row.id === selected)
    return true
  const selectedRow = props.layout.rows.find(candidate => candidate.id === selected)
  if (selectedRow?.role === 'session')
    return row.sessionKey === selectedRow.sessionKey
  return row.role === 'data' && row.dataTrack.target.scope === 'player' && row.dataTrack.target.key === selected
}

function sessionBadge(sessionKey: string): string | undefined {
  if (!multipleSessions.value)
    return undefined
  const placement = sessionsByKey.value.get(sessionKey)?.placement
  return placement === 'wall-clock' ? 'wall clock' : placement === 'sequential' ? 'appended' : 'origin'
}

function sessionDetail(row: Extract<TimelineLayoutRow, { role: 'session' }>): string {
  const session = sessionsByKey.value.get(row.sessionKey)
  const players = `${row.playerCount} ${row.playerCount === 1 ? 'player' : 'players'}`
  return [players, session?.serverName, 'server ticks'].filter(Boolean).join(' · ')
}

function canDrop(sourceId: string, target: TimelineLayoutRow): boolean {
  const source = props.layout.rows.find(row => row.id === sourceId)
  if (!source || source.id === target.id)
    return false
  if (source.sessionKey === target.sessionKey)
    return source.role === 'primary' && target.role === 'primary'
  return source.role === 'session'
}

function onRowPointerDown(event: PointerEvent, row: TimelineLayoutRow): void {
  if (event.button !== 0 || !isSelectable(row))
    return
  // Controls inside the row (disclosure, empty toggle, retry) keep their own click; the type icon
  // is the row's keyboard handle and belongs to the row gesture.
  const button = (event.target as Element).closest('button')
  if (button && !button.hasAttribute('data-track-handle'))
    return
  // Mouse and pen: keep focus where it was and suppress text selection. Touch keeps its default so
  // the list can still scroll.
  if (event.pointerType !== 'touch')
    event.preventDefault()
  ;(event.currentTarget as HTMLElement).setPointerCapture?.(event.pointerId)
  const pointer: RowPointer = { dragging: false, longPressTimer: 0, pointerId: event.pointerId, pointerType: event.pointerType, rowId: row.id, startX: event.clientX, startY: event.clientY }
  rowPointer = pointer
  if (event.pointerType === 'touch' && props.editable) {
    pointer.longPressTimer = window.setTimeout(() => {
      const list = rowList.value
      if (rowPointer !== pointer || !list)
        return
      lastClientY = pointer.startY
      startDrag(pointer, list)
      updateDrag()
    }, LONG_PRESS_MS)
  }
}

function onRowPointerMove(event: PointerEvent): void {
  const pointer = rowPointer
  const list = rowList.value
  if (!pointer || pointer.pointerId !== event.pointerId || !list)
    return
  lastClientY = event.clientY
  if (!pointer.dragging) {
    const moved = Math.hypot(event.clientX - pointer.startX, event.clientY - pointer.startY)
    if (pointer.pointerType === 'touch') {
      // Moving before the long press completes is a scroll, not a selection or a drag.
      if (moved > LONG_PRESS_SLOP_PX)
        endDrag()
      return
    }
    if (!props.editable || moved < DRAG_THRESHOLD_PX)
      return
    startDrag(pointer, list)
  }
  updateDrag()
}

// NOTICE: Once a touch drag has started, the browser would still begin panning the list on the
// next move and cancel the pointer. Pointer events cannot stop that; a non-passive touchmove
// listener that prevents the default can.
function onListTouchMove(event: TouchEvent): void {
  if (rowPointer?.dragging)
    event.preventDefault()
}

function onListContextMenu(event: Event): void {
  // A touch long press would otherwise open the browser's context menu over the dragged row.
  if (rowPointer)
    event.preventDefault()
}

function onRowPointerUp(event: PointerEvent): void {
  const pointer = rowPointer
  if (!pointer || pointer.pointerId !== event.pointerId)
    return
  const targetId = dropTargetId.value
  endDrag()
  // A dragged row is selected too, so the lane the user just moved stays highlighted.
  emit('select', pointer.rowId)
  if (pointer.dragging && targetId)
    emit('reorder', pointer.rowId, targetId)
}

function onRowPointerCancel(): void {
  endDrag()
}

function startDrag(pointer: RowPointer, list: HTMLElement): void {
  pointer.dragging = true
  dragSourceId.value = pointer.rowId
  dropTargetId.value = null
  // The ghost keeps the point where the row was grabbed under the pointer instead of jumping to
  // center on it.
  const source = props.layout.rows.find(row => row.id === pointer.rowId)
  grabOffset = source ? pointer.startY - list.getBoundingClientRect().top - source.top : 0
  document.documentElement.classList.add('timeline-row-dragging')
  autoScrollFrame = requestAnimationFrame(autoScroll)
}

function endDrag(): void {
  if (rowPointer)
    window.clearTimeout(rowPointer.longPressTimer)
  rowPointer = null
  dragSourceId.value = null
  dropTargetId.value = null
  dragView.value = null
  cancelAnimationFrame(autoScrollFrame)
  autoScrollFrame = 0
  document.documentElement.classList.remove('timeline-row-dragging')
}

/** Moves the ghost to the pointer and resolves where the row would land. */
function updateDrag(): void {
  const pointer = rowPointer
  const list = rowList.value
  const source = pointer?.dragging ? props.layout.rows.find(row => row.id === pointer.rowId) : undefined
  if (!list || !source)
    return
  const ghostTop = Math.min(Math.max(lastClientY - list.getBoundingClientRect().top - grabOffset, 0), Math.max(0, props.layout.totalHeight - source.height))
  const hovered = rowAtClientY(list, lastClientY)
  const target = hovered ? dropRowFor(source, hovered) : null
  dropTargetId.value = target?.id ?? null
  dragView.value = { ghostTop, lineTop: target ? dropLineTop(source, target) : null }
}

/**
 * The reorder target for a hovered row. Hovering anywhere in a lane's group (its data tracks
 * included) means that lane, and anywhere in a session means that session, so the insertion line
 * does not flicker off while the pointer crosses data rows.
 */
function dropRowFor(source: TimelineLayoutRow, hovered: TimelineLayoutRow): null | TimelineLayoutRow {
  let target: TimelineLayoutRow | undefined = hovered
  if (source.role === 'session') {
    target = props.layout.rows.find(row => row.role === 'session' && row.sessionKey === hovered.sessionKey)
  }
  else if (hovered.role === 'data' && hovered.dataTrack.target.scope === 'player') {
    const laneId = hovered.dataTrack.target.key
    target = props.layout.rows.find(row => row.id === laneId)
  }
  return target && canDrop(source.id, target) ? target : null
}

/**
 * Where the dragged row would be inserted, in list pixels. Moving up inserts before the target;
 * moving down inserts after the target's whole group (a lane with its data tracks, or a session
 * with everything under it), matching `reorderLaneGroups`.
 */
function dropLineTop(source: TimelineLayoutRow, target: TimelineLayoutRow): number {
  const rows = props.layout.rows
  const targetIndex = rows.indexOf(target)
  if (rows.indexOf(source) > targetIndex)
    return target.top
  let last = target
  for (let index = targetIndex + 1; index < rows.length; index += 1) {
    const row = rows[index]!
    if (row.role === 'session' || (target.role === 'primary' && row.role === 'primary'))
      break
    last = row
  }
  return last.top + last.height
}

// NOTICE: The track panel is short, so a lane often has to travel past the visible rows. Near an
// edge the list scrolls every frame, faster the closer the pointer is; pointer events alone would
// stop scrolling as soon as the pointer stops moving.
const AUTO_SCROLL_EDGE_PX = 28
const AUTO_SCROLL_MAX_STEP_PX = 14

function autoScroll(): void {
  autoScrollFrame = requestAnimationFrame(autoScroll)
  const container = props.scrollContainer
  if (!container)
    return
  const bounds = container.getBoundingClientRect()
  // The alignment header is sticky over the top of the list, so the top edge starts below it.
  const top = bounds.top + rulerHeight
  let step = 0
  if (lastClientY < top + AUTO_SCROLL_EDGE_PX)
    step = -Math.ceil(AUTO_SCROLL_MAX_STEP_PX * (1 - Math.max(0, lastClientY - top) / AUTO_SCROLL_EDGE_PX))
  else if (lastClientY > bounds.bottom - AUTO_SCROLL_EDGE_PX)
    step = Math.ceil(AUTO_SCROLL_MAX_STEP_PX * (1 - Math.max(0, bounds.bottom - lastClientY) / AUTO_SCROLL_EDGE_PX))
  if (step === 0)
    return
  const before = container.scrollTop
  container.scrollTop += step
  if (container.scrollTop !== before)
    updateDrag()
}

/** The mounted row under a viewport y, hit-tested on the rendered rows rather than layout math. */
function rowAtClientY(list: HTMLElement, clientY: number): null | TimelineLayoutRow {
  for (const element of list.querySelectorAll<HTMLElement>('[data-track-id]')) {
    const bounds = element.getBoundingClientRect()
    if (clientY >= bounds.top && clientY < bounds.bottom)
      return props.layout.rows.find(row => row.id === element.dataset.trackId) ?? null
  }
  return null
}

/** Moves a row one step up or down among the rows it can be reordered with (Alt+↑/↓). */
function onHandleKeydown(event: KeyboardEvent, row: TimelineLayoutRow): void {
  if (!event.altKey || (event.key !== 'ArrowUp' && event.key !== 'ArrowDown'))
    return
  event.preventDefault()
  if (!props.editable)
    return
  const rows = props.layout.rows
  const step = event.key === 'ArrowUp' ? -1 : 1
  for (let index = rows.indexOf(row) + step; index >= 0 && index < rows.length; index += step) {
    const candidate = rows[index]!
    if (canDrop(row.id, candidate)) {
      emit('select', row.id)
      emit('reorder', row.id, candidate.id)
      return
    }
  }
}

function handleLabel(row: TimelineLayoutRow): string {
  return props.editable ? `Reorder ${row.label}` : `Select ${row.label}`
}

function rowTitle(row: TimelineLayoutRow): string | undefined {
  if (!isSelectable(row))
    return undefined
  return props.editable ? `Click to select ${row.label}; drag to reorder` : `Select ${row.label}`
}
</script>

<template>
  <aside class="relative border-r border-[var(--dashboard-border-color)] bg-[#27272A]">
    <div
      class="sticky top-0 z-2 flex items-center border-b border-[var(--dashboard-border-color)] bg-#27272A px-3 text-[0.625rem] text-neutral-400 tracking-[0.08em] uppercase"
      data-testid="timeline-alignment"
      :style="{ height: `${rulerHeight}px` }"
      :title="alignmentLabel"
    >
      <span class="truncate">{{ alignmentLabel }}</span>
    </div>

    <div
      ref="rowList"
      aria-label="Timeline rows"
      class="relative"
      :data-row-count="layout.rows.length"
      role="list"
      :style="{ height: `${layout.totalHeight}px` }"
      @contextmenu="onListContextMenu"
      @touchmove="onListTouchMove"
    >
      <div
        v-for="row in visibleRows"
        :key="row.id"
        class="group absolute left-0 right-0 flex items-center border-b border-[var(--dashboard-border-color)] pr-2 text-neutral-100"
        :aria-current="row.id === selectedTrackId ? 'true' : undefined"
        :class="[
          row.id === selectedTrackId ? 'bg-#2c2c31' : inSelection(row) ? (row.role === 'data' ? 'bg-#1c1c20' : 'bg-#232327') : row.role === 'session' ? 'bg-#1f1f23' : row.role === 'data' ? 'bg-#141416' : 'bg-#171717',
          isSelectable(row) ? (editable ? 'cursor-grab select-none active:cursor-grabbing [-webkit-touch-callout:none]' : 'cursor-pointer') : '',
          dragSourceId === row.id ? 'opacity-35' : '',
        ]"
        :data-session-key="row.sessionKey"
        :data-track-id="row.id"
        :data-track-kind="row.kind"
        :data-track-role="row.role"
        role="listitem"
        :title="rowTitle(row)"
        :style="{
          height: `${row.height}px`,
          top: `${row.top}px`,
          // The selected track carries its identity color on its edge, as its Inputs card does.
          boxShadow: row.id === selectedTrackId ? `inset 2px 0 0 ${trackColors.get(row.id) ?? '#a1a1aa'}` : undefined,
        }"
        @pointercancel="onRowPointerCancel"
        @pointerdown="onRowPointerDown($event, row)"
        @pointermove="onRowPointerMove"
        @pointerup="onRowPointerUp"
      >
        <!-- Hierarchy indent. Data tracks have no children, so they skip the chevron column and
             their icon lines up under the parent row's icon instead of a further level in. -->
        <span aria-hidden="true" class="shrink-0" :style="{ width: `${indentOf(row)}px` }" />

        <!-- Disclosure chevron; always on the left of expandable rows. -->
        <template v-if="row.role !== 'data'">
          <button
            v-if="isExpandable(row)"
            :aria-expanded="!row.collapsed"
            :aria-label="toggleLabel(row)"
            class="h-5 w-4 flex shrink-0 items-center justify-center border-0 rounded bg-transparent p-0 text-neutral-500 hover:bg-white/8 hover:text-neutral-100"
            :title="toggleLabel(row)"
            type="button"
            @click="emit('toggleGroup', row.groupId)"
          >
            <span aria-hidden="true" class="text-sm" :class="row.collapsed ? 'i-mingcute-right-line' : 'i-mingcute-down-line'" />
          </button>
          <span v-else aria-hidden="true" class="w-4 shrink-0" />
        </template>

        <!-- What the row is. On selectable rows the icon is the keyboard handle: Enter or Space
             selects the track, Alt+↑/↓ reorders it, and the grip shows on hover or focus. The whole
             row also selects on click and reorders on drag. -->
        <button
          v-if="isSelectable(row)"
          :aria-label="handleLabel(row)"
          :aria-pressed="row.id === selectedTrackId"
          :aria-keyshortcuts="editable ? 'Alt+ArrowUp Alt+ArrowDown' : undefined"
          class="track-handle ml-1 mr-2 h-5 w-5 flex shrink-0 items-center justify-center border-0 rounded bg-transparent p-0 text-neutral-500 focus-visible:text-neutral-100 focus-visible:outline-1 focus-visible:outline-white/60 focus-visible:outline"
          data-track-handle
          :title="editable ? `Alt+↑/↓ to reorder ${row.label}` : undefined"
          type="button"
          @click="emit('select', row.id)"
          @keydown="onHandleKeydown($event, row)"
        >
          <!-- The type icon stays visible; the whole row is the mouse drag target. The grip appears
               only for keyboard focus, where Alt+↑/↓ is the way to move the row. -->
          <span aria-hidden="true" class="track-handle-icon" :class="[rowIcon(row), row.role === 'primary' ? 'text-base' : 'text-sm']" />
          <span v-if="editable" aria-hidden="true" class="track-handle-grip i-mingcute-dots-line hidden rotate-90 text-sm" />
        </button>
        <span v-else aria-hidden="true" class="ml-1 mr-2 h-5 w-5 flex shrink-0 items-center justify-center text-neutral-500">
          <span :class="[rowIcon(row), row.role === 'primary' ? 'text-base' : 'text-sm']" />
        </span>

        <!-- Column 4: label, then right-aligned meta. -->
        <template v-if="row.role === 'session'">
          <p class="m-0 min-w-0 flex-1 truncate text-xs text-neutral-200 font-medium" :title="sessionDetail(row)">
            {{ row.label }}
            <span class="ml-1 text-[10px] text-neutral-500 font-normal">{{ sessionDetail(row) }}</span>
          </p>
          <span
            v-if="sessionBadge(row.sessionKey)"
            class="ml-1 shrink-0 rounded bg-amber-500/15 px-1 text-[9px] text-amber-200 tracking-wide uppercase"
            :title="sessionBadge(row.sessionKey) === 'origin' ? 'Episode origin; other sessions are aligned to it by wall clock' : 'Positioned against the other sessions by wall clock; Plays inside are aligned by server tick'"
          >{{ sessionBadge(row.sessionKey) }}</span>
        </template>

        <template v-else-if="row.role === 'world' || row.role === 'primary'">
          <span v-if="row.role === 'primary' && trackColors.has(row.id)" aria-hidden="true" class="mr-1.5 h-2 w-2 shrink-0 rounded-full" :style="{ backgroundColor: trackColors.get(row.id) }" />
          <div class="min-w-0 flex-1">
            <p class="m-0 truncate" :class="row.role === 'primary' ? 'text-sm text-neutral-100 font-medium' : 'text-xs text-neutral-300'" :title="row.role === 'primary' ? row.track.playerUuid ?? row.label : undefined">
              {{ row.role === 'world' ? 'World' : row.label }}
            </p>
            <p class="m-0 mt-0.5 truncate text-[10px] text-neutral-500">
              {{ rowDetail(row) }}
            </p>
          </div>
          <span v-if="row.collapsed && row.dataTrackCount > 0" class="ml-1 shrink-0 text-[10px] text-neutral-500 tabular-nums" :title="`${row.dataTrackCount} data tracks hidden`">
            {{ row.dataTrackCount }} tracks
          </span>
          <button
            v-else-if="row.emptyDataTracks.count > 0"
            :aria-label="emptyToggleLabel(row.emptyDataTracks)"
            :aria-pressed="row.emptyDataTracks.shown"
            class="ml-1 shrink-0 border-0 rounded bg-transparent px-1 py-0.5 text-[10px] text-neutral-500 hover:bg-white/8 hover:text-neutral-100"
            :title="emptyToggleLabel(row.emptyDataTracks)"
            type="button"
            @click="emit('toggleEmpty', row.emptyDataTracks.targetKey)"
          >
            {{ row.emptyDataTracks.shown ? 'Hide' : '+' }}{{ row.emptyDataTracks.shown ? '' : row.emptyDataTracks.count }} empty
          </button>
        </template>

        <template v-else>
          <p class="m-0 min-w-0 flex-1 truncate text-xs text-neutral-300" :title="row.dataTrack.error ?? row.label">
            {{ row.label }}
          </p>
          <span v-if="row.dataTrack.status === 'loading' || row.dataTrack.status === 'idle'" aria-label="Loading" class="i-mingcute-loading-line shrink-0 animate-spin text-xs text-neutral-500" role="status" />
          <button
            v-else-if="row.dataTrack.status === 'error'"
            :aria-label="`Retry loading ${row.label}`"
            class="flex shrink-0 items-center border-0 bg-transparent p-0.5 text-xs text-red-300 hover:text-red-100"
            :title="`Failed: ${row.dataTrack.error ?? 'unknown error'}. Click to retry.`"
            type="button"
            @click="emit('reload', row.id)"
          >
            <span aria-hidden="true" class="i-mingcute-refresh-2-line" />
          </button>
          <span
            v-else-if="row.dataTrack.status === 'missing'"
            class="shrink-0 text-[10px] text-amber-300/80"
            :title="`${row.dataTrack.error ?? 'Source file'} is missing, so this track is unknown, not empty.`"
          >missing</span>
          <span v-else class="shrink-0 text-[10px] text-neutral-500 tabular-nums">{{ row.dataTrack.items.length }}</span>
        </template>
      </div>

      <!-- Drag feedback: an insertion line where the row will land and a lifted copy of the row
           under the pointer. The row itself stays in place, dimmed, as the placeholder. -->
      <template v-if="dragView && dragSourceRow">
        <div
          v-if="dragView.lineTop !== null"
          aria-hidden="true"
          class="pointer-events-none absolute left-0 right-0 z-3 h-0.5 bg-emerald-400"
          data-testid="timeline-drop-line"
          :style="{ top: `${dragView.lineTop - 1}px` }"
        >
          <span class="absolute left-1 top-1/2 h-2 w-2 border-2 border-emerald-400 rounded-full bg-#171717 -translate-y-1/2" />
        </div>
        <div
          aria-hidden="true"
          class="pointer-events-none absolute left-1 right-1 z-4 flex items-center gap-2 rounded-md bg-#323238 px-2 text-neutral-100 shadow-[0_12px_32px_rgba(0,0,0,0.65)] ring-1 ring-white/15"
          data-testid="timeline-drag-ghost"
          :style="{ top: `${dragView.ghostTop}px`, height: `${dragSourceRow.height}px` }"
        >
          <span aria-hidden="true" class="i-mingcute-dots-line rotate-90 text-sm text-neutral-400" />
          <span v-if="trackColors.has(dragSourceRow.id)" class="h-2 w-2 shrink-0 rounded-full" :style="{ backgroundColor: trackColors.get(dragSourceRow.id) }" />
          <div class="min-w-0 flex-1">
            <p class="m-0 truncate font-medium" :class="dragSourceRow.role === 'primary' ? 'text-sm' : 'text-xs'">
              {{ dragSourceRow.label }}
            </p>
            <p v-if="dragSourceRow.role === 'primary'" class="m-0 mt-0.5 truncate text-[10px] text-neutral-400">
              {{ rowDetail(dragSourceRow) }}<template v-if="dragSourceRow.dataTrackCount > 0"> · {{ dragSourceRow.dataTrackCount }} data tracks move with it</template>
            </p>
          </div>
        </div>
      </template>
    </div>
  </aside>
</template>

<style>
/* While a track row is dragged the pointer may cross any element, so the grabbing cursor is set on
   the document instead of on the row that holds the pointer capture. */
.timeline-row-dragging,
.timeline-row-dragging * {
  cursor: grabbing !important;
}
</style>

<style scoped>
/* Keyboard focus on the type icon swaps it for the grip, the cue that Alt+↑/↓ moves the row. Mouse
   presses never focus it (the row's pointerdown prevents the default), so mouse users keep the
   type icon. */
.track-handle:focus-visible .track-handle-icon {
  display: none;
}

.track-handle:focus-visible .track-handle-grip {
  display: block;
}
</style>
