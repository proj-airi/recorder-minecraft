<script setup lang="ts">
import type { CaptureEventKind } from '../captureEvents'
import type { EventLogRow as EventLogRowData } from '../eventLogRows'

import { VList } from 'virtua/vue'
import { computed, nextTick, shallowRef, useTemplateRef, watch } from 'vue'

import EventLogRow from './EventLogRow.vue'

import { useEditorWorkspaceContext } from '../../editor/composables/useEditorWorkspaceContext'
import { CAPTURE_EVENT_KINDS } from '../captureEvents'
import { buildEventLogRows, currentRowIndex, filterEventLogRows } from '../eventLogRows'
import { useCaptureEventSources } from '../useCaptureEvents'

const { episode, session } = useEditorWorkspaceContext()

const defaultKinds = () => new Set(CAPTURE_EVENT_KINDS.filter(info => info.defaultVisible).map(info => info.kind))
const kinds = shallowRef<ReadonlySet<CaptureEventKind>>(defaultKinds())
const players = shallowRef<ReadonlySet<string>>(new Set())
const query = shallowRef('')
const follow = shallowRef(true)
const showTypeFilters = shallowRef(false)
const expanded = shallowRef<ReadonlySet<string>>(new Set())
const list = useTemplateRef<InstanceType<typeof VList>>('list')

const placements = computed(() => episode().placements)
const handles = useCaptureEventSources(() => placements.value.map(placement => placement.source.eventsUrl))
const parsedByUrl = computed(() => new Map(Array.from(handles.value, ([url, handle]) => [url, handle.data.value] as const)))
const rows = computed(() => buildEventLogRows(episode(), parsedByUrl.value))
const visibleRows = computed(() => filterEventLogRows(rows.value, { kinds: kinds.value, players: players.value, query: query.value }))
const currentIndex = computed(() => currentRowIndex(visibleRows.value, session.playheadTick.value))
const currentKey = computed(() => visibleRows.value[currentIndex.value]?.key ?? null)

const loading = computed(() => Array.from(handles.value.values()).filter(handle => handle.status.value === 'loading'))
const failed = computed(() => Array.from(handles.value.values()).filter(handle => handle.status.value === 'error'))
const loadingLines = computed(() => loading.value.reduce((total, handle) => total + handle.progress.value, 0))

const playerOptions = computed(() => {
  const colors = new Map(episode().segments.flatMap(segment => segment.placementId ? [[segment.placementId, segment.color] as const] : []))
  const options = new Map<string, { color: string, key: string, name: string }>()
  for (const placement of placements.value) {
    if (!options.has(placement.playerKey))
      options.set(placement.playerKey, { color: colors.get(placement.id) ?? '#a3a3a3', key: placement.playerKey, name: placement.playerName })
  }
  return Array.from(options.values())
})
const colorByPlacement = computed(() => new Map(episode().segments.flatMap(segment => segment.placementId ? [[segment.placementId, segment.color] as const] : [])))
const kindCounts = computed(() => {
  const counts = new Map<CaptureEventKind, number>()
  for (const row of rows.value)
    counts.set(row.entry.kind, (counts.get(row.entry.kind) ?? 0) + 1)
  return counts
})
const filtersChanged = computed(() => players.value.size > 0 || query.value.trim() !== '' || !sameSet(kinds.value, defaultKinds()))

function sameSet<T>(left: ReadonlySet<T>, right: ReadonlySet<T>): boolean {
  return left.size === right.size && Array.from(left).every(value => right.has(value))
}

function toggleKind(kind: CaptureEventKind): void {
  const next = new Set(kinds.value)
  if (next.has(kind))
    next.delete(kind)
  else
    next.add(kind)
  kinds.value = next
}

function togglePlayer(key: string): void {
  const next = new Set(players.value)
  if (next.has(key))
    next.delete(key)
  else
    next.add(key)
  players.value = next
}

function toggleExpanded(key: string): void {
  const next = new Set(expanded.value)
  if (next.has(key))
    next.delete(key)
  else
    next.add(key)
  expanded.value = next
}

function resetFilters(): void {
  kinds.value = defaultKinds()
  players.value = new Set()
  query.value = ''
}

function seek(row: EventLogRowData): void {
  session.seekToTick(row.episodeTick)
}

function scrollToCurrent(): void {
  const index = currentIndex.value
  if (index >= 0)
    list.value?.scrollToIndex(index, { align: session.isPlaying.value ? 'center' : 'nearest' })
}

watch([currentIndex, follow], async () => {
  if (!follow.value)
    return
  await nextTick()
  scrollToCurrent()
})
</script>

<template>
  <section aria-label="Capture event log" class="event-log h-full min-h-0 flex flex-col bg-neutral-950">
    <div class="flex flex-col gap-1.5 border-b border-[var(--dashboard-border-color)] p-1.5">
      <div class="flex items-center gap-1">
        <label class="relative min-w-0 flex-1">
          <span class="sr-only">Search events</span>
          <span aria-hidden="true" class="i-mingcute-search-line pointer-events-none absolute left-2 top-1/2 text-xs text-neutral-500 -translate-y-1/2" />
          <input
            v-model="query"
            aria-label="Search events"
            class="h-7 w-full border-0 rounded bg-white/5 pl-6 pr-2 text-xs text-neutral-200 outline-none focus:bg-white/8 placeholder:text-neutral-500"
            placeholder="Search events"
            type="search"
          >
        </label>
        <button
          :aria-expanded="showTypeFilters"
          aria-label="Event type filters"
          class="h-7 inline-flex shrink-0 items-center gap-1 border-0 rounded bg-white/5 px-2 text-[11px] text-neutral-300 hover:bg-white/10"
          :class="!sameSet(kinds, defaultKinds()) && 'text-amber-200'"
          title="Event type filters"
          type="button"
          @click="showTypeFilters = !showTypeFilters"
        >
          <span aria-hidden="true" class="i-mingcute-filter-line" />
          {{ kinds.size }}/{{ CAPTURE_EVENT_KINDS.length }}
        </button>
        <button
          :aria-pressed="follow"
          aria-label="Follow playhead"
          class="h-7 inline-flex shrink-0 items-center gap-1 border-0 rounded px-2 text-[11px] hover:bg-white/10"
          :class="follow ? 'bg-amber-400/15 text-amber-200' : 'bg-white/5 text-neutral-400'"
          :title="follow ? 'Following the playhead. Click to pause follow.' : 'Follow paused. Click to follow the playhead.'"
          type="button"
          @click="follow = !follow"
        >
          <span aria-hidden="true" :class="follow ? 'i-mingcute-location-fill' : 'i-mingcute-location-line'" />
          {{ follow ? 'Following' : 'Paused' }}
        </button>
      </div>

      <div v-if="showTypeFilters" aria-label="Event types" class="flex flex-wrap gap-1" role="group">
        <label
          v-for="info in CAPTURE_EVENT_KINDS"
          :key="info.kind"
          class="inline-flex cursor-pointer items-center gap-1 rounded px-1.5 py-0.5 text-[10px]"
          :class="kinds.has(info.kind) ? 'bg-white/10 text-neutral-100' : 'bg-white/3 text-neutral-500'"
        >
          <input class="m-0 h-3 w-3 accent-amber-400" :checked="kinds.has(info.kind)" type="checkbox" @change="toggleKind(info.kind)">
          {{ info.label }}
          <span class="text-neutral-500 tabular-nums">{{ kindCounts.get(info.kind) ?? 0 }}</span>
        </label>
      </div>

      <div v-if="playerOptions.length" aria-label="Players" class="flex flex-wrap items-center gap-1" role="group">
        <button
          v-for="player in playerOptions"
          :key="player.key"
          :aria-label="`Player ${player.name}`"
          :aria-pressed="players.size === 0 || players.has(player.key)"
          class="inline-flex items-center gap-1 border-0 rounded px-1.5 py-0.5 text-[10px]"
          :class="players.size === 0 || players.has(player.key) ? 'bg-white/10 text-neutral-100' : 'bg-white/3 text-neutral-500'"
          :title="players.has(player.key) ? `Stop filtering by ${player.name}` : `Show only ${player.name} (add more to compare)`"
          type="button"
          @click="togglePlayer(player.key)"
        >
          <span aria-hidden="true" class="h-1.5 w-1.5 rounded-full" :style="{ backgroundColor: player.color }" />
          {{ player.name }}
        </button>
        <span class="ml-auto text-[10px] text-neutral-500 tabular-nums" role="status">
          {{ visibleRows.length.toLocaleString('en-US') }} / {{ rows.length.toLocaleString('en-US') }} events
        </span>
      </div>

      <p v-if="loading.length" class="m-0 flex items-center gap-1.5 text-[10px] text-neutral-400" role="status">
        <span aria-hidden="true" class="i-mingcute-loading-3-line animate-spin" />
        Parsing events for {{ loading.length }} {{ loading.length === 1 ? 'Play' : 'Plays' }}… {{ loadingLines.toLocaleString('en-US') }} lines
      </p>
      <p v-for="handle in failed" :key="handle.url" class="m-0 flex items-center gap-1.5 text-[10px] text-red-300" role="alert">
        <span aria-hidden="true" class="i-mingcute-warning-line shrink-0" />
        <span class="min-w-0 flex-1 truncate" :title="`${handle.url}: ${handle.error.value}`">Events could not load: {{ handle.error.value }}</span>
        <button aria-label="Retry loading events" class="border-0 rounded bg-white/8 px-1.5 py-0.5 text-[10px] text-neutral-200 hover:bg-white/12" title="Retry loading events" type="button" @click="handle.reload()">
          Retry
        </button>
      </p>
    </div>

    <div v-if="placements.length === 0" class="m-auto max-w-xs p-4 text-center text-sm text-neutral-500">
      Add Plays to the timeline to list their capture events.
    </div>
    <div v-else-if="visibleRows.length === 0 && !loading.length" class="m-auto max-w-xs p-4 text-center text-sm text-neutral-500">
      <p class="m-0">
        No events match these filters.
      </p>
      <button v-if="filtersChanged" aria-label="Reset filters" title="Reset filters" class="mt-3 border-0 rounded bg-white/8 px-2 py-1 text-xs text-neutral-200 hover:bg-white/12" type="button" @click="resetFilters">
        Reset filters
      </button>
    </div>
    <div v-else class="event-log-list min-h-0 flex-1" role="list" aria-label="Capture events">
      <VList ref="list" :data="visibleRows" :item-size="40" class="h-full">
        <template #default="{ item: row }">
          <EventLogRow
            :key="row.key"
            :color="colorByPlacement.get(row.placementId) ?? '#a3a3a3'"
            :current="row.key === currentKey"
            :expanded="expanded.has(row.key)"
            :row="row"
            @seek="seek(row)"
            @toggle="toggleExpanded(row.key)"
          />
        </template>
      </VList>
    </div>
  </section>
</template>

<style scoped>
.event-log {
  container: event-log / inline-size;
}
</style>
