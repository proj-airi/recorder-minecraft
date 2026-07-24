import type { ViewerApiClient, ViewerReadContext } from '../api/client'
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

interface BundleSnapshot {
  actions: AppliedAction[]
  bundle: BundleSummary
  replays: ReplayDescriptor[]
  sceneSlice: SceneSliceResponse
  sceneY: number
  selectedRenderFrame: null | number
  selectedTimelineFrame: null | RenderTimelineFrame
  tickState: PlayerTickState
  timelineFrames: RenderTimelineFrame[]
  trajectory: TrajectoryResponse
}

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
    uploaded_bytes: null,
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
    || importProgress.value.phase === 'committing'
  ))

  const canCancelImport = computed(() => (
    importProgress.value.phase === 'uploading'
    || importProgress.value.phase === 'validating'
    || importProgress.value.phase === 'loading'
  ))

  const renderMediaUrl = computed(() => (
    bundle.value?.render ? apiClient.getRenderMediaUrl(bundle.value.bundle_id) : ''
  ))

  async function initialize(): Promise<void> {
    if (!apiClient.hasToken) {
      errorMessage.value = 'Launch this page with `minerec viewer` so the loopback bridge can authorize it.'
      return
    }

    try {
      const activeBundle = await apiClient.getBundle()
      applySnapshot(await prepareBundle(activeBundle, activeBundle.tick_range.start))
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

    const controller = new AbortController()
    importController = controller
    let stagedImportId: null | string = null
    let prepared: BundleSnapshot | null = null
    let commitStarted = false
    errorMessage.value = null
    importProgress.value = {
      file_name: file.name,
      file_size: file.size,
      message: 'Streaming bundle bytes to the local bridge…',
      phase: 'uploading',
      uploaded_bytes: 0,
    }

    try {
      const staged = await apiClient.stageBundle(file, {
        onProgress(loadedBytes) {
          if (!controller.signal.aborted) {
            importProgress.value = {
              ...importProgress.value,
              uploaded_bytes: loadedBytes,
            }
          }
        },
        onValidationStart() {
          if (!controller.signal.aborted) {
            importProgress.value = {
              ...importProgress.value,
              message: 'Upload complete. Validating hashes, ZIP structure, scene, and optional render…',
              phase: 'validating',
              uploaded_bytes: file.size,
            }
          }
        },
      }, controller.signal)
      stagedImportId = staged.staged_import_id
      importProgress.value = {
        ...importProgress.value,
        message: 'Validation passed. Loading bounded views before activation…',
        phase: 'loading',
      }
      prepared = await prepareBundle(staged.bundle, staged.bundle.tick_range.start, {
        signal: controller.signal,
        stagedImportId,
      })
      controller.signal.throwIfAborted()

      commitStarted = true
      importProgress.value = {
        ...importProgress.value,
        message: 'Activating the validated bundle…',
        phase: 'committing',
      }
      const committed = await apiClient.commitStagedImport(stagedImportId)
      if (committed.bundle_id !== prepared.bundle.bundle_id) {
        throw new ViewerApiError('The bridge activated an unexpected bundle revision.')
      }
      applySnapshot({ ...prepared, bundle: committed })
      importProgress.value = {
        ...importProgress.value,
        message: 'Bundle validated and ready.',
        phase: 'ready',
      }
    }
    catch (error) {
      if (commitStarted && prepared && await recoverCommittedSnapshot(prepared)) {
        importProgress.value = {
          ...importProgress.value,
          message: 'Bundle validated and ready.',
          phase: 'ready',
        }
        return
      }
      if (stagedImportId) {
        await discardQuietly(stagedImportId)
      }
      if (isAbortError(error)) {
        importProgress.value = {
          file_name: null,
          file_size: null,
          message: 'Import cancelled. The active bundle was not replaced.',
          phase: bundle.value ? 'ready' : 'idle',
          uploaded_bytes: null,
        }
        return
      }
      errorMessage.value = errorText(error)
      importProgress.value = {
        ...importProgress.value,
        message: commitStarted
          ? 'The bridge could not confirm which bundle is active. Reload the viewer before continuing.'
          : 'Import rejected. The active bundle was not replaced.',
        phase: 'error',
      }
    }
    finally {
      importController = null
    }
  }

  function cancelImport(): void {
    if (canCancelImport.value) {
      importController?.abort()
    }
  }

  async function prepareBundle(
    nextBundle: BundleSummary,
    requestedTick: number,
    context?: ViewerReadContext,
  ): Promise<BundleSnapshot> {
    const tick = Math.round(clamp(requestedTick, nextBundle.tick_range.start, nextBundle.tick_range.end))
    const nextState = await apiClient.getTickState(tick, context)
    const nextY = Math.floor(nextState.position.y)
    const [nextActions, nextTrajectory, nextScene, nextReplays, timeline] = await Promise.all([
      apiClient.getActions(tick, tick, ACTION_LIMIT, context),
      apiClient.getTrajectory(TRAJECTORY_POINT_LIMIT, context),
      apiClient.getSceneSlice(tick, nextState.dimension, nextY, sceneRadius.value, context),
      apiClient.getReplays(context),
      nextBundle.render
        ? apiClient.getRenderTimeline(0, TIMELINE_PAGE_SIZE, context)
        : Promise.resolve({ frames: [], next_frame: null }),
    ])
    const sortedActions = sortAppliedActions(nextActions.actions)
    const firstFrame = timeline.frames.find(frame => frame.frame === 0) ?? null
    return {
      actions: sortedActions,
      bundle: nextBundle,
      replays: [...nextReplays.replays].sort((left, right) => left.ordinal - right.ordinal),
      sceneSlice: nextScene,
      sceneY: nextY,
      selectedRenderFrame: firstFrame?.frame ?? null,
      selectedTimelineFrame: firstFrame,
      tickState: {
        ...nextState,
        controls: reconstructControls(sortedActions, nextState.controls),
      },
      timelineFrames: timeline.frames,
      trajectory: nextTrajectory,
    }
  }

  function applySnapshot(snapshot: BundleSnapshot): void {
    tickController?.abort()
    sliceController?.abort()
    timelineController?.abort()
    tickRequestId += 1
    isLoadingTick.value = false
    bundle.value = snapshot.bundle
    tickState.value = snapshot.tickState
    selectedTick.value = snapshot.tickState.tick
    actions.value = snapshot.actions
    trajectory.value = snapshot.trajectory
    sceneSlice.value = snapshot.sceneSlice
    replays.value = snapshot.replays
    sceneY.value = snapshot.sceneY
    timelineFrames.clear()
    for (const frame of snapshot.timelineFrames) {
      timelineFrames.set(frame.frame, frame)
    }
    selectedRenderFrame.value = snapshot.selectedRenderFrame
    selectedTimelineFrame.value = snapshot.selectedTimelineFrame
  }

  async function recoverCommittedSnapshot(snapshot: BundleSnapshot): Promise<boolean> {
    try {
      const active = await apiClient.getBundle()
      if (active.bundle_id !== snapshot.bundle.bundle_id) {
        return false
      }
      applySnapshot({ ...snapshot, bundle: active })
      return true
    }
    catch {
      return false
    }
  }

  async function discardQuietly(stagedImportId: string): Promise<void> {
    try {
      await apiClient.discardStagedImport(stagedImportId)
    }
    catch {
      // The bridge may already have discarded or committed this candidate.
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
      const nextState = await apiClient.getTickState(tick, { signal: controller.signal })
      const renderFrame = activeBundle.render
        ? Math.round(clamp(tick - activeBundle.render.start_tick, 0, activeBundle.render.frame_count - 1))
        : null
      const [nextActions, nextScene, timelineFrame] = await Promise.all([
        apiClient.getActions(tick, tick, ACTION_LIMIT, { signal: controller.signal }),
        apiClient.getSceneSlice(
          tick,
          nextState.dimension,
          sceneY.value,
          sceneRadius.value,
          { signal: controller.signal },
        ),
        renderFrame === null
          ? Promise.resolve(null)
          : loadTimelineFrame(renderFrame, controller.signal),
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
      selectedRenderFrame.value = renderFrame
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
        { signal: controller.signal },
      )
    }
    catch (error) {
      if (!isAbortError(error)) {
        errorMessage.value = errorText(error)
      }
    }
  }

  async function loadTimelineFrame(
    frame: number,
    externalSignal?: AbortSignal,
  ): Promise<null | RenderTimelineFrame> {
    const cached = timelineFrames.get(frame)
    if (cached) {
      return cached
    }
    let ownedController: AbortController | null = null
    if (!externalSignal) {
      timelineController?.abort()
      ownedController = new AbortController()
      timelineController = ownedController
    }
    const fromFrame = Math.floor(frame / TIMELINE_PAGE_SIZE) * TIMELINE_PAGE_SIZE
    try {
      const response = await apiClient.getRenderTimeline(fromFrame, TIMELINE_PAGE_SIZE, {
        signal: externalSignal ?? ownedController?.signal,
      })
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
    finally {
      if (ownedController && timelineController === ownedController) {
        timelineController = null
      }
    }
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
    canCancelImport,
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
