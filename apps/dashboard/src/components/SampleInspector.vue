<script setup lang="ts">
import ControlPanel from './ControlPanel.vue'
import SceneSlice from './SceneSlice.vue'
import TrajectoryPanel from './TrajectoryPanel.vue'

defineProps<{
  canLoadScene: boolean
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
  sceneError?: string
  sceneLoading: boolean
  sceneSlice?: any
  stateDiffText: string
  trajectory?: any
  trajectoryError?: string
  trajectoryLoading: boolean
}>()

const emit = defineEmits<{
  applyFilters: []
  loadScene: [axis: string, coordinate: number, radius: number]
  nextPage: []
  nextSample: []
  previousPage: []
  previousSample: []
  setSample: [index: number]
  togglePlay: []
}>()
</script>

<template>
  <section class="panel inspector">
    <div v-if="!dataset" class="empty-state">
      <p class="eyebrow">SAMPLE INSPECTOR</p>
      <h2>Select a verified dataset</h2>
      <p>Every canonical state tick is indexed on the server; transition controls appear when a matching sample exists.</p>
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
        <label>Modality<select v-model="filters.modality"><option value="">Any</option><option value="rgb">RGB attached</option><option value="scene">Scene attached</option></select></label>
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
        <TrajectoryPanel
          :current-record="currentRecord"
          :error="trajectoryError"
          :loading="trajectoryLoading"
          :trajectory="trajectory"
        />
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
              <dt>Scene</dt><dd>{{ sampleSummary.scene }}</dd>
            </dl>
          </template>
        </div>
      </div>
      <details open><summary>State -> next state (when available)</summary><pre>{{ stateDiffText }}</pre></details>
      <details><summary>Controls and ordered actions</summary><pre>{{ sampleActionsText }}</pre></details>
      <details><summary>Peers, validity, and provenance</summary><pre>{{ sampleProvenanceText }}</pre></details>
      <SceneSlice
        :can-load="canLoadScene"
        :error="sceneError"
        :loading="sceneLoading"
        :sample="currentRecord"
        :slice="sceneSlice"
        @load="(axis, coordinate, radius) => emit('loadScene', axis, coordinate, radius)"
      />
    </div>
  </section>
</template>
