import { errorMessageFrom } from '@moeru/std'
import { onBeforeUnmount, onMounted, ref } from 'vue'

import type {
  ArtifactCatalog,
  DatasetConnection,
  RenderJob,
  RenderWorker,
} from '../types/dashboard'

type ViewName = 'artifacts' | 'renders'

const EMPTY_CATALOG: ArtifactCatalog = {
  capture_sessions: [],
  datasets: [],
  datasets_indexing: false,
  issues: [],
  rejected_datasets: [],
  replay_archives: [],
  truncated: false,
}

export function useDashboard() {
  const csrf = ref('')
  const view = ref<ViewName>('artifacts')
  const artifacts = ref<ArtifactCatalog>(EMPTY_CATALOG)
  const selectedDataset = ref<any>(null)
  const renderJobs = ref<RenderJob[]>([])
  const renderWorkers = ref<RenderWorker[]>([])
  const loading = ref(false)
  const toastMessage = ref('')
  const toastVisible = ref(false)
  let refreshTimer: ReturnType<typeof setInterval> | undefined

  function showToast(value: unknown) {
    toastMessage.value = errorMessageFrom(value) ?? String(value)
    toastVisible.value = true
    setTimeout(() => {
      toastVisible.value = false
    }, 3500)
  }

  async function api(path: string, options: RequestInit = {}) {
    const response = await fetch(path, {
      cache: 'no-store',
      ...options,
      headers: {
        ...(options.body
          ? {
              'Content-Type': 'application/json',
              'X-MC-Recorder-CSRF': csrf.value,
            }
          : {}),
        ...options.headers,
      },
    })
    const data = await response.json()
    if (!response.ok)
      throw new Error(data.error || `${response.status} ${response.statusText}`)
    return data
  }

  async function refreshStatus() {
    const status = await api('/api/v1/status')
    csrf.value = status.csrf_token
  }

  async function refreshArtifacts() {
    loading.value = true
    try {
      artifacts.value = await api('/api/v1/artifacts')
    }
    catch (error) {
      showToast(error)
    }
    finally {
      loading.value = false
    }
  }

  async function selectDataset(datasetId: string) {
    try {
      selectedDataset.value = await api(`/api/v1/datasets/${datasetId}`)
    }
    catch (error) {
      showToast(error)
    }
  }

  async function queueRender(
    datasetId: string,
    connection: DatasetConnection,
    resolution: string,
    noGui: boolean,
  ) {
    const [width, height] = resolution.split('x').map(Number)
    try {
      const job = await api(`/api/v1/datasets/${datasetId}/render`, {
        body: JSON.stringify({
          connection_id: connection.connection_id,
          fps: 20,
          height,
          no_gui: noGui,
          player_uuid: connection.player_uuid,
          width,
        }),
        method: 'POST',
      })
      showToast(`Render ${job.state}`)
      view.value = 'renders'
      await refreshRenders()
    }
    catch (error) {
      showToast(error)
    }
  }

  async function refreshRenders() {
    try {
      const [jobs, workers] = await Promise.all([
        api('/api/v1/render-jobs'),
        api('/api/v1/render-workers'),
      ])
      renderJobs.value = jobs.jobs || []
      renderWorkers.value = workers.workers || []
    }
    catch (error) {
      showToast(error)
    }
  }

  async function renderJobAction(jobId: string, action: 'cancel' | 'retry') {
    try {
      await api(`/api/v1/render-jobs/${jobId}/${action}`, {
        body: '{}',
        method: 'POST',
      })
      await refreshRenders()
    }
    catch (error) {
      showToast(error)
    }
  }

  function renderProgress(job: RenderJob) {
    const progress = job.progress
    if (Number.isInteger(progress?.current) && Number.isInteger(progress?.total))
      return `${progress?.current} / ${progress?.total}${progress?.message ? ` · ${progress.message}` : ''}`
    return progress?.message || job.error || job.updated_at
  }

  async function start() {
    try {
      await refreshStatus()
      await Promise.all([refreshArtifacts(), refreshRenders()])
    }
    catch (error) {
      showToast(error)
    }
    refreshTimer = setInterval(() => {
      refreshArtifacts()
      refreshRenders()
    }, 5000)
  }

  onMounted(start)
  onBeforeUnmount(() => {
    if (refreshTimer)
      clearInterval(refreshTimer)
  })

  return {
    artifacts,
    loading,
    queueRender,
    refreshArtifacts,
    refreshRenders,
    renderJobAction,
    renderJobs,
    renderProgress,
    renderWorkers,
    selectDataset,
    selectedDataset,
    toastMessage,
    toastVisible,
    view,
  }
}
