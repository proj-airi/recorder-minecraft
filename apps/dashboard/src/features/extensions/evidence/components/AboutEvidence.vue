<script setup lang="ts">
import type { AlignmentHeader, PerceptionHeader } from '../formats'

defineProps<{
  alignment?: AlignmentHeader | null
  /** Perception header of the actor's Play; a string explains why it is not shown. */
  perception?: PerceptionHeader | string | null
}>()

/** `name: explanation` -> [name, explanation]. */
function split(entry: string): [string, string] {
  const index = entry.indexOf(': ')
  return index > 0 ? [entry.slice(0, index), entry.slice(index + 2)] : ['', entry]
}
</script>

<template>
  <details class="border border-white/8 rounded bg-white/2 text-xs" aria-label="About this evidence">
    <summary class="cursor-pointer select-none px-3 py-2 text-neutral-300">
      <span aria-hidden="true" class="i-mingcute-information-line mr-1 align-[-2px]" />
      About this evidence
    </summary>
    <div class="flex flex-col gap-3 px-3 pb-3">
      <section v-if="alignment" class="flex flex-col gap-1.5">
        <h4 class="m-0 text-[11px] text-neutral-400 font-semibold tracking-wide uppercase">
          Session alignment
        </h4>
        <p class="m-0 text-neutral-300">
          {{ alignment.processor.name }} v{{ alignment.processor.version }} · scope {{ alignment.scope || 'session' }} · {{ alignment.provenance || 'deterministic transform' }}
        </p>
        <p v-if="alignment.usesFutureContext" class="m-0 border border-amber-400/30 rounded bg-amber-400/8 px-2 py-1 text-amber-100" data-testid="hindsight-flag">
          Uses hindsight: {{ alignment.futureContext || 'some fields read records after the tick they describe.' }}
        </p>
        <p v-else class="m-0 text-neutral-400">
          Uses no records after the tick they describe.
        </p>
        <dl class="m-0 flex flex-col gap-1">
          <template v-for="entry in alignment.assumptions" :key="entry">
            <dt class="text-neutral-200 font-mono">
              {{ split(entry)[0] || 'assumption' }}
            </dt>
            <dd class="m-0 mb-1 text-neutral-400">
              {{ split(entry)[1] }}
            </dd>
          </template>
        </dl>
        <h5 class="m-0 mt-1 text-[11px] text-neutral-400 font-semibold">
          Known limitations
        </h5>
        <ul class="m-0 flex flex-col gap-1 pl-4 text-neutral-400">
          <li v-for="entry in alignment.knownLimitations" :key="entry">
            <span class="text-neutral-200 font-mono">{{ split(entry)[0] }}</span>
            {{ split(entry)[1] }}
          </li>
        </ul>
      </section>

      <section v-if="perception && typeof perception === 'object'" class="flex flex-col gap-1.5">
        <h4 class="m-0 text-[11px] text-neutral-400 font-semibold tracking-wide uppercase">
          Perception (reconstructed)
        </h4>
        <p class="m-0 text-neutral-300">
          {{ perception.processor.name }} v{{ perception.processor.version }} · {{ perception.scope }} · {{ perception.provenance }}
        </p>
        <dl class="grid grid-cols-[max-content_1fr] m-0 gap-x-3 gap-y-1">
          <dt class="text-neutral-500">
            Field of view
          </dt>
          <dd class="m-0 text-neutral-200" data-testid="perception-fov">
            {{ perception.assumptions.camera.verticalFovDegrees }}° vertical, {{ perception.assumptions.camera.horizontalFovDegrees }}° horizontal (aspect {{ perception.assumptions.camera.aspectRatio.toFixed(3) }}), assumed
          </dd>
          <dt class="text-neutral-500">
            Distance limit
          </dt>
          <dd class="m-0 text-neutral-200">
            {{ perception.assumptions.maxDistanceBlocks }} blocks{{ perception.assumptions.maxDistanceCappedByViewDistance ? ', capped by view distance' : '' }}
          </dd>
          <dt class="text-neutral-500">
            Sampling
          </dt>
          <dd class="m-0 text-neutral-200">
            every {{ perception.assumptions.samplingIntervalTicks || '?' }} tick(s), {{ perception.sampleCount }} samples
          </dd>
          <dt class="text-neutral-500">
            Occluders
          </dt>
          <dd class="m-0 text-neutral-200">
            {{ perception.assumptions.occluderModel.name }}
          </dd>
          <dt class="text-neutral-500">
            Visibility rule
          </dt>
          <dd class="m-0 text-neutral-400">
            {{ perception.assumptions.targetSampling.visibilityRule }}
          </dd>
          <dt class="text-neutral-500">
            Unknown cells
          </dt>
          <dd class="m-0 text-neutral-400">
            {{ perception.assumptions.unknownCellPolicy.replace('UNKNOWN_CELL_POLICY_', '').toLowerCase() || 'unspecified' }}
          </dd>
        </dl>
        <h5 class="m-0 mt-1 text-[11px] text-neutral-400 font-semibold">
          Known limitations
        </h5>
        <ul class="m-0 flex flex-col gap-1 pl-4 text-neutral-400">
          <li v-for="entry in perception.knownLimitations" :key="entry">
            <span class="text-neutral-200 font-mono">{{ split(entry)[0] }}</span>
            {{ split(entry)[1] }}
          </li>
        </ul>
      </section>
      <p v-else-if="typeof perception === 'string'" class="m-0 text-neutral-500">
        {{ perception }}
      </p>
    </div>
  </details>
</template>
