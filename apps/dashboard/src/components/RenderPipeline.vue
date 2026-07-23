<script setup lang="ts">
import type { RenderJob, RenderWorker } from '../types/dashboard'
import StatusPill from './StatusPill.vue'

defineProps<{
  jobs: RenderJob[]
  workers: RenderWorker[]
  renderProgress: (job: RenderJob) => string
}>()

const emit = defineEmits<{
  action: [jobId: string, action: 'cancel' | 'retry']
  refresh: []
}>()
</script>

<template>
  <section class="render-view">
    <div class="view-toolbar">
      <div><p class="eyebrow">RABBITMQ + GUI WORKERS</p><h2>Render pipeline</h2></div>
      <button class="quiet" @click="emit('refresh')">Refresh</button>
    </div>
    <section class="artifact-section">
      <div class="section-heading"><h3>Workers</h3><span>{{ workers.length }}</span></div>
      <p v-if="!workers.length" class="empty">No render workers have registered.</p>
      <div v-else class="worker-list">
        <div v-for="worker in workers" :key="worker.id" class="worker-row">
          <StatusPill :value="worker.state" />
          <strong>{{ worker.name }}</strong>
          <code>{{ worker.current_job_id || 'idle' }}</code>
          <small>{{ worker.heartbeat_at }}</small>
        </div>
      </div>
    </section>
    <section class="artifact-section">
      <div class="section-heading"><h3>Jobs</h3><span>{{ jobs.length }}</span></div>
      <p v-if="!jobs.length" class="empty">No render jobs.</p>
      <div v-else class="job-list">
        <div v-for="job in jobs" :key="job.id" class="job-row">
          <span><strong>{{ job.payload.session_id }}</strong><code>{{ job.dataset_id }}</code></span>
          <span><code>{{ job.payload.connection_id }}</code><small>{{ job.payload.render.width }}×{{ job.payload.render.height }} @ {{ job.payload.render.fps }} fps</small></span>
          <span>{{ renderProgress(job) }}</span>
          <StatusPill :value="job.state" />
          <button v-if="['queued', 'downloading', 'rendering', 'uploading'].includes(job.state)" class="danger" @click="emit('action', job.id, 'cancel')">Cancel</button>
          <button v-else-if="['failed', 'partial', 'canceled'].includes(job.state)" class="quiet" @click="emit('action', job.id, 'retry')">Retry</button>
        </div>
      </div>
    </section>
  </section>
</template>
