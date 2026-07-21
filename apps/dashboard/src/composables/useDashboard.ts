import { errorMessageFrom } from '@moeru/std'
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'

import { fmtBytes, stateDifference } from '../utils'

type ViewName = 'datasets' | 'operations'

export function useDashboard() {
  const csrf = ref('')
  const view = ref<ViewName>('operations')
  const toastMessage = ref('')
  const toastVisible = ref(false)
  const confirmation = ref<null | { message: string, resolve: (accepted: boolean) => void }>(null)
  const status = ref<any>(null)
  const recordings = ref<any[]>([])
  const renderJobs = ref<any[]>([])
  const renderWorkers = ref<any[]>([])
  const datasets = ref<any[]>([])
  const dataset = ref<any>(null)
  const samples = ref<any[]>([])
  const sampleIndex = ref(0)
  const currentSample = ref<any>(null)
  const sampleRequest = ref(0)
  const playing = ref(false)
  const currentCursor = ref<null | string>(null)
  const nextCursor = ref<null | string>(null)
  const pageHistory = ref<Array<null | string>>([])
  const sampleTotal = ref(0)
  const trajectory = ref<any>(null)
  const trajectoryRequest = ref(0)
  const datasetIssues = ref('')
  const filters = reactive({
    connection_id: '',
    from_tick: '',
    modality: '',
    player_uuid: '',
    to_tick: '',
    validity: '',
  })

  let playbackTimer: ReturnType<typeof setTimeout> | undefined
  let refreshTimer: ReturnType<typeof setInterval> | undefined
  let datasetRefreshTimer: ReturnType<typeof setTimeout> | undefined

  const serverState = computed(() => status.value?.server?.state ?? 'loading')
  const serverDetail = computed(() => {
    const server = status.value?.server
    return server?.message
      || (server?.services || []).map((service: any) => `${service.service}: ${service.state}${service.health ? `/${service.health}` : ''}`).join(' · ')
      || 'No Compose services found'
  })
  const capture = computed(() => status.value?.capture ?? {})
  const captureMetrics = computed<Array<[string, unknown]>>(() => [
    ['Session', capture.value.session_id],
    ['Tick', capture.value.server_tick],
    ['Epoch', capture.value.epoch_index],
    ['Players', capture.value.connected_player_count ?? capture.value.active_connection_count],
    ['Queue', capture.value.writer_queue_depth],
    ['Heartbeat', capture.value.heartbeat_age_seconds == null ? '-' : `${capture.value.heartbeat_age_seconds}s`],
  ])
  const storage = computed(() => status.value?.storage ?? {})
  const storagePercent = computed(() => storage.value.quota_bytes > 0 ? Math.min(100, storage.value.used_bytes / storage.value.quota_bytes * 100) : 0)
  const startServerDisabled = computed(() => ['running', 'starting', 'stopping'].includes(serverState.value) || Boolean(status.value?.active_job))
  const stopServerDisabled = computed(() => !['running', 'unhealthy'].includes(serverState.value) || Boolean(status.value?.active_job))
  const groupedRecordings = computed(() => {
    const groups = new Map<string, any[]>()
    recordings.value.forEach((recording) => {
      groups.set(recording.player_uuid, [...(groups.get(recording.player_uuid) || []), recording])
    })
    return [...groups.entries()].map(([playerUuid, rows]) => ({ playerUuid, rows }))
  })
  const connections = computed(() => dataset.value?.connections || [])
  const players = computed(() => {
    const rows = new Map<string, string>()
    connections.value.forEach((connection: any) => {
      if (!rows.has(connection.player_uuid))
        rows.set(connection.player_uuid, connection.player_name)
    })
    return [...rows.entries()].map(([uuid, name]) => ({ name, uuid }))
  })
  const pagePosition = computed(() => samples.value.length
    ? `${pageHistory.value.length * 100 + 1}-${pageHistory.value.length * 100 + samples.value.length} of ${sampleTotal.value}`
    : `0 of ${sampleTotal.value}`)
  const samplePosition = computed(() => samples.value.length ? `${sampleIndex.value + 1} / ${samples.value.length}` : '0 / 0')
  const currentRecord = computed(() => currentSample.value?.record)
  const currentSampleId = computed(() => currentSample.value?.id)
  const sampleSummary = computed(() => {
    const sample = currentRecord.value
    if (!sample)
      return null
    const key = sample.sample_key || {}
    const rgb = sample.modalities?.rgb || {}
    const voxels = sample.modalities?.voxels || {}
    return {
      connection: key.connection_id || sample.connection_id,
      player: sample.state?.player_name || key.player_uuid || sample.player_uuid,
      rgb: rgb.available && rgb.valid ? 'available' : rgb.reason || 'missing',
      tick: key.server_tick ?? sample.server_tick,
      transition: sample.transition_valid ? 'valid' : 'invalid',
      voxels: voxels.available && voxels.valid ? 'available' : voxels.reason || 'missing',
    }
  })
  const stateDiffText = computed(() => {
    const sample = currentRecord.value
    if (!sample)
      return JSON.stringify({ unavailable: 'no matching sample' }, null, 2)
    const difference = stateDifference(sample.state, sample.next_state)
    return JSON.stringify(Object.keys(difference).length ? difference : { unchanged: true }, null, 2)
  })
  const sampleActionsText = computed(() => {
    const sample = currentRecord.value
    return JSON.stringify(sample
      ? { ordered_packets: sample.action?.ordered_packets || [], reconstructed_control: sample.action?.reconstructed_control }
      : { unavailable: 'no matching sample' }, null, 2)
  })
  const sampleProvenanceText = computed(() => {
    const sample = currentRecord.value
    return JSON.stringify(sample
      ? { peers: sample.peers, source: sample.source, source_manifest_sha256: sample.source_manifest_sha256, transition_invalid_reasons: sample.transition_invalid_reasons, transition_valid: sample.transition_valid }
      : { unavailable: 'no matching sample' }, null, 2)
  })
  const rgbFrameUrl = computed(() => {
    const rgb = currentRecord.value?.modalities?.rgb
    return rgb?.available && rgb.valid && rgb.artifact_id && dataset.value && currentSampleId.value
      ? `/api/v1/datasets/${dataset.value.id}/samples/${currentSampleId.value}/frame`
      : ''
  })
  const canLoadVoxel = computed(() => {
    const voxels = currentRecord.value?.modalities?.voxels
    return Boolean(voxels?.available && voxels.valid && voxels.artifact_id)
  })

  function showToast(message: unknown) {
    toastMessage.value = errorMessageFrom(message) ?? String(message)
    toastVisible.value = true
    setTimeout(() => {
      toastVisible.value = false
    }, 3500)
  }

  function requestConfirmation(message: string) {
    return new Promise<boolean>((resolve) => {
      confirmation.value = { message, resolve }
    })
  }

  function answerConfirmation(accepted: boolean) {
    confirmation.value?.resolve(accepted)
    confirmation.value = null
  }

  async function api(path: string, options: RequestInit = {}) {
    const mutationHeaders: Record<string, string> = options.body
      ? { 'Content-Type': 'application/json', 'X-MC-Recorder-CSRF': csrf.value }
      : {}
    const optionHeaders = options.headers instanceof Headers
      ? Object.fromEntries(options.headers.entries())
      : Array.isArray(options.headers)
        ? Object.fromEntries(options.headers)
        : options.headers || {}
    const response = await fetch(path, {
      cache: 'no-store',
      ...options,
      headers: { ...mutationHeaders, ...optionHeaders },
    })
    const type = response.headers.get('content-type') || ''
    const data = type.includes('json') ? await response.json() : await response.blob()
    if (!response.ok)
      throw new Error(data.error || `${response.status} ${response.statusText}`)
    return data
  }

  async function refreshStatus() {
    try {
      const data = await api('/api/v1/status')
      status.value = data
      csrf.value = data.csrf_token
    }
    catch (error) {
      showToast(error)
    }
  }

  async function mutate(path: string, confirmText?: string) {
    if (confirmText && !(await requestConfirmation(confirmText)))
      return
    try {
      const job = await api(path, { body: '{}', method: 'POST' })
      showToast(`${job.kind} queued`)
      await refreshStatus()
    }
    catch (error) {
      showToast(error)
    }
  }

  async function refreshRecordings() {
    try {
      const data = await api('/api/v1/recordings')
      recordings.value = data.recordings || []
    }
    catch (error) {
      showToast(error)
    }
  }

  async function queueRender(recordingId: string, resolution: string, replaceLegacyRgb = false) {
    const [width, height] = resolution.split('x').map(Number)
    try {
      const job = await api(`/api/v1/recordings/${recordingId}/render`, {
        body: JSON.stringify({
          fps: 20,
          height,
          width,
          ...(replaceLegacyRgb ? { replace_legacy_rgb: true } : {}),
        }),
        method: 'POST',
      })
      showToast(`RGB render ${job.state}; the foreground GUI worker will claim it when online.`)
      await Promise.all([refreshRecordings(), refreshRenders()])
    }
    catch (error) {
      showToast(error)
    }
  }

  async function renderJobAction(jobId: string, action: 'cancel' | 'retry') {
    if (action === 'cancel' && !(await requestConfirmation('Cancel this RGB render job?')))
      return
    try {
      const job = await api(`/api/v1/render-jobs/${jobId}/${action}`, {
        body: '{}',
        method: 'POST',
      })
      showToast(`RGB render ${job.state}`)
      await Promise.all([refreshRecordings(), refreshRenders()])
    }
    catch (error) {
      showToast(error)
    }
  }

  async function refreshRenders() {
    try {
      const [jobData, workerData] = await Promise.all([
        api('/api/v1/render-jobs'),
        api('/api/v1/render-workers'),
      ])
      renderJobs.value = jobData.jobs || []
      renderWorkers.value = workerData.workers || []
    }
    catch (error) {
      showToast(error)
    }
  }

  function renderProgress(job: any) {
    const progress = job.progress || {}
    if (Number.isInteger(progress.current) && Number.isInteger(progress.total))
      return `${progress.current} / ${progress.total}${progress.message ? ` · ${progress.message}` : ''}`

    return progress.message || job.error || job.updated_at
  }

  async function openDataset(datasetId: string) {
    view.value = 'datasets'
    await refreshDatasets()
    if (datasets.value.some(item => item.id === datasetId))
      await selectDataset(datasetId)
    else
      showToast('The dataset is still being indexed; refresh the catalog shortly.')
  }

  async function refreshDatasets() {
    try {
      const data = await api('/api/v1/datasets')
      datasets.value = data.datasets || []
      if (datasetRefreshTimer)
        clearTimeout(datasetRefreshTimer)
      datasetIssues.value = (data.rejected || []).length ? `${data.rejected.length} export(s) rejected by integrity checks` : ''
      if (data.indexing)
        datasetRefreshTimer = setTimeout(refreshDatasets, 1000)
    }
    catch (error) {
      showToast(error)
    }
  }

  function appendActiveFilters(query: URLSearchParams) {
    Object.entries(filters).forEach(([key, value]) => {
      if (value)
        query.set(key, value)
    })
    return query
  }

  function sampleQuery() {
    const query = appendActiveFilters(new URLSearchParams({ limit: '100' }))
    if (currentCursor.value)
      query.set('cursor', currentCursor.value)
    return query
  }

  function trajectoryQuery() {
    return appendActiveFilters(new URLSearchParams({ max_points: '2400' }))
  }

  async function selectDataset(id: string) {
    stopPlayback()
    try {
      dataset.value = await api(`/api/v1/datasets/${id}`)
      currentSample.value = null
      trajectory.value = null
      sampleRequest.value += 1
      currentCursor.value = null
      nextCursor.value = null
      pageHistory.value = []
      await Promise.all([loadTrajectory(), loadSamplePage()])
    }
    catch (error) {
      showToast(error)
    }
  }

  async function loadTrajectory() {
    if (!dataset.value)
      return
    const request = ++trajectoryRequest.value
    try {
      const data = await api(`/api/v1/datasets/${dataset.value.id}/trajectory?${trajectoryQuery()}`)
      if (request === trajectoryRequest.value)
        trajectory.value = data
    }
    catch (error) {
      if (request === trajectoryRequest.value)
        trajectory.value = null
      showToast(error)
    }
  }

  async function loadSamplePage() {
    if (!dataset.value)
      return
    try {
      const page = await api(`/api/v1/datasets/${dataset.value.id}/samples?${sampleQuery()}`)
      samples.value = page.samples || []
      sampleIndex.value = 0
      sampleTotal.value = page.total || 0
      nextCursor.value = page.next_cursor
      if (samples.value.length)
        await showSample(0)
      else
        currentSample.value = null
    }
    catch (error) {
      showToast(error)
    }
  }

  async function applyFilters() {
    stopPlayback()
    sampleRequest.value += 1
    currentSample.value = null
    currentCursor.value = null
    nextCursor.value = null
    pageHistory.value = []
    await Promise.all([loadTrajectory(), loadSamplePage()])
  }

  async function nextSamplePage() {
    if (!nextCursor.value)
      return
    stopPlayback()
    pageHistory.value.push(currentCursor.value)
    currentCursor.value = nextCursor.value
    await loadSamplePage()
  }

  async function previousSamplePage() {
    if (!pageHistory.value.length)
      return
    stopPlayback()
    currentCursor.value = pageHistory.value.pop() || null
    await loadSamplePage()
  }

  async function showSample(index: number) {
    if (!samples.value.length)
      return
    const bounded = Math.max(0, Math.min(index, samples.value.length - 1))
    const summary = samples.value[bounded]
    const request = ++sampleRequest.value
    try {
      const detail = await api(`/api/v1/datasets/${dataset.value.id}/samples/${summary.sample_id}`)
      if (request !== sampleRequest.value)
        return
      sampleIndex.value = bounded
      currentSample.value = { id: detail.sample_id, record: detail.record }
    }
    catch (error) {
      showToast(error)
    }
  }

  function stopPlayback() {
    playing.value = false
    if (playbackTimer)
      clearTimeout(playbackTimer)
    playbackTimer = undefined
  }

  async function playbackStep() {
    if (!playing.value)
      return
    const started = performance.now()
    if (sampleIndex.value < samples.value.length - 1) {
      await showSample(sampleIndex.value + 1)
    }
    else if (nextCursor.value) {
      pageHistory.value.push(currentCursor.value)
      currentCursor.value = nextCursor.value
      await loadSamplePage()
    }
    else {
      stopPlayback()
      return
    }
    if (playing.value)
      playbackTimer = setTimeout(playbackStep, Math.max(0, 50 - (performance.now() - started)))
  }

  function togglePlay() {
    if (playing.value) {
      stopPlayback()
      return
    }
    if (!samples.value.length)
      return
    playing.value = true
    playbackTimer = setTimeout(playbackStep, 50)
  }

  async function loadVoxel(axis: string, index: number) {
    if (!currentSample.value || !dataset.value)
      return null
    try {
      return await api(`/api/v1/datasets/${dataset.value.id}/samples/${currentSample.value.id}/voxel-slice?axis=${axis}&index=${index}`)
    }
    catch (error) {
      showToast(error)
      return null
    }
  }

  async function start() {
    await Promise.all([refreshStatus(), refreshRecordings(), refreshRenders()])
    refreshTimer = setInterval(() => {
      refreshStatus()
      refreshRecordings()
      refreshRenders()
    }, 2000)
  }

  onMounted(start)
  onBeforeUnmount(() => {
    if (refreshTimer)
      clearInterval(refreshTimer)
    if (datasetRefreshTimer)
      clearTimeout(datasetRefreshTimer)
    stopPlayback()
  })

  return {
    answerConfirmation,
    applyFilters,
    canLoadVoxel,
    capture,
    captureMetrics,
    confirmation,
    connections,
    currentRecord,
    currentSample,
    dataset,
    datasetIssues,
    datasets,
    filters,
    fmtBytes,
    groupedRecordings,
    loadVoxel,
    mutate,
    nextCursor,
    nextSamplePage,
    openDataset,
    pageHistory,
    pagePosition,
    players,
    playing,
    previousSamplePage,
    queueRender,
    refreshDatasets,
    refreshRecordings,
    refreshRenders,
    renderJobAction,
    renderJobs,
    renderProgress,
    renderWorkers,
    rgbFrameUrl,
    sampleActionsText,
    sampleIndex,
    samplePosition,
    sampleProvenanceText,
    samples,
    sampleSummary,
    selectDataset,
    serverDetail,
    serverState,
    showSample,
    startServerDisabled,
    stateDiffText,
    status,
    stopServerDisabled,
    storage,
    storagePercent,
    toastMessage,
    toastVisible,
    togglePlay,
    trajectory,
    view,
  }
}
