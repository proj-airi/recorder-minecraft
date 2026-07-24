import type { ViewerApiClient } from '../api/client'
import type {
  AppliedAction,
  BundleSummary,
  ImportProgress,
  PlayerTickState,
  RenderTimelineFrame,
  ReplayDescriptor,
  SceneSliceResponse,
  TrajectoryResponse,
} from '../types/viewer'

import { computed, onBeforeUnmount, shallowReadonly, shallowRef } from 'vue'

import { ViewerApiError } from '../api/client'
import { clamp, reconstructControls, sortAppliedActions } from '../utils/viewer'

const ACTION_LIMIT = 200
const TRAJECTORY_POINT_LIMIT = 2400
const TIMELINE_PAGE_SIZE = 400
const TIMELINE_CACHE_LIMIT = 1200

export function useBundleViewer(apiClient: ViewerApiClient) {
  const bundle = shallowRef<BundleSummary | null>(null)
  const tickState = shallowRef<null | PlayerTickState>(null)
  const actions = shallowRef<AppliedAction[]>([])
  const trajectory = shallowRef<null | TrajectoryResponse>(null)
  const sceneSlice = shallowRef<null | SceneSliceResponse>(null)
  const replays = shallowRef<ReplayDescriptor[]>([])
  const selectedTick = shallowRef<null | number>(null)
  const selectedRenderFrame = shallowRef<null | number>(null)
  const selectedTimelineFrame = shallowRef<null | RenderTimelineFrame>(null)
  const sceneY = shallowRef(64)
  const sceneRadius = shallowRef(16)
  const errorMessage = shallowRef<null | string>(null)
  const importProgress = shallowRef<ImportProgress>({
    file_name: null,
    file_size: null,
    message: 'Choose one portable play bundle.',
    phase: 'idle',
  })
  const isLoadingTick = shallowRef(false)
  const timelineFrames = new Map<number, RenderTimelineFrame>()

  let importController: AbortController | null = null
  let tickController: AbortController | null = null
  let sliceController: AbortController | null = null
  let timelineController: AbortController | null = null
  let tickRequestId = 0

  const isImporting = computed(() => (
    importProgress.value.phase === 'uploading'
    || importProgress.value.phase === 'validating'
    || importProgress.value.phase === 'loading'
  ))

  const renderMediaUrl = computed(() => (
    bundle.value?.render ? apiClient.getRenderMediaUrl() : ''
  ))

  async function initialize(): Promise<void> {
    if (!apiClient.hasToken) {
      errorMessage.value = 'Launch this page with `minerec viewer` so the loopback bridge can authorize it.'
      return
    }

    try {
      const activeBundle = await apiClient.getBundle()
      await activateBundle(activeBundle, activeBundle.tick_range.start)
    }
    catch (error) {
      if (error instanceof ViewerApiError && (error.status === 404 || (error.status === 400 && error.message.includes('no play bundle is open')))) {
        return
      }
      errorMessage.value = errorText(error)
    }
  }

  async function importBundle(file: File): Promise<void> {
    if (isImporting.value) {
      return
    }

    importController = new AbortController()
    errorMessage.value = null
    importProgress.value = {
      file_name: file.name,
      file_size: file.size,
      message: 'Streaming to the local bridge. Validation completes before the bundle is exposed.',
      phase: 'uploading',
    }

    try {
      const result = await apiClient.importBundle(file, importController.signal)
      importProgress.value = {
        ...importProgress.value,
        message: 'Validation passed. Loading bounded views…',
        phase: 'loading',
      }
      await activateBundle(result.bundle, result.initial_tick)
      importProgress.value = {
        ...importProgress.value,
        message: 'Bundle validated and ready.',
        phase: 'ready',
      }
    }
    catch (error) {
      if (isAbortError(error)) {
        importProgress.value = {
          file_name: null,
          file_size: null,
          message: 'Import cancelled. The previously opened bundle is unchanged.',
          phase: bundle.value ? 'ready' : 'idle',
        }
        return
      }
      errorMessage.value = errorText(error)
      importProgress.value = {
        ...importProgress.value,
        message: 'Import rejected. The previously opened bundle is unchanged.',
        phase: 'error',
      }
    }
    finally {
      importController = null
    }
  }

  function cancelImport(): void {
    importController?.abort()
  }

  async function activateBundle(nextBundle: BundleSummary, requestedTick: number): Promise<void> {
    const tick = Math.round(clamp(requestedTick, nextBundle.tick_range.start, nextBundle.tick_range.end))
    const nextState = await apiClient.getTickState(tick, importController?.signal)
    const nextY = Math.floor(nextState.position.y)
    const [nextActions, nextTrajectory, nextScene, nextReplays] = await Promise.all([
      apiClient.getActions(tick, tick, ACTION_LIMIT, importController?.signal),
      apiClient.getTrajectory(TRAJECTORY_POINT_LIMIT, importController?.signal),
      apiClient.getSceneSlice(tick, nextState.dimension, nextY, sceneRadius.value, importController?.signal),
      apiClient.getReplays(importController?.signal),
    ])

    const sortedActions = sortAppliedActions(nextActions.actions)
    const reconstructedState = {
      ...nextState,
      controls: reconstructControls(sortedActions, nextState.controls),
    }
    bundle.value = nextBundle
    tickState.value = reconstructedState
    selectedTick.value = nextState.tick
    actions.value = sortedActions
    trajectory.value = nextTrajectory
    sceneSlice.value = nextScene
    replays.value = [...nextReplays.replays].sort((left, right) => left.ordinal - right.ordinal)
    sceneY.value = nextY
    timelineFrames.clear()
    selectedRenderFrame.value = null
    selectedTimelineFrame.value = null

    if (nextBundle.render) {
      const firstFrame = await loadTimelineFrame(0)
      selectedRenderFrame.value = firstFrame?.frame ?? null
      selectedTimelineFrame.value = firstFrame
    }
  }

  async function selectTick(requestedTick: number): Promise<void> {
    const activeBundle = bundle.value
    if (!activeBundle) {
      return
    }
    const tick = Math.round(clamp(requestedTick, activeBundle.tick_range.start, activeBundle.tick_range.end))
    if (tick === selectedTick.value && tickState.value) {
      return
    }

    tickController?.abort()
    const controller = new AbortController()
    tickController = controller
    const requestId = ++tickRequestId
    isLoadingTick.value = true

    try {
      const nextState = await apiClient.getTickState(tick, controller.signal)
      const [nextActions, nextScene] = await Promise.all([
        apiClient.getActions(tick, tick, ACTION_LIMIT, controller.signal),
        apiClient.getSceneSlice(tick, nextState.dimension, sceneY.value, sceneRadius.value, controller.signal),
      ])
      if (requestId !== tickRequestId) {
        return
      }
      const sortedActions = sortAppliedActions(nextActions.actions)
      tickState.value = {
        ...nextState,
        controls: reconstructControls(sortedActions, nextState.controls),
      }
      selectedTick.value = nextState.tick
      actions.value = sortedActions
      sceneSlice.value = nextScene
      const timelineFrame = findTimelineFrameForTick(nextState.tick)
      selectedRenderFrame.value = timelineFrame?.frame ?? null
      selectedTimelineFrame.value = timelineFrame
    }
    catch (error) {
      if (!isAbortError(error)) {
        errorMessage.value = errorText(error)
      }
    }
    finally {
      if (requestId === tickRequestId) {
        isLoadingTick.value = false
      }
    }
  }

  async function selectRenderFrame(frame: number): Promise<void> {
    const render = bundle.value?.render
    if (!render) {
      return
    }
    const boundedFrame = Math.round(clamp(frame, 0, Math.max(0, render.frame_count - 1)))
    const timeline = timelineFrames.get(boundedFrame) ?? await loadTimelineFrame(boundedFrame)
    if (!timeline) {
      return
    }
    selectedRenderFrame.value = timeline.frame
    selectedTimelineFrame.value = timeline
    await selectTick(timeline.server_tick)
    selectedRenderFrame.value = timeline.frame
    selectedTimelineFrame.value = timeline
  }

  async function setSceneY(nextY: number): Promise<void> {
    sceneY.value = Math.round(clamp(nextY, -2048, 2048))
    await reloadSceneSlice()
  }

  async function setSceneRadius(nextRadius: number): Promise<void> {
    sceneRadius.value = Math.round(clamp(nextRadius, 1, 32))
    await reloadSceneSlice()
  }

  async function reloadSceneSlice(): Promise<void> {
    const state = tickState.value
    if (!state) {
      return
    }
    sliceController?.abort()
    const controller = new AbortController()
    sliceController = controller
    try {
      sceneSlice.value = await apiClient.getSceneSlice(
        state.tick,
        state.dimension,
        sceneY.value,
        sceneRadius.value,
        controller.signal,
      )
    }
    catch (error) {
      if (!isAbortError(error)) {
        errorMessage.value = errorText(error)
      }
    }
  }

  async function loadTimelineFrame(frame: number): Promise<null | RenderTimelineFrame> {
    const cached = timelineFrames.get(frame)
    if (cached) {
      return cached
    }
    timelineController?.abort()
    const controller = new AbortController()
    timelineController = controller
    const fromFrame = Math.floor(frame / TIMELINE_PAGE_SIZE) * TIMELINE_PAGE_SIZE
    try {
      const response = await apiClient.getRenderTimeline(fromFrame, TIMELINE_PAGE_SIZE, controller.signal)
      if (timelineFrames.size + response.frames.length > TIMELINE_CACHE_LIMIT) {
        timelineFrames.clear()
      }
      for (const timelineFrame of response.frames) {
        timelineFrames.set(timelineFrame.frame, timelineFrame)
      }
      return timelineFrames.get(frame) ?? null
    }
    catch (error) {
      if (!isAbortError(error)) {
        errorMessage.value = errorText(error)
      }
      return null
    }
  }

  function findTimelineFrameForTick(tick: number): null | RenderTimelineFrame {
    for (const frame of timelineFrames.values()) {
      if (frame.server_tick === tick) {
        return frame
      }
    }
    return null
  }

  function clearError(): void {
    errorMessage.value = null
  }

  onBeforeUnmount(() => {
    importController?.abort()
    tickController?.abort()
    sliceController?.abort()
    timelineController?.abort()
  })

  return {
    actions: shallowReadonly(actions),
    bundle: shallowReadonly(bundle),
    cancelImport,
    clearError,
    errorMessage: shallowReadonly(errorMessage),
    importBundle,
    importProgress: shallowReadonly(importProgress),
    initialize,
    isImporting,
    isLoadingTick: shallowReadonly(isLoadingTick),
    renderMediaUrl,
    replays: shallowReadonly(replays),
    sceneRadius: shallowReadonly(sceneRadius),
    sceneSlice: shallowReadonly(sceneSlice),
    sceneY: shallowReadonly(sceneY),
    selectedRenderFrame: shallowReadonly(selectedRenderFrame),
    selectedTick: shallowReadonly(selectedTick),
    selectedTimelineFrame: shallowReadonly(selectedTimelineFrame),
    selectRenderFrame,
    selectTick,
    setSceneRadius,
    setSceneY,
    tickState: shallowReadonly(tickState),
    trajectory: shallowReadonly(trajectory),
  }
}

function errorText(error: unknown): string {
  if (error instanceof Error) {
    return error.message
  }
  return 'The viewer bridge rejected the request.'
}

function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === 'AbortError'
}
