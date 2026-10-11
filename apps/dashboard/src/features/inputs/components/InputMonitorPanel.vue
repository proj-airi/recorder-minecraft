<script setup lang="ts">
import type { PlayPlacement } from '../../timeline/domain'
import type { PlayerInputStatus } from './PlayerInputCard.vue'

import { VList } from 'virtua/vue'
import { computed, shallowRef } from 'vue'

import PlayerInputCard from './PlayerInputCard.vue'

import { useEditorWorkspaceContext } from '../../editor/composables/useEditorWorkspaceContext'
import { latestAtOrBefore } from '../../event-log/captureEvents'
import { useCaptureEventSources } from '../../event-log/useCaptureEvents'
import { placementContainsTick, playServerTickAt } from '../../timeline/ticks'

const { episode, session } = useEditorWorkspaceContext()
const showAll = shallowRef(true)

const handles = useCaptureEventSources(() => episode().placements.map(placement => placement.source.eventsUrl))
const lanes = computed(() => episode().tracks.flatMap(track => track.role === 'primary' ? [track] : []))
const sessionLabels = computed(() => new Map(episode().sessions.map(candidate => [candidate.key, candidate.label])))
const multipleSessions = computed(() => episode().sessions.length > 1)
const selectedLaneId = computed(() => {
  const segment = episode().segments.find(candidate => candidate.id === session.selectedSegmentId.value)
  const placement = segment?.placementId ? episode().placements.find(candidate => candidate.id === segment.placementId) : undefined
  return placement?.laneId ?? null
})
/** The lane the single-player view follows: the selected lane, else the first lane. */
const focusLaneId = computed(() => selectedLaneId.value ?? lanes.value[0]?.id ?? null)
const placementsByLane = computed(() => {
  const byLane = new Map<string, PlayPlacement[]>()
  for (const placement of episode().placements)
    byLane.set(placement.laneId, [...(byLane.get(placement.laneId) ?? []), placement])
  return byLane
})
const colors = computed(() => new Map(episode().segments.flatMap(segment => segment.placementId ? [[segment.placementId, segment.color] as const] : [])))

/** The clip of a lane under the playhead; the latest start wins on overlap (as `placementAt`). */
function placementOnLane(laneId: string, tick: number): null | PlayPlacement {
  let found: null | PlayPlacement = null
  for (const placement of placementsByLane.value.get(laneId) ?? []) {
    if (placementContainsTick(placement, tick) && (!found || placement.startTick >= found.startTick))
      found = placement
  }
  return found
}

// Each lane reads the clip under the playhead and maps the playhead through that placement, so
// trimmed, cut, and moved clips show the Server tick they actually display.
const cards = computed(() => {
  const playhead = session.playheadTick.value
  return lanes.value
    .filter(lane => showAll.value || lane.id === focusLaneId.value)
    .map((lane) => {
      const placement = placementOnLane(lane.id, playhead)
      const firstPlacement = placementsByLane.value.get(lane.id)?.[0]
      const base = {
        color: colors.value.get(placement?.id ?? firstPlacement?.id ?? '') ?? '#a3a3a3',
        error: null as null | string,
        hotbar: null,
        laneId: lane.id,
        playerName: lane.playerName,
        sample: null,
        selected: lane.id === selectedLaneId.value,
        serverTick: null as null | number,
        sessionLabel: multipleSessions.value ? sessionLabels.value.get(lane.sessionKey) : undefined,
        status: 'idle' as PlayerInputStatus,
      }
      if (!placement)
        return base
      const serverTick = playServerTickAt(placement, playhead)
      const url = placement.source.eventsUrl
      const handle = url ? handles.value.get(url) : undefined
      if (!url || !handle)
        return { ...base, serverTick, status: 'no-events' as PlayerInputStatus }
      if (handle.status.value !== 'ready' || !handle.data.value) {
        return { ...base, error: handle.error.value, serverTick, status: handle.status.value as PlayerInputStatus }
      }
      const parsed = handle.data.value
      const sample = latestAtOrBefore(parsed.controlSamples, serverTick)
      const clicks = parsed.clicksByTick.get(serverTick)
      return {
        ...base,
        hotbar: latestAtOrBefore(parsed.hotbarSamples, serverTick),
        sample: sample && clicks ? { ...sample, ...clicks } : sample,
        serverTick,
        status: 'ready' as PlayerInputStatus,
      }
    })
})
</script>

<template>
  <section class="h-full min-h-0 flex flex-col bg-neutral-950" aria-label="Input monitor">
    <div v-if="lanes.length === 0" class="m-auto max-w-xs p-4 text-center text-sm text-neutral-500">
      Add Plays to the timeline to inspect each player's inputs.
    </div>
    <template v-else>
      <div class="flex items-center gap-1 border-b border-[var(--dashboard-border-color)] px-2 py-1">
        <span class="text-[10px] text-neutral-500">{{ lanes.length }} {{ lanes.length === 1 ? 'player' : 'players' }}</span>
        <div class="ml-auto flex rounded bg-white/5 p-0.5" role="group" aria-label="Players shown">
          <button
            aria-label="Show all players"
            :aria-pressed="showAll"
            class="border-0 rounded px-1.5 py-0.5 text-[10px]"
            :class="showAll ? 'bg-white/12 text-neutral-100' : 'bg-transparent text-neutral-500 hover:text-neutral-200'"
            type="button"
            title="Every player on the timeline"
            @click="showAll = true"
          >
            All
          </button>
          <button
            aria-label="Show the selected player"
            :aria-pressed="!showAll"
            class="border-0 rounded px-1.5 py-0.5 text-[10px]"
            :class="!showAll ? 'bg-white/12 text-neutral-100' : 'bg-transparent text-neutral-500 hover:text-neutral-200'"
            title="Only the player of the selected clip"
            type="button"
            @click="showAll = false"
          >
            Selected
          </button>
        </div>
      </div>
      <!-- Many lanes (stress drafts have hundreds) stay cheap: only visible cards are mounted. -->
      <VList :data="cards" :item-size="120" class="min-h-0 flex-1">
        <template #default="{ item: card }">
          <div :key="card.laneId" class="px-1.5 pt-1.5">
            <PlayerInputCard
              :color="card.color"
              :error="card.error"
              :hotbar="card.hotbar"
              :player-name="card.playerName"
              :sample="card.sample"
              :selected="card.selected"
              :server-tick="card.serverTick"
              :session-label="card.sessionLabel"
              :status="card.status"
            />
          </div>
        </template>
      </VList>
    </template>
  </section>
</template>
