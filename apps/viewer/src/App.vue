<script setup lang="ts">
import { computed, onMounted, shallowRef } from 'vue'

import type { ViewerApiClient } from './api/client'
import BundleDropZone from './components/BundleDropZone.vue'
import ControlStatePanel from './components/ControlStatePanel.vue'
import IntegrityPanel from './components/IntegrityPanel.vue'
import PlaybackPanel from './components/PlaybackPanel.vue'
import ReplayInventoryPanel from './components/ReplayInventoryPanel.vue'
import SceneSlicePanel from './components/SceneSlicePanel.vue'
import TrajectoryPanel from './components/TrajectoryPanel.vue'
import { useBundleViewer } from './composables/useBundleViewer'

const props = defineProps<{
  apiClient: ViewerApiClient
}>()

const viewer = useBundleViewer(props.apiClient)
const localError = shallowRef<string | null>(null)
const visibleError = computed(() => localError.value ?? viewer.errorMessage.value)

onMounted(viewer.initialize)

function rejectFile(message: string): void {
  localError.value = message
}

async function importBundle(file: File): Promise<void> {
  localError.value = null
  await viewer.importBundle(file)
}

function clearError(): void {
  localError.value = null
  viewer.clearError()
}
</script>

<template>
  <div class="viewer-app">
    <header class="app-header">
      <div>
        <p class="brand-kicker">
          MineRec / Portable Play
        </p>
        <h1>Local bundle viewer</h1>
        <p class="header-copy">
          Inspect one validated connection without the recorder dashboard.
        </p>
      </div>
      <div class="bridge-state" :class="{ connected: apiClient.hasToken }">
        <i aria-hidden="true" />
        {{ apiClient.hasToken ? 'loopback bridge' : 'bridge unavailable' }}
      </div>
    </header>

    <main class="app-main">
      <div v-if="visibleError" class="error-banner" role="alert">
        <div>
          <strong>Viewer request failed</strong>
          <p>{{ visibleError }}</p>
        </div>
        <button class="icon-button" type="button" aria-label="Dismiss error" @click="clearError">
          ×
        </button>
      </div>

      <BundleDropZone
        :busy="viewer.isImporting.value"
        :progress="viewer.importProgress.value"
        @cancel="viewer.cancelImport"
        @import="importBundle"
        @reject="rejectFile"
      />

      <template v-if="viewer.bundle.value && viewer.tickState.value">
        <section class="bundle-identity" aria-label="Active bundle identity">
          <div class="identity-primary">
            <p class="eyebrow">
              Active connection
            </p>
            <h2>{{ viewer.bundle.value.identity.player_name }}</h2>
            <code>{{ viewer.bundle.value.identity.player_uuid }}</code>
          </div>
          <dl>
            <div>
              <dt>Server</dt>
              <dd>{{ viewer.bundle.value.identity.server_name }}</dd>
            </div>
            <div>
              <dt>Connection</dt>
              <dd :title="viewer.bundle.value.identity.connection_id">{{ viewer.bundle.value.identity.connection_id }}</dd>
            </div>
            <div>
              <dt>UTC range</dt>
              <dd>{{ new Date(viewer.bundle.value.utc_range.started_at).toLocaleString() }}</dd>
            </div>
            <div>
              <dt>Bundle revision</dt>
              <dd :title="viewer.bundle.value.bundle_id">{{ viewer.bundle.value.bundle_id.slice(0, 16) }}…</dd>
            </div>
          </dl>
        </section>

        <div class="primary-grid">
          <PlaybackPanel
            :current-frame="viewer.selectedRenderFrame.value"
            :current-tick="viewer.selectedTick.value ?? viewer.bundle.value.tick_range.start"
            :loading="viewer.isLoadingTick.value"
            :media-url="viewer.renderMediaUrl.value"
            :render="viewer.bundle.value.render"
            :tick-range="viewer.bundle.value.tick_range"
            :timeline-frame="viewer.selectedTimelineFrame.value"
            @frame-change="viewer.selectRenderFrame"
            @tick-change="viewer.selectTick"
          />
          <ControlStatePanel :actions="viewer.actions.value" :state="viewer.tickState.value" />
        </div>

        <div class="scene-grid">
          <TrajectoryPanel :state="viewer.tickState.value" :trajectory="viewer.trajectory.value" />
          <SceneSlicePanel
            :loading="viewer.isLoadingTick.value"
            :radius="viewer.sceneRadius.value"
            :slice="viewer.sceneSlice.value"
            :state="viewer.tickState.value"
            :y="viewer.sceneY.value"
            @radius-change="viewer.setSceneRadius"
            @y-change="viewer.setSceneY"
          />
        </div>

        <div class="provenance-grid">
          <ReplayInventoryPanel :replays="viewer.replays.value" />
          <IntegrityPanel :bundle="viewer.bundle.value" />
        </div>
      </template>

      <section v-else class="empty-welcome">
        <div class="empty-mark" aria-hidden="true">
          M
        </div>
        <h2>Nothing open yet</h2>
        <p>
          Drop a validated portable bundle above. Missing FPV renders are fine—the core reconstructed state and replay provenance do not depend on video.
        </p>
      </section>
    </main>

    <footer class="app-footer">
      <span>Local-only viewer</span>
      <span>No dashboard controls · no authentication UI · no render queue</span>
    </footer>
  </div>
</template>
