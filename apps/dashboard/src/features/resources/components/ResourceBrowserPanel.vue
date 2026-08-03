<script setup lang="ts">
import type { RecorderMinecraftApiV1Replay } from '@proj-airi/recorder-minecraft-api'

import { VList } from 'virtua/vue'
import { computed, shallowRef, watch } from 'vue'

import ComboboxSelect from '../../basic/components/ComboboxSelect.vue'
import RecordingCalendarFilter from './RecordingCalendarFilter.vue'
import ReplayResourceRow from './ReplayResourceRow.vue'
import ResourceBrowserState from './ResourceBrowserState.vue'

import { useEditorWorkspaceContext } from '../../editor/composables/useEditorWorkspaceContext'

const { catalog } = useEditorWorkspaceContext()
const allFilterValue = '__all__'
const serverFilter = shallowRef(allFilterValue)
const playerFilter = shallowRef(allFilterValue)
const timeRange = shallowRef<number[] | null>(null)
const filtersDisabled = computed(() => catalog.isLoading.value || Boolean(catalog.error.value) || catalog.replays.value.length === 0)

const serverOptions = computed(() => [
  {
    description: `${catalog.servers.value.length} available instances`,
    label: 'All servers',
    value: allFilterValue,
  },
  ...catalog.servers.value.map(server => ({
    description: `${server.players?.length ?? 0} players · ${server.players?.reduce((count, player) => count + (player.replays?.length ?? 0), 0) ?? 0} recordings`,
    label: server.name ?? 'Unnamed server',
    value: server.instanceId ?? '',
  })),
])
const playerOptions = computed(() => {
  const players = catalog.servers.value
    .filter(server => serverFilter.value === allFilterValue || server.instanceId === serverFilter.value)
    .flatMap(server => server.players ?? [])
  const options = Array.from(new Map(players.map(player => [player.uuid, {
    description: `${player.replays?.length ?? 0} recordings · ${player.uuid ?? 'Unknown UUID'}`,
    label: player.name ?? 'Unnamed player',
    value: player.uuid ?? '',
  }])).values())
  return [{ description: `${options.length} available players`, label: 'All players', value: allFilterValue }, ...options]
})
const scopedReplays = computed(() => catalog.replays.value.filter((replay) => {
  if (serverFilter.value !== allFilterValue && replay.serverInstanceId !== serverFilter.value)
    return false
  if (playerFilter.value !== allFilterValue && replay.playerUuid !== playerFilter.value)
    return false
  return true
}))
const filteredReplays = computed(() => scopedReplays.value.filter((replay) => {
  if (timeRange.value?.length === 2) {
    const startedAt = replay.startedAt ? Date.parse(replay.startedAt) : Number.NaN
    if (!Number.isFinite(startedAt) || startedAt < timeRange.value[0]! || startedAt >= timeRange.value[1]!)
      return false
  }
  return true
}))

function clearFilters(): void {
  playerFilter.value = allFilterValue
  serverFilter.value = allFilterValue
  timeRange.value = null
}

function select(replay: RecorderMinecraftApiV1Replay): void {
  catalog.selectReplay(replay)
}

watch(serverFilter, () => {
  playerFilter.value = allFilterValue
})
</script>

<template>
  <section aria-label="Replay resources" class="h-full min-h-0 flex flex-col bg-neutral-900">
    <div class="grid grid-cols-2 gap-2 border-b border-white/8 p-2">
      <div class="min-w-0">
        <span class="mb-1 block text-xs text-neutral-500 uppercase">Server</span>
        <ComboboxSelect v-model="serverFilter" :disabled="filtersDisabled" label="Server filter" :options="serverOptions" placeholder="Filter servers">
          <template #option="{ option }">
            <span class="min-w-0 flex items-center gap-2">
              <span aria-hidden="true" class="i-mingcute-server-line shrink-0 text-base text-sky-300" />
              <span class="min-w-0 flex flex-col">
                <span class="truncate">{{ option.label }}</span>
                <span class="truncate text-xs text-neutral-500">{{ option.description }}</span>
              </span>
            </span>
          </template>
        </ComboboxSelect>
      </div>
      <div class="min-w-0">
        <span class="mb-1 block text-xs text-neutral-500 uppercase">Player</span>
        <ComboboxSelect v-model="playerFilter" :disabled="filtersDisabled" label="Player filter" :options="playerOptions" placeholder="Filter players">
          <template #option="{ option }">
            <span class="min-w-0 flex items-center gap-2">
              <span aria-hidden="true" class="i-mingcute-user-3-line shrink-0 text-base text-violet-300" />
              <span class="min-w-0 flex flex-col">
                <span class="truncate">{{ option.label }}</span>
                <span class="truncate text-xs text-neutral-500">{{ option.description }}</span>
              </span>
            </span>
          </template>
        </ComboboxSelect>
      </div>
      <div v-if="!filtersDisabled" class="col-span-2">
        <RecordingCalendarFilter v-model="timeRange" :replays="scopedReplays" />
      </div>
    </div>

    <ResourceBrowserState
      v-if="catalog.isLoading.value"
      kind="loading"
      message="Reading server instances, players, and replay metadata."
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
      v-else-if="catalog.replays.value.length === 0"
      action-label="Reload recordings"
      kind="empty"
      message="The service is available, but it did not find any readable replay artifacts."
      title="No recordings available"
      @action="catalog.load"
    />
    <!-- NOTICE: Virtua's Vue binding derives its stable item identity from the single slot root's
         key. See `https://github.com/inokawa/virtua/blob/dc92d9d6485df2578e10f5acb06875c69d1bda3b/src/vue/utils.ts#L7-L16`. -->
    <VList v-else-if="filteredReplays.length" :data="filteredReplays" :item-size="68" class="min-h-0 flex-1">
      <template #default="{ item: replay }">
        <ReplayResourceRow
          :key="replay.connectionId"
          :active="catalog.selectedReplay.value?.connectionId === replay.connectionId"
          :replay="replay"
          @select="select(replay)"
        />
      </template>
    </VList>
    <ResourceBrowserState
      v-else
      action-label="Clear filters"
      kind="filtered"
      message="Try another server, player, or recording date range."
      title="No recordings match these filters"
      @action="clearFilters"
    />
  </section>
</template>
