<script setup lang="ts">
import type { EventLogRow } from '../eventLogRows'

import { computed } from 'vue'

import { formatEpisodeTime } from '../../basic/format'

const props = defineProps<{
  color: string
  current: boolean
  expanded: boolean
  row: EventLogRow
}>()

const emit = defineEmits<{
  seek: []
  toggle: []
}>()

const kindClass = computed(() => ({
  'action': 'text-sky-200 bg-sky-400/10',
  'arrival': 'text-neutral-400 bg-white/5',
  'client': 'text-violet-200 bg-violet-400/10',
  'connection': 'text-emerald-200 bg-emerald-400/10',
  'container': 'text-amber-200 bg-amber-400/12',
  'movement': 'text-neutral-400 bg-white/5',
  'protocol': 'text-neutral-500 bg-white/4',
  'state-change': 'text-rose-200 bg-rose-400/10',
  'tick-state': 'text-neutral-500 bg-white/4',
})[props.row.entry.kind])

const rawJson = computed(() => {
  if (!props.expanded)
    return ''
  const raw = props.row.entry.raw
  if (!raw) {
    const { raw: _raw, ...synthetic } = props.row.entry
    return JSON.stringify({ derived: true, ...synthetic }, null, 2)
  }
  try {
    return JSON.stringify(JSON.parse(raw), null, 2)
  }
  catch {
    return raw
  }
})
</script>

<template>
  <div
    class="event-log-row border-b border-[var(--dashboard-border-color)]/60"
    :class="current ? 'bg-amber-400/12' : 'hover:bg-white/4'"
    :data-current="current || undefined"
    role="listitem"
  >
    <div class="flex items-stretch">
      <button
        :aria-current="current ? 'step' : undefined"
        :aria-label="`Seek to ${row.playerName} ${row.entry.label} at Server tick ${row.entry.serverTick}: ${row.entry.summary}`"
        class="event-log-grid min-w-0 flex-1 border-0 bg-transparent px-2 py-1 text-left text-[11px] text-neutral-300"
        :title="row.entry.summary"
        type="button"
        @click="emit('seek')"
      >
        <span class="cell-tick text-neutral-400 font-mono tabular-nums">{{ row.entry.serverTick }}</span>
        <span class="cell-time text-neutral-500 font-mono tabular-nums">{{ formatEpisodeTime(row.episodeTick) }}</span>
        <span class="cell-player min-w-0 flex items-center gap-1 truncate">
          <span aria-hidden="true" class="h-1.5 w-1.5 shrink-0 rounded-full" :style="{ backgroundColor: color }" />
          <span class="truncate">{{ row.playerName }}</span>
        </span>
        <span class="cell-type min-w-0">
          <span class="inline-block max-w-full truncate rounded px-1 py-px align-middle text-[10px]" :class="kindClass">{{ row.entry.label }}</span>
        </span>
        <span class="cell-summary min-w-0 truncate" :class="current ? 'text-amber-50' : 'text-neutral-200'">{{ row.entry.summary }}</span>
      </button>
      <button
        :aria-expanded="expanded"
        :aria-label="`${expanded ? 'Hide' : 'Show'} raw JSON`"
        class="w-6 shrink-0 border-0 bg-transparent text-neutral-500 hover:text-neutral-100"
        :title="`${expanded ? 'Hide' : 'Show'} raw JSON`"
        type="button"
        @click="emit('toggle')"
      >
        <span aria-hidden="true" class="i-mingcute-code-line text-xs" />
      </button>
    </div>
    <pre
      v-if="expanded"
      class="m-0 mx-2 mb-2 max-h-64 overflow-auto rounded bg-black/40 p-2 text-[10px] text-neutral-300 leading-snug"
    ><code>{{ rawJson }}</code></pre>
  </div>
</template>

<style scoped>
/* Narrow docks stack the summary under the metadata; wide docks show one line of columns. */
.event-log-grid {
  align-items: center;
  column-gap: 0.5rem;
  display: grid;
  grid-template-areas:
    'tick time player type'
    'summary summary summary summary';
  grid-template-columns: 2.75rem 3.5rem minmax(0, 1fr) minmax(0, auto);
  row-gap: 0.125rem;
}

@container event-log (min-width: 560px) {
  .event-log-grid {
    grid-template-areas: 'tick time player type summary';
    grid-template-columns: 3rem 3.75rem 5.5rem 7.5rem minmax(0, 1fr);
  }
}

.cell-tick {
  grid-area: tick;
}

.cell-time {
  grid-area: time;
}

.cell-player {
  grid-area: player;
}

.cell-type {
  grid-area: type;
  justify-self: end;
}

@container event-log (min-width: 560px) {
  .cell-type {
    justify-self: start;
  }
}

.cell-summary {
  grid-area: summary;
}
</style>
