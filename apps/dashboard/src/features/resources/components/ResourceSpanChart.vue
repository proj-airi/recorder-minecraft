<script setup lang="ts">
import type { PlayerIdentity } from '../../players/avatar'

import { computed } from 'vue'

import PlayerAvatar from '../../players/components/PlayerAvatar.vue'

import { formatDurationTicks } from '../../basic/format'

export interface SpanBar {
  endedAt?: string
  endTick: number
  /** Drawn brighter: the Play this details pane is about. */
  highlight?: boolean
  key: string
  label: string
  player?: PlayerIdentity
  startedAt?: string
  startTick: number
}

const props = defineProps<{
  /** Axis in Server ticks; bars are placed on it by tick, which is how Plays of one session align. */
  axis: { endedAt?: string, endTick: number, startedAt?: string, startTick: number }
  bars: readonly SpanBar[]
}>()

const axisTicks = computed(() => Math.max(1, props.axis.endTick - props.axis.startTick))

function left(tick: number): string {
  return `${Math.max(0, Math.min(100, (tick - props.axis.startTick) / axisTicks.value * 100))}%`
}

function width(bar: SpanBar): string {
  const start = Math.max(bar.startTick, props.axis.startTick)
  const end = Math.min(bar.endTick, props.axis.endTick)
  // A minimum width keeps a very short Play visible as a sliver instead of disappearing.
  return `max(3px, ${Math.max(0, end - start) / axisTicks.value * 100}%)`
}

/** Wall-clock time of day in UTC, e.g. `18:18:22`; the full instant stays in the text rows. */
function clock(value?: string): string {
  const time = value ? Date.parse(value) : Number.NaN
  return Number.isFinite(time) ? new Date(time).toISOString().slice(11, 19) : '—'
}

function barTitle(bar: SpanBar): string {
  return `${bar.label}: ${clock(bar.startedAt)}–${clock(bar.endedAt)} UTC · ${formatDurationTicks(bar.endTick - bar.startTick)} · ticks ${bar.startTick}–${bar.endTick}`
}
</script>

<template>
  <figure class="m-0 flex flex-col gap-1" aria-label="Time span">
    <div class="flex items-baseline justify-between text-[10px] text-neutral-500 tabular-nums">
      <span>{{ clock(axis.startedAt) }}</span>
      <span class="text-neutral-300">{{ formatDurationTicks(axis.endTick - axis.startTick) }}</span>
      <span>{{ clock(axis.endedAt) }}</span>
    </div>
    <ul class="m-0 flex flex-col list-none gap-1 p-0">
      <li v-for="bar in bars" :key="bar.key" class="flex items-center gap-1.5" :title="barTitle(bar)">
        <PlayerAvatar v-if="bar.player" :player="bar.player" :size="14" />
        <span v-else aria-hidden="true" class="i-mingcute-earth-2-line w-3.5 shrink-0 text-[13px] text-neutral-500" />
        <span class="w-14 shrink-0 truncate text-[10px]" :class="bar.highlight ? 'text-neutral-100' : 'text-neutral-400'">{{ bar.label }}</span>
        <span class="relative h-3.5 min-w-0 flex-1 rounded-sm bg-white/4">
          <span
            class="absolute inset-y-0 rounded-sm"
            :class="bar.player ? (bar.highlight ? 'bg-amber-300/70' : 'bg-sky-400/45') : 'bg-neutral-500/45'"
            :style="{ left: left(bar.startTick), width: width(bar) }"
          />
        </span>
        <span class="w-9 shrink-0 text-right text-[10px] text-neutral-400 tabular-nums">{{ formatDurationTicks(bar.endTick - bar.startTick) }}</span>
      </li>
    </ul>
  </figure>
</template>
