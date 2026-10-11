<script setup lang="ts">
import type { EpisodeSession } from '../domain'
import type { TimelineLayout, TimelineLayoutRow } from '../layout'

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
        class="absolute left-0 right-0 flex items-center border-b border-[var(--dashboard-border-color)] text-neutral-100"
        :class="[
          row.role === 'session' ? 'bg-#1f1f23 px-1.5' : row.role === 'primary' ? 'bg-#171717 px-3' : 'bg-#141416 pl-6 pr-2',
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
        <template v-if="row.role === 'session'">
          <button
            :aria-expanded="!row.collapsed"
            :aria-label="`${row.collapsed ? 'Expand' : 'Collapse'} ${row.label}`"
            class="mr-1 flex shrink-0 items-center justify-center border-0 bg-transparent p-0.5 text-neutral-400 hover:text-neutral-100"
            :title="`${row.collapsed ? 'Expand' : 'Collapse'} ${row.label}`"
            type="button"
            @click="emit('toggleGroup', row.groupId)"
          >
            <span aria-hidden="true" class="text-sm" :class="row.collapsed ? 'i-mingcute-right-line' : 'i-mingcute-down-line'" />
          </button>
          <button
            v-if="editable"
            :aria-label="`Reorder ${row.label}`"
            class="mr-1 flex shrink-0 cursor-grab touch-none items-center border-0 bg-transparent p-0 text-sm text-neutral-500 hover:text-neutral-200"
            :title="`Drag to reorder ${row.label}`"
            type="button"
            @pointercancel="onHandlePointerUp"
            @pointerdown="onHandlePointerDown($event, row)"
            @pointermove="onHandlePointerMove"
            @pointerup="onHandlePointerUp"
          >
            <span aria-hidden="true" class="i-mingcute-dot-grid-line" />
          </button>
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

        <template v-else-if="row.role === 'world'">
          <span aria-hidden="true" class="i-mingcute-earth-2-line mr-2 shrink-0 text-sm text-neutral-500" />
          <p class="m-0 min-w-0 truncate text-xs text-neutral-300">
            World
            <span class="ml-1 text-[10px] text-neutral-500">{{ sessionsByKey.get(row.sessionKey)?.world ? 'world session' : 'session span' }}</span>
          </p>
        </template>

        <template v-else-if="row.role === 'primary'">
          <button
            v-if="editable"
            :aria-label="`Reorder ${row.label}`"
            class="timeline-track-drag-handle mr-1 flex flex-[0_0_1.5rem] cursor-grab touch-none items-center self-stretch justify-center border-0 bg-transparent p-0 text-base focus-visible:text-[#dfdfdf] hover:text-[#dfdfdf] focus-visible:outline-1 focus-visible:outline-white/60 focus-visible:outline-offset-[-3px] focus-visible:outline"
            :title="`Drag to reorder ${row.label}`"
            type="button"
            @pointercancel="onHandlePointerUp"
            @pointerdown="onHandlePointerDown($event, row)"
            @pointermove="onHandlePointerMove"
            @pointerup="onHandlePointerUp"
          >
            <span aria-hidden="true" class="i-mingcute-dot-grid-line" />
          </button>
          <span
            v-else
            aria-hidden="true"
            class="mr-2 shrink-0 text-base text-neutral-500"
            :class="row.kind === 'video' ? 'i-mingcute-video-line' : 'i-mingcute-user-3-line'"
          />
          <div class="min-w-0 flex-1">
            <p class="m-0 truncate text-sm text-neutral-200" :title="row.track.playerUuid ?? row.label">
              {{ row.label }}
            </p>
            <p class="m-0 mt-0.5 truncate text-[10px] text-neutral-500 tracking-wider uppercase">
              {{ row.playCount }} {{ row.playCount === 1 ? 'play' : 'plays' }} · {{ row.kind }}
            </p>
          </div>
          <button
            v-if="row.dataTrackCount > 0"
            :aria-expanded="!row.collapsed"
            :aria-label="`${row.collapsed ? 'Show' : 'Hide'} data tracks of ${row.label}`"
            class="ml-1 flex shrink-0 items-center gap-0.5 border-0 bg-transparent p-0.5 text-[10px] text-neutral-500 hover:text-neutral-100"
            :title="`${row.collapsed ? 'Show' : 'Hide'} ${row.dataTrackCount} data tracks`"
            type="button"
            @click="emit('toggleGroup', row.groupId)"
          >
            {{ row.dataTrackCount }}
            <span aria-hidden="true" class="text-sm" :class="row.collapsed ? 'i-mingcute-right-line' : 'i-mingcute-down-line'" />
          </button>
        </template>

        <template v-else>
          <span aria-hidden="true" class="i-mingcute-chart-line-line mr-1.5 shrink-0 text-xs text-neutral-500" />
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
          <span v-else class="shrink-0 text-[10px] text-neutral-500 tabular-nums">{{ row.dataTrack.items.length }}</span>
        </template>
      </div>
    </div>
  </aside>
</template>
