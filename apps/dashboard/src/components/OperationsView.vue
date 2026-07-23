<script setup lang="ts">
import { Button } from '@proj-airi/ui'
import { computed, ref } from 'vue'

import { badgeClass } from '../utils'
import StatusPill from './StatusPill.vue'

const props = defineProps<{
  capture: any
  captureMetrics: Array<[string, unknown]>
  groupedRecordings: Array<{ playerUuid: string, rows: any[] }>
  recentJobs: any[]
  renderJobs: any[]
  renderProgress: (job: any) => string
  renderWorkers: any[]
  serverDetail: string
  serverState: string
  startServerDisabled: boolean
  stopServerDisabled: boolean
  storage: any
  storagePercent: number
}>()

const emit = defineEmits<{
  generate: [recordingId: string]
  openDataset: [datasetId: string]
  refreshRecordings: []
  refreshRenders: []
  renderAction: [jobId: string, action: 'cancel' | 'retry']
  renderRecording: [recordingId: string, resolution: string, replaceLegacyRgb?: boolean]
  startServer: []
  stopServer: []
}>()

const renderResolutions = ref<Record<string, string>>({})
const storageDetail = computed(() => `${props.storage?.used_bytes == null ? '-' : props.storage.used_bytes} / ${props.storage?.quota_bytes == null ? '-' : props.storage.quota_bytes} (${props.storagePercent.toFixed(1)}%)`)

function resolutionFor(recordingId: string) {
  return renderResolutions.value[recordingId] || '640x360'
}

function rgbPresentation(recording: any) {
  const presentations: Record<string, string> = {
    full_client: 'full client + hand',
    hud_free: 'HUD-free (legacy)',
    legacy_gui_unsynchronized: 'legacy GUI (unsynchronized)',
    mixed_legacy_gui_unsynchronized: 'mixed legacy GUI (unsynchronized)',
    mixed: 'mixed presentation',
  }

  return presentations[String(recording.rgb_presentation)]
}
</script>

<template>
  <section id="operations" class="view active">
    <div class="summary-grid">
      <article class="card server-card">
        <div class="card-heading">
          <h2>Server</h2>
          <span id="server-state">{{ serverState }}</span>
        </div>
        <p class="muted">
          {{ serverDetail }}
        </p>
        <div class="actions">
          <button class="primary" :disabled="startServerDisabled" @click="emit('startServer')">
            Start server
          </button>
          <button class="danger" :disabled="stopServerDisabled" @click="emit('stopServer')">
            Stop and seal
          </button>
        </div>
      </article>

      <article class="card">
        <div class="card-heading">
          <h2>Capture</h2>
          <span>{{ capture?.state || 'offline' }}</span>
        </div>
        <dl class="metrics">
          <template v-for="[key, value] in captureMetrics" :key="key">
            <dt>{{ key }}</dt>
            <dd>{{ value ?? '-' }}</dd>
          </template>
        </dl>
      </article>

      <article class="card">
        <div class="card-heading">
          <h2>Storage</h2>
          <span>{{ storage?.state ?? '-' }}</span>
        </div>
        <div class="meter">
          <span :style="{ width: `${storagePercent}%`, background: storage?.state === 'ok' ? 'var(--green)' : storage?.state === 'warning' ? 'var(--yellow)' : 'var(--red)' }" />
        </div>
        <p class="muted">
          {{ storageDetail }}
        </p>
      </article>
    </div>

    <section class="panel">
      <div class="panel-heading">
        <div><p class="eyebrow">CONNECTION-SCOPED</p><h2>Player recordings</h2></div>
        <Button class="quiet" variant="ghost" size="sm" label="Refresh" @click="emit('refreshRecordings')" />
      </div>
      <div class="recordings">
        <p v-if="!groupedRecordings.length" class="empty">
          No connection ledger yet. Start the server and join once to create a recording.
        </p>
        <div v-for="group in groupedRecordings" v-else :key="group.playerUuid" class="player-group">
          <div class="player-heading">
            <strong>{{ group.rows[0]?.player_name || 'Unknown player' }}</strong><code>{{ group.playerUuid }}</code>
          </div>
          <div v-for="recording in group.rows" :key="recording.id" class="recording-row">
            <div><strong>Connection</strong><code>{{ recording.connection_id }}</code></div>
            <div>
              <small>Ticks</small>{{ recording.start_tick ?? '-' }} -> {{ recording.end_tick ?? 'live' }}
              <small v-if="recording.sample_count != null">RGB {{ recording.rgb_samples ?? 0 }} / {{ recording.sample_count }} samples</small>
            </div>
            <StatusPill :value="recording.state" />
            <div class="recording-actions">
              <button v-if="recording.state === 'complete'" class="primary" @click="emit('openDataset', recording.dataset_id)">
                View dataset
              </button>
              <button v-else :class="recording.can_generate ? 'primary' : 'quiet'" :disabled="!recording.can_generate" @click="emit('generate', recording.id)">
                {{ recording.state === 'failed' ? (recording.can_generate ? 'Retry generate' : 'Resolve conflict') : 'Seal & generate' }}
              </button>

              <template v-if="recording.render_job && ['queued', 'downloading', 'rendering', 'uploading'].includes(recording.render_job.state)">
                <span :class="['status', badgeClass(recording.render_job.state)]">RGB {{ recording.render_job.state }}</span>
                <button class="quiet" @click="emit('renderAction', recording.render_job.id, 'cancel')">Cancel</button>
              </template>
              <span v-else-if="recording.render_job && ['verifying', 'attaching'].includes(recording.render_job.state)" :class="['status', badgeClass(recording.render_job.state)]">
                RGB {{ recording.render_job.state }} · finalizing on server
              </span>
              <template v-else-if="recording.can_replace_legacy_rgb">
                <span class="status warning">Warning: legacy RGB HUD is unsynchronized</span>
                <select v-model="renderResolutions[recording.id]" class="render-resolution" aria-label="RGB re-render resolution">
                  <option value="640x360">640x360</option>
                  <option value="1280x720">1280x720</option>
                </select>
                <button class="quiet" @click="emit('renderRecording', recording.id, resolutionFor(recording.id), true)">Re-render RGB</button>
              </template>
              <button v-else-if="recording.render_job && ['failed', 'partial', 'canceled'].includes(recording.render_job.state)" class="quiet" @click="emit('renderAction', recording.render_job.id, 'retry')">
                Retry RGB
              </button>
              <span v-else-if="recording.rgb_complete" class="status ok">RGB complete{{ rgbPresentation(recording) ? ` · ${rgbPresentation(recording)}` : '' }}</span>
              <template v-else-if="recording.can_render">
                <select v-model="renderResolutions[recording.id]" class="render-resolution" aria-label="RGB render resolution">
                  <option value="640x360">640x360</option>
                  <option value="1280x720">1280x720</option>
                </select>
                <button class="quiet" @click="emit('renderRecording', recording.id, resolutionFor(recording.id))">Render RGB</button>
              </template>
            </div>
            <p v-if="recording.error" class="recording-error">
              {{ recording.error }}
            </p>
          </div>
        </div>
      </div>
    </section>

    <section class="panel">
      <div class="panel-heading">
        <div><p class="eyebrow">RABBITMQ ONE-SHOT WORKERS</p><h2>RGB render queue</h2></div>
        <Button class="quiet" variant="ghost" size="sm" label="Refresh" @click="emit('refreshRenders')" />
      </div>
      <div class="worker-strip">
        <p v-if="!renderWorkers.length" class="empty">
          No recent render-worker activity. Start one-shot minerec render-worker processes in a logged-in graphical session; queued RabbitMQ messages remain safe.
        </p>
        <div v-for="worker in renderWorkers" v-else :key="worker.name" class="worker">
          <StatusPill :value="worker.state" />
          <strong>{{ worker.name }}</strong>
          <small>{{ worker.state === 'offline' ? `last seen ${worker.heartbeat_at}` : worker.current_job_id || 'ready' }}</small>
        </div>
      </div>
      <div class="jobs">
        <p v-if="!renderJobs.length" class="empty">
          No RGB jobs queued.
        </p>
        <div v-for="job in renderJobs" v-else :key="job.id" class="job render-job">
          <div>
            <strong>{{ job.payload?.session_id || job.recording_id }}</strong>
            <small>{{ job.payload?.render?.width }}x{{ job.payload?.render?.height }} @ {{ job.payload?.render?.fps }} fps · {{ renderProgress(job) || 'waiting for RabbitMQ worker' }}</small>
          </div>
          <StatusPill :value="job.state" />
        </div>
      </div>
    </section>

    <section class="panel">
      <div class="panel-heading">
        <h2>Recent operations</h2>
      </div>
      <div class="jobs">
        <p v-if="!recentJobs.length" class="empty">
          No operations yet.
        </p>
        <div v-for="job in recentJobs" v-else :key="job.id" class="job">
          <div><strong>{{ job.kind?.replaceAll('_', ' ') }}</strong><small>{{ job.error || job.result?.output || job.created_at }}</small></div>
          <StatusPill :value="job.state" />
        </div>
      </div>
    </section>
  </section>
</template>
