<script setup lang="ts">
import type { RecordRef } from '../formats'
import type { VisibilityPayload } from '../payload'

import { computed } from 'vue'

import AboutEvidence from './AboutEvidence.vue'
import RecordLink from './RecordLink.vue'

import { dimensionName, formatPos } from '../labels'

const props = defineProps<{
  payload: VisibilityPayload
}>()

const emit = defineEmits<{
  seek: [record: RecordRef]
}>()

const run = computed(() => props.payload.run)
const target = computed(() => props.payload.target)
const visible = computed(() => run.value.state === 'visible')
const expectedSamples = computed(() => props.payload.intervalTicks > 0
  ? Math.floor((run.value.lastTick - run.value.startTick) / props.payload.intervalTicks) + 1
  : null)

function sampleRef(serverTick: number): RecordRef {
  return { connectionId: props.payload.connectionId, sequence: 0, serverTick, stream: 'PERCEPTION' }
}

function perSample(total: number): string {
  return (total / Math.max(1, run.value.samples)).toFixed(1)
}
</script>

<template>
  <section class="flex flex-col gap-3" aria-label="Visibility interval details">
    <header class="border-b border-white/8 pb-3">
      <p class="m-0 text-[10px] tracking-wider uppercase" :class="visible ? 'text-emerald-300' : 'text-stone-300'">
        Reconstructed visibility · {{ visible ? 'visible' : 'undetermined' }}
      </p>
      <h2 class="m-0 mt-1 text-sm font-semibold" data-testid="visibility-title">
        {{ payload.targetLabel }} {{ visible ? 'visible' : 'undetermined' }} to {{ payload.actorName }}
      </h2>
      <p class="m-0 mt-1 text-xs text-neutral-400">
        <template v-if="visible">
          At least one sight ray reached the target through known non-occluding cells in every sample of this interval.
        </template>
        <template v-else>
          In view and in range, but every unblocked ray crossed an unknown cell. This is not evidence of absence.
        </template>
      </p>
    </header>

    <dl class="grid grid-cols-[max-content_1fr] m-0 items-center gap-x-3 gap-y-1.5 border border-white/8 rounded bg-white/2 p-3 text-xs">
      <dt class="text-neutral-500">
        Target
      </dt>
      <dd class="m-0">
        <span class="font-mono">{{ target.typeId }}</span>
        <template v-if="target.uuid">
          · <span class="font-mono">{{ target.uuid }}</span>
        </template>
        <template v-if="target.blockPos">
          · <span class="font-mono">{{ formatPos(target.blockPos) }}</span> in {{ dimensionName(target.dimension ?? '') }}
        </template>
      </dd>
      <dt class="text-neutral-500">
        First sample
      </dt>
      <dd class="m-0">
        <RecordLink :actor="payload.actorName" :record="sampleRef(run.startTick)" @seek="emit('seek', $event)" />
      </dd>
      <dt class="text-neutral-500">
        Last sample
      </dt>
      <dd class="m-0">
        <RecordLink :actor="payload.actorName" :record="sampleRef(run.lastTick)" @seek="emit('seek', $event)" />
      </dd>
      <dt class="text-neutral-500">
        Samples
      </dt>
      <dd class="m-0 font-mono" data-testid="visibility-samples">
        {{ run.samples }}<template v-if="expectedSamples !== null">
          of {{ expectedSamples }} expected at {{ payload.intervalTicks }}-tick sampling
        </template>
      </dd>
      <dt class="text-neutral-500">
        Distance
      </dt>
      <dd class="m-0 font-mono">
        {{ run.minDistance.toFixed(2) }}–{{ run.maxDistance.toFixed(2) }} blocks
      </dd>
    </dl>

    <section class="flex flex-col gap-2 border border-white/8 rounded bg-white/2 p-3 text-xs" data-testid="ray-support">
      <h3 class="m-0 text-[11px] text-neutral-300 font-semibold">
        Ray support (sum over {{ run.samples }} samples, mean per sample)
      </h3>
      <table class="w-full border-collapse font-mono">
        <thead>
          <tr class="text-left text-[10px] text-neutral-500 font-sans uppercase">
            <th class="py-1 font-normal">
              Outcome
            </th>
            <th class="py-1 text-right font-normal">
              Total
            </th>
            <th class="py-1 text-right font-normal">
              Per sample
            </th>
          </tr>
        </thead>
        <tbody>
          <tr class="border-t border-white/6">
            <td class="py-1 font-sans">
              Sample points
            </td><td class="text-right">
              {{ run.support.points }}
            </td><td class="text-right">
              {{ perSample(run.support.points) }}
            </td>
          </tr>
          <tr class="border-t border-white/6">
            <td class="py-1 font-sans">
              In view (cast)
            </td><td class="text-right">
              {{ run.support.inView }}
            </td><td class="text-right">
              {{ perSample(run.support.inView) }}
            </td>
          </tr>
          <tr class="border-t border-white/6 text-emerald-200">
            <td class="py-1 font-sans">
              Clear
            </td><td class="text-right">
              {{ run.support.clear }}
            </td><td class="text-right">
              {{ perSample(run.support.clear) }}
            </td>
          </tr>
          <tr class="border-t border-white/6">
            <td class="py-1 font-sans">
              Blocked
            </td><td class="text-right">
              {{ run.support.blocked }}
            </td><td class="text-right">
              {{ perSample(run.support.blocked) }}
            </td>
          </tr>
          <tr class="border-t border-white/6 text-stone-300">
            <td class="py-1 font-sans">
              Unknown cell
            </td><td class="text-right">
              {{ run.support.unknown }}
            </td><td class="text-right">
              {{ perSample(run.support.unknown) }}
            </td>
          </tr>
        </tbody>
      </table>
    </section>

    <AboutEvidence :perception="payload.header" />
  </section>
</template>
