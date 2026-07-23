<script setup lang="ts">
import { ref } from 'vue'

import type { ArtifactCatalog, DatasetConnection } from '../types/dashboard'
import { fmtBytes } from '../utils'
import StatusPill from './StatusPill.vue'

defineProps<{
  catalog: ArtifactCatalog
  loading: boolean
  selectedDataset: any
}>()

const emit = defineEmits<{
  refresh: []
  render: [datasetId: string, connection: DatasetConnection, resolution: string, noGui: boolean]
  selectDataset: [datasetId: string]
}>()

const resolution = ref('1280x720')
const noGui = ref(false)
</script>

<template>
  <section class="artifact-view">
    <div class="view-toolbar">
      <div>
        <p class="eyebrow">FILESYSTEM INDEX</p>
        <h2>Artifacts</h2>
      </div>
      <button class="quiet" :disabled="loading" @click="emit('refresh')">
        {{ loading ? 'Scanning…' : 'Refresh' }}
      </button>
    </div>

    <section class="artifact-section">
      <div class="section-heading">
        <h3>Capture fs</h3>
        <span>{{ catalog.capture_sessions.length }}</span>
      </div>
      <p v-if="!catalog.capture_sessions.length" class="empty">No capture directories found.</p>
      <div v-else class="data-table">
        <div class="table-row table-head"><span>Session</span><span>State</span><span>Epochs</span><span>Ticks</span><span>Size</span></div>
        <div v-for="capture in catalog.capture_sessions" :key="capture.relative_path" class="table-row">
          <code>{{ capture.session_id }}</code>
          <StatusPill :value="capture.state" />
          <span>{{ capture.published_epoch_count }} published / {{ capture.unpublished_epoch_count }} open</span>
          <span>{{ capture.first_tick ?? '–' }}–{{ capture.last_tick ?? '–' }}</span>
          <span>{{ fmtBytes(capture.size_bytes) }}</span>
        </div>
      </div>
    </section>

    <section class="artifact-section">
      <div class="section-heading">
        <h3>Flashback replay ZIP</h3>
        <span>{{ catalog.replay_archives.length }}</span>
      </div>
      <p v-if="!catalog.replay_archives.length" class="empty">No verified replay archives found.</p>
      <div v-else class="data-table replay-table">
        <div class="table-row table-head"><span>Archive</span><span>Session</span><span>Player / connection</span><span>Segment</span><span>Size</span></div>
        <div v-for="replay in catalog.replay_archives" :key="replay.artifact_id" class="table-row">
          <code>{{ replay.relative_path }}</code>
          <code>{{ replay.session_id }}</code>
          <span><code>{{ replay.player_uuid }}</code><small>{{ replay.connection_id || 'unbound' }}</small></span>
          <span>#{{ replay.segment_ordinal }}<small>{{ replay.segment_id }}</small></span>
          <span>{{ fmtBytes(replay.size_bytes) }}</span>
        </div>
      </div>
    </section>

    <section class="artifact-section">
      <div class="section-heading">
        <h3>Datasets</h3>
        <span>{{ catalog.datasets.length }}</span>
      </div>
      <p v-if="!catalog.datasets.length" class="empty">No verified dataset exports found.</p>
      <div v-else class="dataset-rows">
        <button v-for="dataset in catalog.datasets" :key="dataset.id" class="dataset-row" @click="emit('selectDataset', dataset.id)">
          <span><strong>{{ dataset.session_id }}</strong><code>{{ dataset.id }}</code></span>
          <span>{{ dataset.connection_count }} connections</span>
          <span>{{ dataset.sample_count }} samples</span>
          <span>{{ dataset.rgb_samples }} RGB</span>
          <span>{{ fmtBytes(dataset.size_bytes) }}</span>
        </button>
      </div>

      <div v-if="selectedDataset" class="dataset-detail">
        <div class="section-heading">
          <div><h3>{{ selectedDataset.session_id }}</h3><code>{{ selectedDataset.id }}</code></div>
          <div class="render-settings">
            <select v-model="resolution" aria-label="Render resolution">
              <option value="640x360">640×360</option>
              <option value="1280x720">1280×720</option>
              <option value="1920x1080">1920×1080</option>
            </select>
            <label><input v-model="noGui" type="checkbox"> Hide GUI</label>
          </div>
        </div>
        <div class="connection-list">
          <div v-for="connection in selectedDataset.connections" :key="connection.connection_id" class="connection-row">
            <span><strong>{{ connection.player_name || connection.player_uuid }}</strong><code>{{ connection.connection_id }}</code></span>
            <span>ticks {{ connection.first_tick }}–{{ connection.last_tick }}</span>
            <span>{{ connection.rgb_states }} / {{ connection.state_count }} RGB</span>
            <button
              class="primary"
              :disabled="connection.rgb_states >= connection.state_count"
              @click="emit('render', selectedDataset.id, connection, resolution, noGui)"
            >
              Render RGB
            </button>
          </div>
        </div>
      </div>
    </section>

    <section v-if="catalog.issues.length || catalog.rejected_datasets.length || catalog.truncated" class="artifact-issues">
      <h3>Index issues</h3>
      <p v-if="catalog.truncated">Replay scan reached its safety limit and is incomplete.</p>
      <p v-for="issue in catalog.issues" :key="`${issue.relative_path}:${issue.message}`"><code>{{ issue.relative_path }}</code> {{ issue.message }}</p>
      <p v-for="issue in catalog.rejected_datasets" :key="`${issue.name}:${issue.message}`"><code>{{ issue.name }}</code> {{ issue.message }}</p>
    </section>
  </section>
</template>
