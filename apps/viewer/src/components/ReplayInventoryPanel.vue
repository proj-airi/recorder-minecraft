<script setup lang="ts">
import { computed } from 'vue'

import type { ReplayDescriptor } from '../types/viewer'
import { formatBytes } from '../utils/viewer'

const props = defineProps<{
  replays: ReplayDescriptor[]
}>()

const totalBytes = computed(() => props.replays.reduce((total, replay) => total + replay.size, 0))
</script>

<template>
  <section class="panel replay-panel" aria-labelledby="replays-title">
    <div class="panel-heading">
      <div>
        <p class="eyebrow">
          Original bytes
        </p>
        <h2 id="replays-title">
          Replay inventory
        </h2>
      </div>
      <span>{{ replays.length }} segment{{ replays.length === 1 ? '' : 's' }} · {{ formatBytes(totalBytes) }}</span>
    </div>

    <div v-if="replays.length > 0" class="replay-list">
      <article v-for="replay in replays" :key="replay.segment_id" class="replay-row">
        <span class="ordinal">{{ replay.ordinal.toString().padStart(6, '0') }}</span>
        <div class="replay-name">
          <strong>{{ replay.segment_id }}</strong>
          <small>{{ replay.path }}</small>
        </div>
        <div class="tick-span">
          <span>server / replay ticks</span>
          <strong>{{ replay.start_tick.toLocaleString() }}–{{ replay.end_tick.toLocaleString() }}</strong>
          <small>{{ replay.replay_start_tick.toLocaleString() }}–{{ replay.replay_end_tick.toLocaleString() }}</small>
        </div>
        <code :title="replay.sha256">{{ replay.sha256.slice(0, 12) }}…</code>
        <span class="size">{{ formatBytes(replay.size) }}</span>
      </article>
    </div>
    <p v-else class="empty-copy">
      No replay descriptors are available.
    </p>
  </section>
</template>

<style scoped>
.replay-panel { padding: 1rem; }
.panel-heading { display: flex; justify-content: space-between; align-items: start; gap: 1rem; }.panel-heading > span { color: var(--muted); font: 620 .67rem/1.3 var(--mono); text-align: right; }
.replay-list { display: grid; margin-top: .8rem; border: 1px solid var(--line); border-radius: .55rem; overflow: hidden; }
.replay-row { display: grid; grid-template-columns: auto minmax(11rem, 1.6fr) minmax(8rem, .8fr) minmax(7rem, .7fr) auto; gap: .7rem; align-items: center; min-height: 3.5rem; padding: .6rem .7rem; border-top: 1px solid var(--line); background: #0a100c; }.replay-row:first-child { border-top: 0; }
.ordinal { color: var(--accent); font: 700 .65rem/1 var(--mono); }.replay-name { min-width: 0; }.replay-name strong, .replay-name small { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }.replay-name strong { font-size: .72rem; }.replay-name small { margin-top: .2rem; color: var(--muted); font: 580 .6rem/1.2 var(--mono); }
.tick-span span, .tick-span strong, .tick-span small { display: block; }.tick-span span { color: var(--muted); font-size: .56rem; text-transform: uppercase; }.tick-span strong, .tick-span small, .replay-row code, .size { margin-top: .2rem; color: #c8d6cb; font: 620 .64rem/1.2 var(--mono); }.tick-span small { color: var(--muted); }.replay-row code { overflow: hidden; text-overflow: ellipsis; }.size { text-align: right; }
.empty-copy { margin: .8rem 0 0; color: var(--muted); font-size: .74rem; }
@media (max-width: 760px) { .replay-row { grid-template-columns: auto minmax(0, 1fr) auto; }.tick-span, .replay-row code { display: none; } }
</style>
