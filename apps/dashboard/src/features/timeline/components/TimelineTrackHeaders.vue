<script setup lang="ts">
import type { EpisodeSession } from '../domain'
import type { EmptyDataTracks, TimelineLayout, TimelineLayoutRow } from '../layout'

import { defaultTimelineRendererTheme } from '@proj-airi/canvas-timeline-renderer'
import { useElementSize } from '@vueuse/core'
import { computed, shallowRef, toRef, useTemplateRef } from 'vue'

import { rowAtOffset } from '../layout'

const props = defineProps<{
  alignmentLabel: string
  editable: boolean
  layout: TimelineLayout
  scrollContainer: HTMLDivElement | null
  scrollTop: number
  sessions: readonly EpisodeSession[]
}>()

const emit = defineEmits<{
  reload: [trackId: string]
  reorder: [sourceTrackId: string, targetTrackId: string]
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

function onHandlePointerDown(event: PointerEvent, row: TimelineLayoutRow): void {
  if (!props.editable || event.button !== 0)
    return
  event.preventDefault()
  ;(event.currentTarget as HTMLElement).setPointerCapture?.(event.pointerId)
  dragSourceId.value = row.id
  dropTargetId.value = null
}

function onHandlePointerMove(event: PointerEvent): void {
  const sourceId = dragSourceId.value
  const list = rowList.value
  if (!sourceId || !list)
    return
  const offset = event.clientY - list.getBoundingClientRect().top
  const target = rowAtOffset(props.layout, offset)
  dropTargetId.value = target && canDrop(sourceId, target) ? target.id : null
}

function onHandlePointerUp(): void {
  const sourceId = dragSourceId.value
  const targetId = dropTargetId.value
  dragSourceId.value = null
  dropTargetId.value = null
  if (sourceId && targetId)
    emit('reorder', sourceId, targetId)
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
    >
      <div
        v-for="row in visibleRows"
        :key="row.id"
        class="group absolute left-0 right-0 flex items-center border-b border-[var(--dashboard-border-color)] pr-2 text-neutral-100"
        :class="[
          row.role === 'session' ? 'bg-#1f1f23' : row.role === 'data' ? 'bg-#141416' : 'bg-#171717',
          dropTargetId === row.id ? 'outline outline-1 outline-emerald-400/70 outline-offset-[-1px]' : '',
          dragSourceId === row.id ? 'opacity-60' : '',
        ]"
        :data-session-key="row.sessionKey"
        :data-track-id="row.id"
        :data-track-kind="row.kind"
        :data-track-role="row.role"
        role="listitem"
        :style="{ height: `${row.height}px`, top: `${row.top}px` }"
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

        <!-- What the row is. Reorderable rows turn their icon into the drag grip on hover or focus,
             so the grip needs no column of its own. -->
        <button
          v-if="editable && (row.role === 'session' || row.role === 'primary')"
          :aria-label="`Reorder ${row.label}`"
          class="ml-1 mr-2 h-5 w-5 flex shrink-0 cursor-grab touch-none items-center justify-center border-0 rounded bg-transparent p-0 text-neutral-500 hover:bg-white/8 hover:text-neutral-100 focus-visible:outline-1 focus-visible:outline-white/60 focus-visible:outline"
          :title="`Drag to reorder ${row.label}`"
          type="button"
          @pointercancel="onHandlePointerUp"
          @pointerdown="onHandlePointerDown($event, row)"
          @pointermove="onHandlePointerMove"
          @pointerup="onHandlePointerUp"
        >
          <span aria-hidden="true" class="group-focus-within:hidden group-hover:hidden" :class="[rowIcon(row), row.role === 'primary' ? 'text-base' : 'text-sm']" />
          <span aria-hidden="true" class="i-mingcute-dots-line hidden rotate-90 text-sm group-focus-within:block group-hover:block" />
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
    </div>
  </aside>
</template>
