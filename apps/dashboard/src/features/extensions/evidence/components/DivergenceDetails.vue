<script setup lang="ts">
import type { CoPresentEntity, PerceptionHeader, RecordRef } from '../formats'
import type { DivergencePayload } from '../payload'

import { computed } from 'vue'

import AboutEvidence from './AboutEvidence.vue'
import RecordLink from './RecordLink.vue'
import SlotList from './SlotList.vue'

import { dimensionName, DIVERGENCE_END_LABELS, formatPos, isCoverageEnd, shortId, VISIBILITY_LABELS } from '../labels'

const props = defineProps<{
  payload: DivergencePayload
  perception?: PerceptionHeader | string | null
}>()

const emit = defineEmits<{
  seek: [record: RecordRef]
}>()

const divergence = computed(() => props.payload.divergence)
const alignment = computed(() => props.payload.alignment)
const coPresence = computed(() => divergence.value.coPresence)
const open = computed(() => divergence.value.endTick === undefined)

function actorOf(connectionId: string): string | undefined {
  return connectionId ? alignment.value.participantsByConnection.get(connectionId)?.playerName || undefined : undefined
}

function entityName(entity: CoPresentEntity): string {
  const participant = (entity.participantConnectionId && alignment.value.participantsByConnection.get(entity.participantConnectionId))
    || (entity.uuid && alignment.value.participantsByUuid.get(entity.uuid))
  if (participant && participant.playerName)
    return participant.playerName
  return `${shortId(entity.typeId) || 'entity'}${entity.uuid ? ` ${entity.uuid.slice(0, 8)}` : ''}`
}

interface EntityRow {
  entity: CoPresentEntity
  isParticipant: boolean
  name: string
}

const entities = computed<EntityRow[]>(() => coPresence.value.entities.map(entity => ({
  entity,
  isParticipant: Boolean(entity.participantConnectionId),
  name: entityName(entity),
})))

const witnesses = computed(() => entities.value.filter(row => row.entity.visibility === 'VISIBLE' && row.entity.participantContainerOpen === true))

/** One factual sentence about the co-presence sample, so two runs can be compared at a glance. */
const summary = computed(() => {
  const actor = props.payload.actorName
  const status = coPresence.value.status
  if (status === 'PERCEPTION_NOT_PROVIDED')
    return `No perception.jsonl was given for ${actor}; co-presence at the start tick is unknown, not absent.`
  if (status === 'NO_SAMPLE')
    return `No perception sample of ${actor} lies within one sampling interval before the start tick.`
  if (status !== 'SAMPLED')
    return 'Co-presence was not recorded.'

  const container = `The container was ${VISIBILITY_LABELS[coPresence.value.container]} to ${actor}`
  const visibleParticipants = entities.value.filter(row => row.isParticipant && row.entity.visibility === 'VISIBLE')
  if (witnesses.value.length > 0)
    return `${container}. ${witnesses.value.map(row => row.name).join(', ')} ${witnesses.value.length > 1 ? 'were' : 'was'} visible to ${actor} with this container open.`
  if (visibleParticipants.length > 0)
    return `${container}. ${visibleParticipants.map(row => row.name).join(', ')} visible to ${actor}, without this container open.`
  return `${container}. No other participant was visible to ${actor}.`
})

const visibilityClass: Record<string, string> = {
  NOT_VISIBLE: 'border-white/10 bg-white/4 text-neutral-300',
  UNDETERMINED: 'border-stone-400/40 bg-stone-500/15 text-stone-200',
  UNSPECIFIED: 'border-white/10 bg-white/4 text-neutral-400',
  VISIBLE: 'border-emerald-400/50 bg-emerald-500/15 text-emerald-100',
}
</script>

<template>
  <section class="flex flex-col gap-3" aria-label="Observation divergence details">
    <header class="border-b border-white/8 pb-3">
      <p class="m-0 text-[10px] text-rose-300 tracking-wider uppercase">
        Observation divergence
      </p>
      <h2 class="m-0 mt-1 text-sm font-semibold" data-testid="divergence-title">
        {{ payload.actorName }} · {{ payload.containerLabel }}
      </h2>
      <p class="m-0 mt-1 text-xs text-neutral-400">
        World contents differed from the contents {{ payload.actorName }} last observed in this container.
      </p>
    </header>

    <dl class="grid grid-cols-[max-content_1fr] m-0 items-center gap-x-3 gap-y-1.5 border border-white/8 rounded bg-white/2 p-3 text-xs">
      <dt class="text-neutral-500">
        Actor
      </dt>
      <dd class="m-0">
        {{ payload.actorName }} <span class="text-neutral-500 font-mono">{{ divergence.connectionId.slice(0, 8) }}</span>
      </dd>
      <dt class="text-neutral-500">
        Container
      </dt>
      <dd class="m-0">
        <span class="font-mono">{{ formatPos(divergence.blockPos) }}</span> in {{ dimensionName(divergence.dimension) }}
      </dd>
      <dt class="text-neutral-500">
        Start
      </dt>
      <dd class="m-0 flex flex-wrap items-center gap-2">
        <span class="font-mono" data-testid="divergence-start">tick {{ divergence.startTick }}</span>
        <RecordLink v-for="source in divergence.truth.sources.slice(0, 1)" :key="`start-${source.sequence}`" :record="source" @seek="emit('seek', $event)" />
      </dd>
      <dt class="text-neutral-500">
        Last differing
      </dt>
      <dd class="m-0 font-mono">
        tick {{ divergence.lastTick }}
      </dd>
      <dt class="text-neutral-500">
        End
      </dt>
      <dd class="m-0 flex flex-wrap items-center gap-2" data-testid="divergence-end">
        <span v-if="open" class="text-orange-200">open: {{ DIVERGENCE_END_LABELS[divergence.end] }}</span>
        <template v-else>
          <span class="font-mono">tick {{ divergence.endTick }}</span>
          <span :class="isCoverageEnd(divergence.end) ? 'text-orange-200' : 'text-neutral-200'">{{ DIVERGENCE_END_LABELS[divergence.end] }}</span>
        </template>
        <RecordLink v-if="divergence.endSource" :actor="actorOf(divergence.endSource.connectionId)" :record="divergence.endSource" @seek="emit('seek', $event)" />
      </dd>
      <dt class="text-neutral-500">
        Truth changes
      </dt>
      <dd class="m-0 font-mono">
        {{ divergence.truthChanges }} more world snapshot(s) inside the interval
      </dd>
    </dl>

    <div class="grid grid-cols-2 gap-3" aria-label="Contents comparison">
      <section class="flex flex-col gap-2" data-testid="observed-contents">
        <h3 class="m-0 text-[11px] text-neutral-300 font-semibold">
          Last observed by {{ payload.actorName }}
        </h3>
        <div class="flex flex-wrap gap-1">
          <RecordLink v-for="source in divergence.observed.sources" :key="`${source.serverTick}:${source.sequence}`" :actor="actorOf(source.connectionId)" :record="source" @seek="emit('seek', $event)" />
        </div>
        <SlotList :compare-with="divergence.truth.slots" :slots="divergence.observed.slots" />
      </section>
      <section class="flex flex-col gap-2" data-testid="truth-contents">
        <h3 class="m-0 text-[11px] text-neutral-300 font-semibold">
          World contents at tick {{ divergence.startTick }}
        </h3>
        <div class="flex flex-wrap gap-1">
          <RecordLink v-for="source in divergence.truth.sources" :key="`${source.serverTick}:${source.sequence}`" :record="source" @seek="emit('seek', $event)" />
        </div>
        <SlotList :compare-with="divergence.observed.slots" :slots="divergence.truth.slots" />
      </section>
    </div>

    <section class="flex flex-col gap-2 border border-white/8 rounded bg-white/2 p-3" aria-label="Co-presence at start" data-testid="co-presence">
      <header class="flex flex-wrap items-center justify-between gap-2">
        <h3 class="m-0 text-[11px] text-neutral-300 font-semibold">
          Co-presence at tick {{ divergence.startTick }} ({{ payload.actorName }}'s perception)
        </h3>
        <RecordLink v-if="coPresence.sample" :actor="actorOf(coPresence.sample.connectionId)" :record="coPresence.sample" @seek="emit('seek', $event)" />
      </header>

      <p
        class="m-0 border rounded px-2 py-1.5 text-xs"
        :class="witnesses.length ? 'border-amber-300/50 bg-amber-400/12 text-amber-50' : 'border-white/10 bg-white/4 text-neutral-200'"
        data-testid="co-presence-summary"
      >
        {{ summary }}
      </p>

      <template v-if="coPresence.status === 'SAMPLED'">
        <div class="flex flex-wrap items-center gap-2 text-xs">
          <span class="text-neutral-500">Container</span>
          <span class="border rounded px-1.5 py-0.5 font-semibold uppercase" :class="visibilityClass[coPresence.container]" data-testid="co-presence-container">
            {{ VISIBILITY_LABELS[coPresence.container] }}
          </span>
        </div>
        <table v-if="entities.length" class="w-full border-collapse text-xs">
          <thead>
            <tr class="text-left text-[10px] text-neutral-500 uppercase">
              <th class="py-1 font-normal">
                Entity
              </th>
              <th class="py-1 font-normal">
                Visibility
              </th>
              <th class="py-1 font-normal">
                Had this container open
              </th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in entities" :key="row.entity.uuid ?? row.name" class="border-t border-white/6" data-testid="co-present-entity">
              <td class="py-1.5">
                {{ row.name }}
                <span v-if="!row.isParticipant" class="text-neutral-500">(not a participant)</span>
              </td>
              <td class="py-1.5">
                <span class="border rounded px-1.5 py-0.5 text-[10px] uppercase" :class="visibilityClass[row.entity.visibility]">
                  {{ VISIBILITY_LABELS[row.entity.visibility] }}
                </span>
              </td>
              <td class="py-1.5">
                <span v-if="row.entity.participantContainerOpen === true" class="flex flex-wrap items-center gap-2 text-amber-100 font-semibold">
                  yes
                  <RecordLink v-if="row.entity.participantMenu" :actor="row.name" :record="row.entity.participantMenu" @seek="emit('seek', $event)" />
                </span>
                <span v-else-if="row.entity.participantContainerOpen === false" class="text-neutral-300">no</span>
                <span v-else class="text-neutral-500">n/a</span>
              </td>
            </tr>
          </tbody>
        </table>
        <p v-else class="m-0 text-xs text-neutral-400" data-testid="co-presence-no-entities">
          No player or other entity was visible or undetermined in this sample.
        </p>
      </template>
    </section>

    <AboutEvidence :alignment="alignment.header" :perception="perception" />
  </section>
</template>
