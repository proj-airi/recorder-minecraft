<script setup lang="ts">
import type { PlannerCallRecord } from './module'

import { computed } from 'vue'

import PlannerMessage from './PlannerMessage.vue'

import { plannerTranscript } from './transcript'

const props = defineProps<{
  call: PlannerCallRecord
}>()

const entries = computed(() => plannerTranscript(props.call))
</script>

<template>
  <section class="min-w-0">
    <div class="mb-2 flex items-baseline justify-between gap-3">
      <h3 class="m-0 text-[10px] text-neutral-400 font-semibold tracking-wider uppercase">
        Transcript
      </h3>
      <span class="text-[10px] text-neutral-600">
        {{ call.request?.messages?.length ?? 0 }} request messages
      </span>
    </div>

    <ol v-if="entries.length" class="m-0 flex flex-col list-none gap-2 p-0">
      <PlannerMessage v-for="entry in entries" :key="entry.id" :entry="entry" />
    </ol>
    <p v-else class="m-0 border border-white/8 rounded bg-black/20 p-3 text-xs text-neutral-500">
      This call has no transcript content.
    </p>

    <div v-if="call.outcome?.failure" role="alert" class="mt-2 border border-red-400/20 rounded bg-red-400/6 p-3">
      <p class="m-0 text-[10px] text-red-300 font-semibold tracking-wider uppercase">
        {{ call.outcome.failure.type ?? 'Planner failure' }}
      </p>
      <p class="m-0 mt-1 whitespace-pre-wrap break-words text-xs text-red-100 leading-5">
        {{ call.outcome.failure.message }}
      </p>
    </div>
  </section>
</template>
