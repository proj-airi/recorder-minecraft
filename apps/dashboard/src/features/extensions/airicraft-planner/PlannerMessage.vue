<script setup lang="ts">
import type { PlannerTranscriptEntry } from './transcript'

import { computed } from 'vue'

const props = defineProps<{
  entry: PlannerTranscriptEntry
}>()

const roleLabel = computed(() => props.entry.source === 'response' ? 'Model response' : props.entry.role)
const roleClass = computed(() => ({
  assistant: 'border-violet-400/20 bg-violet-400/6',
  system: 'border-sky-400/20 bg-sky-400/6',
  tool: 'border-amber-400/20 bg-amber-400/6',
  unknown: 'border-white/10 bg-white/3',
  user: 'border-emerald-400/20 bg-emerald-400/6',
})[props.entry.role])
</script>

<template>
  <li>
    <article class="min-w-0 border rounded p-3" :class="roleClass">
      <header class="mb-2 flex flex-wrap items-center gap-2">
        <span class="text-[10px] text-neutral-300 font-semibold tracking-wider uppercase">
          {{ roleLabel }}
        </span>
        <span v-if="entry.toolCallId" class="truncate text-[10px] text-neutral-500 font-mono">
          {{ entry.toolCallId }}
        </span>
      </header>

      <div class="flex flex-col gap-2">
        <template v-for="(block, index) in entry.content" :key="index">
          <p v-if="block.kind === 'text'" class="m-0 whitespace-pre-wrap break-words text-xs text-neutral-200 leading-5">
            {{ block.text }}
          </p>
          <div v-else-if="block.kind === 'image'" class="flex items-center gap-2 rounded bg-black/20 px-2 py-1.5 text-xs text-neutral-400">
            <span aria-hidden="true" class="i-mingcute-pic-line" />
            <span>Image input{{ block.detail ? ` · ${block.detail} detail` : '' }}</span>
          </div>
          <pre v-else class="m-0 overflow-auto whitespace-pre-wrap break-words rounded bg-black/25 p-2 text-[11px] text-neutral-300 leading-5 font-mono">{{ JSON.stringify(block.value, null, 2) }}</pre>
        </template>

        <section v-for="(toolCall, index) in entry.toolCalls" :key="toolCall.id ?? `${toolCall.name}-${index}`" class="overflow-hidden border border-amber-400/15 rounded bg-black/20">
          <header class="flex items-center gap-2 border-b border-white/6 px-2 py-1.5 text-xs">
            <span aria-hidden="true" class="i-mingcute-tools-line text-amber-300" />
            <span class="font-medium">{{ toolCall.name }}</span>
            <span v-if="toolCall.id" class="min-w-0 truncate text-[10px] text-neutral-500 font-mono">{{ toolCall.id }}</span>
          </header>
          <pre class="m-0 overflow-auto whitespace-pre-wrap break-words p-2 text-[11px] text-neutral-300 leading-5 font-mono">{{ toolCall.arguments }}</pre>
        </section>

        <details v-if="entry.reasoning" class="rounded bg-black/20 px-2 py-1.5 text-xs">
          <summary class="cursor-pointer select-none text-neutral-400">
            Reasoning
          </summary>
          <p class="m-0 mt-2 whitespace-pre-wrap break-words text-neutral-300 leading-5">
            {{ entry.reasoning }}
          </p>
        </details>

        <p v-if="entry.content.length === 0 && entry.toolCalls.length === 0 && !entry.reasoning" class="m-0 text-xs text-neutral-500 italic">
          No content
        </p>
      </div>
    </article>
  </li>
</template>
