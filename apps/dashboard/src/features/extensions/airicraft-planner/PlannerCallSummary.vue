<script setup lang="ts">
import type { PlannerCallRecord } from './module'

import { computed } from 'vue'

const props = defineProps<{
  call: PlannerCallRecord
  playServerTick: null | number
}>()

const emit = defineEmits<{
  showDetails: []
}>()

const modelLabel = computed(() => [props.call.model?.provider, props.call.model?.name].filter(Boolean).join(' / ') || 'Unknown model')
const attemptLabel = computed(() => [
  props.call.plannerAttempt?.generation && `generation ${props.call.plannerAttempt.generation}`,
  props.call.plannerAttempt?.attempt !== undefined && `attempt ${props.call.plannerAttempt.attempt}`,
  props.call.plannerAttempt?.phase,
].filter(Boolean).join(' · ') || 'Unknown attempt')
</script>

<template>
  <section class="flex flex-col gap-3">
    <header class="flex flex-wrap items-start justify-between gap-3 border-b border-white/8 pb-3">
      <div class="min-w-0">
        <p class="m-0 text-[10px] text-violet-300 tracking-wider uppercase">
          Airicraft planner call {{ call.sequence }}
        </p>
        <h2 class="m-0 mt-1 truncate text-sm font-semibold">
          {{ call.callId }}
        </h2>
        <p class="m-0 mt-1 text-xs text-neutral-400">
          {{ modelLabel }} · {{ attemptLabel }}
        </p>
      </div>
      <div class="flex items-center gap-2">
        <button
          type="button"
          class="flex items-center gap-1.5 border border-white/10 rounded bg-white/4 px-2 py-1 text-[11px] text-neutral-300 hover:border-white/20 hover:bg-white/8 hover:text-white"
          @click="emit('showDetails')"
        >
          <span aria-hidden="true" class="i-mingcute-information-line" />
          Details
        </button>
        <span class="rounded bg-violet-400/10 px-2 py-1 text-[10px] text-violet-200 uppercase">
          {{ call.outcome?.status ?? 'incomplete' }}
        </span>
      </div>
    </header>

    <dl class="grid grid-cols-[max-content_1fr] m-0 gap-x-3 gap-y-1 border border-white/8 rounded bg-white/2 p-3 text-xs">
      <dt class="text-neutral-500">
        Play Server tick
      </dt>
      <dd class="m-0 font-mono">
        {{ playServerTick ?? 'outside placement' }}
      </dd>
      <dt class="text-neutral-500">
        Submitted
      </dt>
      <dd class="m-0 font-mono">
        {{ call.timeline.submitted.serverTick }}
      </dd>
      <dt class="text-neutral-500">
        Completed
      </dt>
      <dd class="m-0 font-mono">
        {{ call.timeline.completed?.serverTick ?? 'not completed' }}
      </dd>
      <dt class="text-neutral-500">
        Applied
      </dt>
      <dd class="m-0 font-mono">
        {{ call.timeline.applied?.serverTick ?? 'not applied' }}
      </dd>
      <dt class="text-neutral-500">
        Latency
      </dt>
      <dd class="m-0 font-mono">
        {{ call.timing?.latencyMs ? `${call.timing.latencyMs} ms` : 'unknown' }}
      </dd>
    </dl>
  </section>
</template>
