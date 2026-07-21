<script setup lang="ts">
import { ref } from 'vue'

import ControlPanel from './ControlPanel.vue'
import TrajectoryPanel from './TrajectoryPanel.vue'
import VoxelSlice from './VoxelSlice.vue'

defineProps<{
  canLoadVoxel: boolean
  connections: any[]
  currentRecord?: any
  dataset?: any
  filters: Record<string, string>
  pageHistory: Array<string | null>
  pagePosition: string
  players: Array<{ name: string, uuid: string }>
  playing: boolean
  hasNextPage: boolean
  rgbFrameUrl: string
  sampleActionsText: string
  sampleIndex: number
  samplePosition: string
  sampleProvenanceText: string
  sampleSummary?: any
  samples: any[]
  stateDiffText: string
  trajectory?: any
}>()

const emit = defineEmits<{
  applyFilters: []
  loadVoxel: [axis: string, index: number]
  nextPage: []
  nextSample: []
  previousPage: []
  previousSample: []
  setSample: [index: number]
  togglePlay: []
}>()

const voxel = ref<InstanceType<typeof VoxelSlice>>()

async function handleVoxel(axis: string, index: number) {
  emit('loadVoxel', axis, index)
}

defineExpose({
  drawVoxel(slice: any) {
    voxel.value?.draw(slice)
  },
})
</script>

<template>
  <section class="panel inspector">
    <div v-if="!dataset" class="empty-state">
      <p class="eyebrow">SAMPLE INSPECTOR</p>
      <h2>Select a verified dataset</h2>
      <p>Samples are indexed by byte offset; large JSONL files stay on the server.</p>
    </div>
    <div v-else>
      <div class="viewer-filters">
        <label>Player
          <select v-model="filters.player_uuid">
            <option value="">All players</option>
            <option v-for="player in players" :key="player.uuid" :value="player.uuid">{{ player.name || player.uuid }}</option>
          </select>
        </label>
        <label>Connection
          <select v-model="filters.connection_id">
            <option value="">All connections</option>
            <option v-for="connection in connections" :key="connection.connection_id" :value="connection.connection_id">{{ connection.player_name || connection.player_uuid }} · {{ connection.connection_id }}</option>
          </select>
        </label>
        <label>Validity<select v-model="filters.validity"><option value="">Any</option><option value="valid">Valid</option><option value="invalid">Invalid</option></select></label>
        <label>Modality<select v-model="filters.modality"><option value="">Any</option><option value="rgb">RGB attached</option><option value="voxels">Voxels attached</option></select></label>
        <label>From tick<input v-model="filters.from_tick" type="number" min="0"></label>
        <label>To tick<input v-model="filters.to_tick" type="number" min="0"></label>
        <button class="quiet" @click="emit('applyFilters')">Apply filters</button>
      </div>
      <div class="page-toolbar">
        <button class="quiet" :disabled="pageHistory.length === 0" @click="emit('previousPage')">Previous page</button>
        <span>{{ pagePosition }}</span>
        <button class="quiet" :disabled="!hasNextPage" @click="emit('nextPage')">Next page</button>
      </div>
      <div class="viewer-toolbar">
        <button class="quiet" @click="emit('previousSample')">←</button>
        <button class="primary" @click="emit('togglePlay')">{{ playing ? 'Pause' : 'Play' }}</button>
        <button class="quiet" @click="emit('nextSample')">→</button>
        <input type="range" min="0" :max="Math.max(0, samples.length - 1)" :value="sampleIndex" @input="emit('setSample', Number(($event.target as HTMLInputElement).value))">
        <span>{{ samplePosition }}</span>
      </div>
      <div class="observation-grid">
        <TrajectoryPanel :trajectory="trajectory" :current-record="currentRecord" />
        <ControlPanel :sample="currentRecord" />
      </div>
      <div class="sample-grid">
        <div class="frame-wrap">
          <img v-if="rgbFrameUrl" :src="rgbFrameUrl" alt="Rendered Minecraft frame">
          <div v-else>RGB unavailable</div>
        </div>
        <div class="sample-summary">
          <p v-if="!sampleSummary" class="empty">No samples match these filters.</p>
          <template v-else>
            <p class="eyebrow">TICK {{ sampleSummary.tick }}</p>
            <h2>{{ sampleSummary.player }}</h2>
            <dl>
              <dt>Connection</dt><dd>{{ sampleSummary.connection }}</dd>
              <dt>Transition</dt><dd>{{ sampleSummary.transition }}</dd>
              <dt>RGB</dt><dd>{{ sampleSummary.rgb }}</dd>
              <dt>Voxels</dt><dd>{{ sampleSummary.voxels }}</dd>
            </dl>
          </template>
        </div>
      </div>
      <details open><summary>State -> next state</summary><pre>{{ stateDiffText }}</pre></details>
      <details><summary>Controls and ordered actions</summary><pre>{{ sampleActionsText }}</pre></details>
      <details><summary>Peers, validity, and provenance</summary><pre>{{ sampleProvenanceText }}</pre></details>
      <VoxelSlice ref="voxel" :can-load="canLoadVoxel" :sample="currentRecord" @load="handleVoxel" />
    </div>
  </section>
</template>
