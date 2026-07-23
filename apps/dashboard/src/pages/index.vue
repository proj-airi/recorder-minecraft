<script setup lang="ts">
import ArtifactBrowser from '../components/ArtifactBrowser.vue'
import RenderPipeline from '../components/RenderPipeline.vue'
import ToastMessage from '../components/ToastMessage.vue'
import { useDashboard } from '../composables/useDashboard'

const dashboard = useDashboard()
</script>

<template>
  <header>
    <div>
      <p class="eyebrow">MC PLAY RECORDER</p>
      <h1>Artifact dashboard</h1>
    </div>
    <div class="header-counts">
      <span>{{ dashboard.artifacts.value.datasets.length }} datasets</span>
      <span>{{ dashboard.renderJobs.value.length }} render jobs</span>
    </div>
  </header>
  <nav aria-label="Dashboard sections">
    <button :class="['tab', { active: dashboard.view.value === 'artifacts' }]" @click="dashboard.view.value = 'artifacts'">
      Artifacts
    </button>
    <button :class="['tab', { active: dashboard.view.value === 'renders' }]" @click="dashboard.view.value = 'renders'; dashboard.refreshRenders()">
      Renders
    </button>
  </nav>
  <main>
    <ArtifactBrowser
      v-if="dashboard.view.value === 'artifacts'"
      :catalog="dashboard.artifacts.value"
      :loading="dashboard.loading.value"
      :selected-dataset="dashboard.selectedDataset.value"
      @refresh="dashboard.refreshArtifacts"
      @render="dashboard.queueRender"
      @render-artifact="dashboard.queueArtifactRender"
      @select-dataset="dashboard.selectDataset"
    />
    <RenderPipeline
      v-else
      :jobs="dashboard.renderJobs.value"
      :render-progress="dashboard.renderProgress"
      :workers="dashboard.renderWorkers.value"
      @action="dashboard.renderJobAction"
      @refresh="dashboard.refreshRenders"
    />
  </main>
  <ToastMessage :message="dashboard.toastMessage.value" :visible="dashboard.toastVisible.value" />
</template>
