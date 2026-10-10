<script setup lang="ts">
import type { PerceptionHeader, RecordRef } from './formats'
import type { EvidencePayload } from './payload'
import type { VisibilityRowMode } from './providers'

import { computed, inject } from 'vue'

import DivergenceDetails from './components/DivergenceDetails.vue'
import EvidenceSourcesOverview from './components/EvidenceSourcesOverview.vue'
import InteractionDetails from './components/InteractionDetails.vue'
import VisibilityDetails from './components/VisibilityDetails.vue'
import WorldEventDetails from './components/WorldEventDetails.vue'

import { useEditorWorkspaceContext } from '../../editor/composables/useEditorWorkspaceContext'
import { placementContainsServerTick, serverTickToEpisodeTick } from '../../timeline/ticks'
import { isEvidencePayload, isEvidenceProvider } from './payload'
import { evidenceSettings } from './providers'
import { evidenceSources, evidenceSourcesKey } from './sources'

const context = useEditorWorkspaceContext()
const sources = inject(evidenceSourcesKey, evidenceSources)

const selection = computed(() => {
  const selected = context.selectedDataItem.value
  if (!selected || !isEvidenceProvider(selected.track.providerId) || !isEvidencePayload(selected.item.payload))
    return null
  return { payload: selected.item.payload as EvidencePayload, selected }
})

const divergencePerception = computed<PerceptionHeader | string | null>(() => {
  const payload = selection.value?.payload
  if (payload?.evidence !== 'divergence')
    return null
  if (!payload.perceptionUrl)
    return `No perception.jsonl was given for ${payload.actorName}'s Play.`
  const state = sources.perceptionState(payload.perceptionUrl)
  if (state.status === 'ready')
    return state.value.header ?? 'perception.jsonl has no header.'
  if (state.status === 'loading')
    return 'Loading perception assumptions…'
  return state.status === 'missing' ? 'perception.jsonl is missing.' : `perception.jsonl could not be read: ${state.error}`
})

const rowMode = computed({
  get: () => evidenceSettings.value.visibilityRows,
  set: (visibilityRows: VisibilityRowMode) => {
    evidenceSettings.value = { ...evidenceSettings.value, visibilityRows }
  },
})

/** Seeks to a source record: through the clip of its Play when one shows it, else the session anchor. */
function seek(record: RecordRef): void {
  const current = selection.value?.selected
  const episode = context.episode()
  let tick: null | number = null
  if (record.connectionId) {
    const placement = episode.placements.find(candidate => candidate.connectionId === record.connectionId && placementContainsServerTick(candidate, record.serverTick))
    if (placement)
      tick = serverTickToEpisodeTick(placement, record.serverTick)
  }
  if (tick === null) {
    const sessionKey = current?.session?.key ?? current?.placement?.sessionKey ?? current?.track.target.sessionKey
    const session = episode.sessions.find(candidate => candidate.key === sessionKey)
    if (session)
      tick = serverTickToEpisodeTick(session, record.serverTick)
  }
  if (tick !== null)
    context.session.seekToTick(tick)
}
</script>

<template>
  <div class="h-full flex flex-col bg-[#171717] text-neutral-200" aria-label="Evidence">
    <div class="flex shrink-0 items-center justify-between gap-2 border-b border-white/8 px-3 py-1.5 text-[11px]">
      <span class="text-neutral-400">Facts from world-events, session alignment, and perception files.</span>
      <div class="flex items-center gap-1" role="group" aria-label="Visibility rows">
        <span class="mr-1 text-neutral-500">Visibility rows</span>
        <button
          type="button"
          class="border rounded px-1.5 py-0.5"
          :class="rowMode === 'aligned' ? 'border-sky-300/50 bg-sky-400/15 text-sky-100' : 'border-white/10 text-neutral-400 hover:text-neutral-200'"
          :aria-pressed="rowMode === 'aligned'"
          title="Only other participants and containers named in the session alignment"
          @click="rowMode = 'aligned'"
        >
          Aligned targets
        </button>
        <button
          type="button"
          class="border rounded px-1.5 py-0.5"
          :class="rowMode === 'all' ? 'border-sky-300/50 bg-sky-400/15 text-sky-100' : 'border-white/10 text-neutral-400 hover:text-neutral-200'"
          :aria-pressed="rowMode === 'all'"
          title="Every perceived target (players first, at most 24 rows per player)"
          @click="rowMode = 'all'"
        >
          All targets
        </button>
      </div>
    </div>
    <div class="min-h-0 flex-1 overflow-auto p-4">
      <div v-if="selection" class="mx-auto max-w-4xl">
        <DivergenceDetails v-if="selection.payload.evidence === 'divergence'" :payload="selection.payload" :perception="divergencePerception" @seek="seek" />
        <WorldEventDetails v-else-if="selection.payload.evidence === 'world-event'" :payload="selection.payload" @seek="seek" />
        <VisibilityDetails v-else-if="selection.payload.evidence === 'visibility'" :payload="selection.payload" @seek="seek" />
        <InteractionDetails v-else :payload="selection.payload" @seek="seek" />
      </div>
      <EvidenceSourcesOverview v-else :episode="context.episode()" :sources="sources" />
    </div>
  </div>
</template>
