<script setup lang="ts">
import type { ResourceSort, ResourceTreeNode } from '../resourceTree'

import { Pane, Splitpanes } from 'splitpanes'
import { VList } from 'virtua/vue'
import { computed, nextTick, shallowRef, useTemplateRef, watch } from 'vue'

import ComboboxSelect from '../../basic/components/ComboboxSelect.vue'
import RecordingCalendarFilter from './RecordingCalendarFilter.vue'
import ResourceBrowserState from './ResourceBrowserState.vue'
import ResourceDetails from './ResourceDetails.vue'
import ResourceTreeRow from './ResourceTreeRow.vue'

import { useEditorWorkspaceContext } from '../../editor/composables/useEditorWorkspaceContext'
import { ancestorsOf, buildResourceTree, toggleExpanded } from '../resourceTree'
import { isReplayAddable, toTimelineWorldSession } from '../worldSession'

const { addReplay, addSession, catalog, episode } = useEditorWorkspaceContext()

const allFilterValue = '__all__'
const query = shallowRef('')
const sort = shallowRef<ResourceSort>('newest')
const serverFilter = shallowRef(allFilterValue)
const playerFilter = shallowRef(allFilterValue)
const timeRange = shallowRef<null | number[]>(null)
const showFilters = shallowRef(false)
const showCalendar = shallowRef(false)
const expandedIds = shallowRef<ReadonlySet<string>>(new Set())
const activeId = shallowRef<null | string>(null)
const detailsNode = shallowRef<null | ResourceTreeNode>(null)
const announcement = shallowRef('')
const list = useTemplateRef<InstanceType<typeof VList>>('list')
const treeElement = useTemplateRef<HTMLDivElement>('tree')

const hasCatalog = computed(() => catalog.servers.value.length > 0)
const treeFilter = computed(() => ({
  playerUuid: playerFilter.value === allFilterValue ? undefined : playerFilter.value,
  query: query.value,
  serverInstanceId: serverFilter.value === allFilterValue ? undefined : serverFilter.value,
  sort: sort.value,
  timeRange: timeRange.value,
}))
const tree = computed(() => buildResourceTree(catalog.servers.value, treeFilter.value, expandedIds.value))
const nodeIndex = computed(() => new Map(tree.value.nodes.map((node, index) => [node.id, index])))
const activeIndex = computed(() => activeId.value ? nodeIndex.value.get(activeId.value) ?? -1 : -1)
const siblingPositions = computed(() => {
  const positions = new Map<string, { position: number, size: number }>()
  const roots = tree.value.nodes.filter(node => !node.parentId)
  roots.forEach((node, index) => positions.set(node.id, { position: index + 1, size: roots.length }))
  for (const node of tree.value.nodes) {
    node.childIds.forEach((childId, index) => positions.set(childId, { position: index + 1, size: node.childIds.length }))
  }
  return positions
})
const onTimeline = computed(() => new Set(episode().placements.map(placement => placement.connectionId)))
const filtersDisabled = computed(() => !hasCatalog.value)
const activeFilterCount = computed(() => [serverFilter.value !== allFilterValue, playerFilter.value !== allFilterValue, timeRange.value !== null].filter(Boolean).length)

const serverOptions = computed(() => [
  { description: `${catalog.servers.value.length} available instances`, label: 'All servers', value: allFilterValue },
  ...catalog.servers.value.map(server => ({
    description: `${server.worldSessions?.length ?? 0} world sessions · ${server.players?.reduce((count, player) => count + (player.replays?.length ?? 0), 0) ?? 0} Plays`,
    label: server.name ?? 'Unnamed server',
    value: server.instanceId ?? '',
  })),
])
const playerOptions = computed(() => {
  const players = catalog.servers.value
    .filter(server => serverFilter.value === allFilterValue || server.instanceId === serverFilter.value)
    .flatMap(server => server.players ?? [])
  const options = Array.from(new Map(players.map(player => [player.uuid, {
    description: `${player.replays?.length ?? 0} Plays · ${player.uuid ?? 'Unknown UUID'}`,
    label: player.name ?? 'Unnamed player',
    value: player.uuid ?? '',
  }])).values())
  return [{ description: `${options.length} available players`, label: 'All players', value: allFilterValue }, ...options]
})
const serverFilterLabel = computed(() => serverOptions.value.find(option => option.value === serverFilter.value)?.label)
const playerFilterLabel = computed(() => playerOptions.value.find(option => option.value === playerFilter.value)?.label)
const dateFilterLabel = computed(() => {
  const range = timeRange.value
  if (range?.length !== 2)
    return 'All dates'
  const start = new Date(range[0]!).toISOString().slice(0, 10)
  const end = new Date(range[1]! - 1).toISOString().slice(0, 10)
  return start === end ? start : `${start} – ${end}`
})

function clearFilters(): void {
  playerFilter.value = allFilterValue
  serverFilter.value = allFilterValue
  timeRange.value = null
  query.value = ''
}

function setActive(node: ResourceTreeNode, options: { scroll?: boolean } = {}): void {
  activeId.value = node.id
  detailsNode.value = node
  if (node.kind === 'play')
    catalog.selectReplay(node.replay)
  if (options.scroll) {
    const index = nodeIndex.value.get(node.id)
    if (index !== undefined)
      list.value?.scrollToIndex(index, { align: 'nearest' })
  }
}

function select(node: ResourceTreeNode): void {
  setActive(node)
  treeElement.value?.focus({ preventScroll: true })
}

function toggle(node: ResourceTreeNode, expanded = !node.expanded): void {
  expandedIds.value = toggleExpanded(expandedIds.value, node, expanded)
}

async function add(node: ResourceTreeNode): Promise<void> {
  const before = episode().placements.length
  if (node.kind === 'session') {
    const world = toTimelineWorldSession(node.session)
    const plays = node.sessionPlays.filter(isReplayAddable)
    if (world)
      addSession(world.sessionId, plays, world)
    else
      plays.forEach(addReplay)
  }
  else if (node.kind === 'capture-session') {
    addSession(node.sessionId, node.plays.filter(isReplayAddable))
  }
  else if (node.kind === 'play') {
    if (!isReplayAddable(node.replay)) {
      announcement.value = 'This Play cannot be added to the timeline.'
      return
    }
    addReplay(node.replay)
  }
  else {
    return
  }
  await nextTick()
  const added = episode().placements.length - before
  announcement.value = added > 0
    ? `Added ${added} ${added === 1 ? 'Play' : 'Plays'} to the timeline.`
    : 'Already on the timeline.'
}

function reveal(nodeId: string): void {
  const ancestors = ancestorsOf(catalog.servers.value, treeFilter.value, nodeId)
  if (!ancestors) {
    announcement.value = 'That item is hidden by the current filters.'
    return
  }
  let next: ReadonlySet<string> = expandedIds.value
  for (const ancestor of ancestors)
    next = toggleExpanded(next, ancestor, true)
  expandedIds.value = next
  void nextTick(() => {
    const node = tree.value.nodes.find(candidate => candidate.id === nodeId)
    if (node)
      setActive(node, { scroll: true })
  })
}

function onTreeKeydown(event: KeyboardEvent): void {
  const nodes = tree.value.nodes
  if (nodes.length === 0)
    return
  const index = activeIndex.value
  const current = index >= 0 ? nodes[index]! : null
  const move = (target: number) => setActive(nodes[Math.max(0, Math.min(nodes.length - 1, target))]!, { scroll: true })

  switch (event.key) {
    case 'ArrowDown':
      move(index + 1)
      break
    case 'ArrowUp':
      move(index < 0 ? 0 : index - 1)
      break
    case 'Home':
      move(0)
      break
    case 'End':
      move(nodes.length - 1)
      break
    case 'ArrowRight':
      if (!current)
        move(0)
      else if (current.kind !== 'play' && !current.expanded)
        toggle(current, true)
      else if (current.childIds.length)
        move(index + 1)
      break
    case 'ArrowLeft':
      if (!current) {
        move(0)
      }
      else if (current.kind !== 'play' && current.expanded) {
        toggle(current, false)
      }
      else if (current.parentId) {
        const parentIndex = nodeIndex.value.get(current.parentId)
        if (parentIndex !== undefined)
          move(parentIndex)
      }
      break
    case 'Enter':
      if (!current)
        return
      if (current.kind === 'server' || current.kind === 'unlinked')
        toggle(current)
      else
        void add(current)
      break
    default:
      return
  }
  // The editor binds arrow keys to timeline seeking on the window; keep tree navigation local.
  event.preventDefault()
  event.stopPropagation()
}

function onTreeFocus(): void {
  if (activeIndex.value < 0 && tree.value.nodes.length)
    activeId.value = tree.value.nodes[0]!.id
}

// Keep the details pane in sync with refreshed catalog data.
watch(tree, (view) => {
  const current = detailsNode.value
  if (!current)
    return
  const fresh = view.nodes.find(node => node.id === current.id)
  if (fresh)
    detailsNode.value = fresh
})

watch(serverFilter, () => {
  playerFilter.value = allFilterValue
})
</script>

<template>
  <section aria-label="Replay resources" class="h-full min-h-0 flex flex-col bg-neutral-900">
    <div class="flex flex-col gap-1.5 border-b border-[var(--dashboard-border-color)] p-1.5">
      <div class="flex items-center gap-1">
        <label class="relative min-w-0 flex-1">
          <span class="sr-only">Search recordings</span>
          <span aria-hidden="true" class="i-mingcute-search-line pointer-events-none absolute left-2 top-1/2 text-xs text-neutral-500 -translate-y-1/2" />
          <input
            v-model="query"
            aria-label="Search recordings"
            class="h-7 w-full border-0 rounded bg-white/5 pl-6 pr-1 text-xs text-neutral-200 outline-none focus:bg-white/8 placeholder:text-neutral-500"
            :disabled="filtersDisabled"
            placeholder="Player, server, or id"
            type="search"
          >
        </label>
        <button
          :aria-expanded="showFilters"
          aria-label="Filters"
          class="relative h-7 w-7 flex shrink-0 items-center justify-center border-0 rounded bg-white/5 p-0 text-neutral-300 hover:bg-white/10 disabled:opacity-40"
          :class="activeFilterCount > 0 && 'text-amber-200'"
          :disabled="filtersDisabled"
          :title="activeFilterCount ? `${activeFilterCount} filters active` : 'Filters'"
          type="button"
          @click="showFilters = !showFilters"
        >
          <span aria-hidden="true" class="i-mingcute-filter-line text-sm" />
          <span v-if="activeFilterCount" class="absolute right-0.5 top-0.5 h-1.5 w-1.5 rounded-full bg-amber-300" aria-hidden="true" />
        </button>
        <button
          aria-label="Refresh recordings"
          class="h-7 w-7 flex shrink-0 items-center justify-center border-0 rounded bg-white/5 p-0 text-neutral-300 hover:bg-white/10 disabled:opacity-40"
          :disabled="catalog.isRefreshing.value || catalog.isLoading.value"
          title="Rescan the artifacts root"
          type="button"
          @click="catalog.refresh"
        >
          <span aria-hidden="true" class="i-mingcute-refresh-2-line text-sm" :class="(catalog.isRefreshing.value || catalog.isLoading.value) && 'animate-spin'" />
        </button>
      </div>

      <div v-if="showFilters && !filtersDisabled" class="flex flex-col gap-1.5" aria-label="Recording filters" role="group">
        <ComboboxSelect v-model="serverFilter" label="Server filter" :options="serverOptions" placeholder="Filter servers">
          <template #option="{ option }">
            <span class="min-w-0 flex items-center gap-2">
              <span aria-hidden="true" class="i-mingcute-server-line shrink-0 text-base text-neutral-300" />
              <span class="min-w-0 flex flex-col">
                <span class="truncate">{{ option.label }}</span>
                <span class="truncate text-xs text-neutral-500">{{ option.description }}</span>
              </span>
            </span>
          </template>
        </ComboboxSelect>
        <ComboboxSelect v-model="playerFilter" label="Player filter" :options="playerOptions" placeholder="Filter players">
          <template #option="{ option }">
            <span class="min-w-0 flex items-center gap-2">
              <span aria-hidden="true" class="i-mingcute-user-3-line shrink-0 text-base text-neutral-300" />
              <span class="min-w-0 flex flex-col">
                <span class="truncate">{{ option.label }}</span>
                <span class="truncate text-xs text-neutral-500">{{ option.description }}</span>
              </span>
            </span>
          </template>
        </ComboboxSelect>
        <button
          :aria-expanded="showCalendar"
          :aria-label="`Recording dates: ${dateFilterLabel}`"
          title="Filter by recording date"
          class="h-7 flex items-center gap-1.5 border-0 rounded bg-white/5 px-2 text-left text-xs text-neutral-300 hover:bg-white/10"
          type="button"
          @click="showCalendar = !showCalendar"
        >
          <span aria-hidden="true" class="i-mingcute-calendar-line shrink-0" />
          <span class="min-w-0 flex-1 truncate">{{ dateFilterLabel }}</span>
          <span aria-hidden="true" class="i-mingcute-down-line shrink-0 text-neutral-500 transition-transform" :class="showCalendar && 'rotate-180'" />
        </button>
        <RecordingCalendarFilter v-if="showCalendar" v-model="timeRange" :replays="catalog.replays.value" />
        <label class="flex items-center gap-1.5 text-[10px] text-neutral-500">
          Sort
          <select v-model="sort" aria-label="Sort recordings" class="h-6 min-w-0 flex-1 border-0 rounded bg-white/5 px-1 text-xs text-neutral-200 outline-none">
            <option value="newest">Newest first</option>
            <option value="oldest">Oldest first</option>
            <option value="name">Player name</option>
          </select>
        </label>
      </div>

      <div v-if="activeFilterCount && !showFilters" class="flex flex-wrap gap-1" aria-label="Active filters">
        <button v-if="serverFilter !== allFilterValue" class="filter-chip" type="button" :aria-label="`Clear server filter ${serverFilterLabel}`" title="Clear server filter" @click="serverFilter = allFilterValue">
          {{ serverFilterLabel }}<span aria-hidden="true" class="i-mingcute-close-line" />
        </button>
        <button v-if="playerFilter !== allFilterValue" class="filter-chip" type="button" :aria-label="`Clear player filter ${playerFilterLabel}`" title="Clear player filter" @click="playerFilter = allFilterValue">
          {{ playerFilterLabel }}<span aria-hidden="true" class="i-mingcute-close-line" />
        </button>
        <button v-if="timeRange" class="filter-chip" type="button" :aria-label="`Clear date filter ${dateFilterLabel}`" title="Clear date filter" @click="timeRange = null">
          {{ dateFilterLabel }}<span aria-hidden="true" class="i-mingcute-close-line" />
        </button>
      </div>

      <p v-if="hasCatalog" class="m-0 flex items-center gap-1 text-[10px] text-neutral-500" role="status">
        <span>{{ tree.sessionCount }} sessions · {{ tree.playCount }} Plays</span>
        <span v-if="catalog.summaryError.value" class="ml-auto truncate text-red-300" :title="catalog.summaryError.value">Summaries failed</span>
        <span v-else-if="catalog.isSummaryLoading.value" class="ml-auto">Loading summaries…</span>
      </p>
    </div>

    <ResourceBrowserState
      v-if="catalog.isLoading.value && !hasCatalog"
      kind="loading"
      message="Reading server instances, world sessions, and Play metadata."
      title="Loading recordings…"
    />
    <ResourceBrowserState
      v-else-if="catalog.error.value"
      action-label="Retry connection"
      :detail="catalog.error.value"
      kind="error"
      message="Start recorder-minecraft serve, then retry the connection."
      title="Artifact service unavailable"
      @action="catalog.load"
    />
    <ResourceBrowserState
      v-else-if="catalog.replays.value.length === 0 && catalog.worldSessions.value.length === 0"
      action-label="Rescan recordings"
      kind="empty"
      message="The service is available, but it did not find any readable recordings."
      title="No recordings available"
      @action="catalog.refresh"
    />
    <ResourceBrowserState
      v-else-if="tree.nodes.length === 0"
      action-label="Clear filters"
      kind="filtered"
      message="Try another search, server, player, or date range."
      title="No recordings match"
      @action="clearFilters"
    />
    <Splitpanes v-else horizontal class="resource-split min-h-0 flex-1">
      <Pane :min-size="25" :size="detailsNode ? 55 : 100">
        <!-- NOTICE: Virtua's Vue binding derives its stable item identity from the single slot root's
             key. See `https://github.com/inokawa/virtua/blob/dc92d9d6485df2578e10f5acb06875c69d1bda3b/src/vue/utils.ts#L7-L16`. -->
        <div
          ref="tree"
          :aria-activedescendant="activeId && activeIndex >= 0 ? `resource-node-${activeId}` : undefined"
          aria-label="Recordings"
          class="h-full outline-none focus-visible:ring-1 focus-visible:ring-amber-300/40 focus-visible:ring-inset"
          role="tree"
          tabindex="0"
          @focus="onTreeFocus"
          @keydown="onTreeKeydown"
        >
          <VList ref="list" :data="tree.nodes" :item-size="40" class="h-full">
            <template #default="{ item: node }">
              <ResourceTreeRow
                :key="node.id"
                :active="node.id === activeId"
                :node="node"
                :on-timeline="onTimeline"
                :set-position="siblingPositions.get(node.id)?.position ?? 1"
                :set-size="siblingPositions.get(node.id)?.size ?? 1"
                @add="add(node)"
                @select="select(node)"
                @toggle="toggle(node)"
              />
            </template>
          </VList>
        </div>
      </Pane>
      <Pane v-if="detailsNode" :min-size="20" :size="45">
        <ResourceDetails
          :node="detailsNode"
          :on-timeline="onTimeline"
          :world-sessions="catalog.worldSessions.value"
          @add="detailsNode && add(detailsNode)"
          @close="detailsNode = null"
          @reveal="reveal"
        />
      </Pane>
    </Splitpanes>
    <p class="sr-only" aria-live="polite">
      {{ announcement }}
    </p>
  </section>
</template>

<style scoped>
.filter-chip {
  align-items: center;
  background: rgb(252 211 77 / 12%);
  border: 0;
  border-radius: 0.25rem;
  color: rgb(253 230 138);
  display: inline-flex;
  font-size: 10px;
  gap: 0.25rem;
  max-width: 100%;
  overflow: hidden;
  padding: 0.125rem 0.375rem;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* NOTICE: Splitpanes leaves splitter visuals to consumers; same treatment as the input monitor. */
.resource-split :deep(> .splitpanes__splitter) {
  background: var(--dashboard-border-color);
  flex: 0 0 1px;
  position: relative;
}

.resource-split :deep(> .splitpanes__splitter::before) {
  content: '';
  inset: -4px 0;
  position: absolute;
}
</style>
