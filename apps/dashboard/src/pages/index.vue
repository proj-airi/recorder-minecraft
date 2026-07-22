<script setup lang="ts">
import { Pane, Splitpanes } from 'splitpanes'

import ConfirmationDialog from '../components/ConfirmationDialog.vue'
import DatasetList from '../components/DatasetList.vue'
import OperationsView from '../components/OperationsView.vue'
import SampleInspector from '../components/SampleInspector.vue'
import StatusPill from '../components/StatusPill.vue'
import ToastMessage from '../components/ToastMessage.vue'
import { useDashboard } from '../composables/useDashboard'

const dashboard = useDashboard()
</script>

<template>
  <header>
    <div>
      <p class="eyebrow">MINECRAFT DATA COLLECTION</p>
      <h1>Recorder control room</h1>
    </div>
    <span id="server-pill" :class="['pill']">
      <StatusPill :value="dashboard.serverState.value" />
    </span>
  </header>
  <nav aria-label="Dashboard sections">
    <button :class="['tab', { active: dashboard.view.value === 'operations' }]" @click="dashboard.view.value = 'operations'">
      Live operations
    </button>
    <button :class="['tab', { active: dashboard.view.value === 'datasets' }]" @click="dashboard.view.value = 'datasets'; dashboard.refreshDatasets()">
      Dataset viewer
    </button>
  </nav>
  <main>
    <OperationsView
      v-if="dashboard.view.value === 'operations'"
      :capture="dashboard.capture.value"
      :capture-metrics="dashboard.captureMetrics.value"
      :grouped-recordings="dashboard.groupedRecordings.value"
      :recent-jobs="dashboard.status.value?.recent_jobs || []"
      :render-jobs="dashboard.renderJobs.value"
      :render-progress="dashboard.renderProgress"
      :render-workers="dashboard.renderWorkers.value"
      :server-detail="dashboard.serverDetail.value"
      :server-state="dashboard.serverState.value"
      :start-server-disabled="dashboard.startServerDisabled.value"
      :stop-server-disabled="dashboard.stopServerDisabled.value"
      :storage="dashboard.storage.value"
      :storage-percent="dashboard.storagePercent.value"
      @generate="id => dashboard.mutate(`/api/v1/recordings/${id}/generate`)"
      @open-dataset="dashboard.openDataset"
      @refresh-recordings="dashboard.refreshRecordings"
      @refresh-renders="dashboard.refreshRenders"
      @render-action="dashboard.renderJobAction"
      @render-recording="dashboard.queueRender"
      @start-server="dashboard.mutate('/api/v1/server/start')"
      @stop-server="dashboard.mutate('/api/v1/server/stop', 'Stop Minecraft and seal all active recordings?')"
    />

    <section v-else id="datasets" class="view active">
      <Splitpanes class="dataset-layout">
        <Pane :min-size="18" :size="28">
          <DatasetList
            :active-dataset-id="dashboard.dataset.value?.id"
            :datasets="dashboard.datasets.value"
            :issues="dashboard.datasetIssues.value"
            @refresh="dashboard.refreshDatasets"
            @select="dashboard.selectDataset"
          />
        </Pane>
        <Pane :min-size="35" :size="72">
          <SampleInspector
            :can-load-scene="dashboard.canLoadScene.value"
            :connections="dashboard.connections.value"
            :current-record="dashboard.currentRecord.value"
            :dataset="dashboard.dataset.value"
            :filters="dashboard.filters"
            :has-next-page="Boolean(dashboard.nextCursor.value)"
            :page-history="dashboard.pageHistory.value"
            :page-position="dashboard.pagePosition.value"
            :players="dashboard.players.value"
            :playing="dashboard.playing.value"
            :rgb-frame-url="dashboard.rgbFrameUrl.value"
            :sample-actions-text="dashboard.sampleActionsText.value"
            :sample-index="dashboard.sampleIndex.value"
            :sample-position="dashboard.samplePosition.value"
            :sample-provenance-text="dashboard.sampleProvenanceText.value"
            :sample-summary="dashboard.sampleSummary.value"
            :samples="dashboard.samples.value"
            :scene-error="dashboard.sceneError.value"
            :scene-loading="dashboard.sceneLoading.value"
            :scene-slice="dashboard.sceneSlice.value"
            :state-diff-text="dashboard.stateDiffText.value"
            :trajectory="dashboard.trajectory.value"
            :trajectory-error="dashboard.trajectoryError.value"
            :trajectory-loading="dashboard.trajectoryLoading.value"
            @apply-filters="dashboard.applyFilters"
            @load-scene="dashboard.loadScene"
            @next-page="dashboard.nextSamplePage"
            @next-sample="dashboard.showSample(dashboard.sampleIndex.value + 1)"
            @previous-page="dashboard.previousSamplePage"
            @previous-sample="dashboard.showSample(dashboard.sampleIndex.value - 1)"
            @set-sample="dashboard.showSample"
            @toggle-play="dashboard.togglePlay"
          />
        </Pane>
      </Splitpanes>
    </section>
  </main>
  <ConfirmationDialog
    :message="dashboard.confirmation.value?.message"
    :open="Boolean(dashboard.confirmation.value)"
    @answer="dashboard.answerConfirmation"
  />
  <ToastMessage :message="dashboard.toastMessage.value" :visible="dashboard.toastVisible.value" />
</template>
