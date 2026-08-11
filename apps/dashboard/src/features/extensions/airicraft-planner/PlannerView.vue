<script setup lang="ts">
import type { PlannerCallRecord } from './module'

import { computed } from 'vue'

import PlannerJsonSection from './PlannerJsonSection.vue'

import { useEditorWorkspaceContext } from '../../editor/composables/useEditorWorkspaceContext'

const context = useEditorWorkspaceContext()
const selection = computed(() => context.selectedExtension.value)
const call = computed(() => selection.value?.item.data as PlannerCallRecord | undefined)
const modelLabel = computed(() => [call.value?.model?.provider, call.value?.model?.name].filter(Boolean).join(' / ') || 'Unknown model')
const attemptLabel = computed(() => [
  call.value?.plannerAttempt?.generation && `generation ${call.value.plannerAttempt.generation}`,
  call.value?.plannerAttempt?.attempt !== undefined && `attempt ${call.value.plannerAttempt.attempt}`,
  call.value?.plannerAttempt?.phase,
].filter(Boolean).join(' · ') || 'Unknown attempt')
</script>

<template>
  <div class="h-full overflow-auto bg-[#171717] p-4 text-neutral-200">
    <div v-if="call && selection" class="mx-auto max-w-5xl flex flex-col gap-3">
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
        <span class="rounded bg-violet-400/10 px-2 py-1 text-[10px] text-violet-200 uppercase">
          {{ call.outcome?.status ?? 'incomplete' }}
        </span>
      </header>

      <dl class="grid grid-cols-[max-content_1fr] m-0 gap-x-3 gap-y-1 border border-white/8 rounded bg-white/2 p-3 text-xs">
        <dt class="text-neutral-500">
          Play Server tick
        </dt>
        <dd class="m-0 font-mono">
          {{ selection.playServerTick ?? 'outside placement' }}
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

      <div class="grid gap-3 xl:grid-cols-2">
        <PlannerJsonSection label="Request messages" :value="call.request?.messages" />
        <PlannerJsonSection label="Request tools" :value="call.request?.tools" />
        <PlannerJsonSection label="Assistant content" :value="call.outcome?.assistantContent" />
        <PlannerJsonSection label="Tool calls" :value="call.outcome?.toolCalls" />
        <PlannerJsonSection label="Usage" :value="call.outcome?.usage" />
        <PlannerJsonSection label="Timeline anchors" :value="call.timeline" />
      </div>
    </div>

    <div v-else class="h-full flex flex-col items-center justify-center gap-2 text-center text-neutral-500">
      <span aria-hidden="true" class="i-mingcute-ai-line text-2xl" />
      <p class="m-0 text-xs">
        Select a planner call on the timeline.
      </p>
    </div>
  </div>
</template>
