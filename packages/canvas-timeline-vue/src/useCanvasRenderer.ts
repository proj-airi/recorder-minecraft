/* SPDX-License-Identifier: MPL-2.0 */
// NOTICE: This composable is a lifecycle-equivalent Vue port of
// `https://github.com/techsquidtv/canvas-timeline/blob/1536a2dbc54e3a333ace360894a2e4508b295cf1/packages/renderer/src/CanvasRenderer.tsx#L142-L428`.
// React refs/effects/context are replaced by Vue refs/watchers and an explicit engine prop; worker messages are unchanged.

import type { CanvasRendererStats, TimelineRendererTheme, TimelineRenderOptions } from '@proj-airi/canvas-timeline-renderer'
import type { TimelineKeyframeRenderGeometry } from '@techsquidtv/canvas-timeline-core'
import type { RationalTime } from '@techsquidtv/canvas-timeline-utils'
import type { ShallowRef } from 'vue'

import type { CanvasRendererError, CanvasRendererProps } from './types'

import { createRendererWorker, resolveTimelineRendererThemeFromElement } from '@proj-airi/canvas-timeline-renderer'
import { onBeforeUnmount, onMounted, watch } from 'vue'

export interface UseCanvasRendererEvents {
  renderError: (error: CanvasRendererError) => void
  renderStats: (stats: CanvasRendererStats) => void
}

type WorkerMessage = WorkerRenderErrorMessage | WorkerStatsMessage

interface WorkerRenderErrorMessage {
  error: {
    message: string
    name?: string
    stack?: string
  }
  type: 'RENDER_ERROR'
}

interface WorkerStatsMessage {
  stats: CanvasRendererStats
  type: 'RENDER_STATS'
}

export function useCanvasRenderer(
  container: Readonly<ShallowRef<HTMLDivElement | null>>,
  props: Readonly<CanvasRendererProps>,
  events: UseCanvasRendererEvents,
): void {
  let worker: null | Worker = null
  let resizeObserver: null | ResizeObserver = null
  let unsubscribeEngine: (() => void)[] = []
  let stopClassNameWatch: (() => void) | null = null
  let stopEngineWatch: (() => void) | null = null
  let resolvedTheme: null | TimelineRendererTheme = null
  let containerSize = { height: 0, width: 0 }

  function report(error: CanvasRendererError): void {
    events.renderError(error)
  }

  function createKeyframeGeometry(): TimelineKeyframeRenderGeometry | undefined {
    if (!(props.showKeyframes ?? props.keyframeProperty !== undefined))
      return undefined
    if (props.keyframeProperty === undefined) {
      report({ message: 'CanvasRenderer keyframe drawing requires a keyframeProperty.', reason: 'invalid-options' })
      return undefined
    }
    if (!props.engine.hasKeyframeProperty(props.keyframeProperty)) {
      report({
        message: `CanvasRenderer keyframe property "${props.keyframeProperty}" is not registered with the engine.`,
        reason: 'invalid-options',
      })
      return undefined
    }
    if (resolvedTheme === null)
      return undefined

    try {
      return props.engine.getKeyframeRenderGeometry({
        property: props.keyframeProperty,
        rulerHeight: resolvedTheme.metrics.rulerHeight,
        trackHeight: resolvedTheme.metrics.trackHeight,
        viewportHeight: containerSize.height,
        viewportWidth: containerSize.width,
      })
    }
    catch (cause) {
      report({ cause: asError(cause), message: 'CanvasRenderer could not prepare keyframe geometry.', reason: 'invalid-options' })
      return undefined
    }
  }

  function createRenderOptions(element: Element): TimelineRenderOptions {
    resolvedTheme = resolveTimelineRendererThemeFromElement(element, props.theme)
    const keyframeGeometry = createKeyframeGeometry()
    const showKeyframes = (props.showKeyframes ?? props.keyframeProperty !== undefined) && keyframeGeometry !== undefined
    return {
      keyframeGeometry,
      ruler: props.ruler,
      showClipDropFeedback: props.showClipDropFeedback ?? true,
      showClipLabels: props.showClipLabels ?? true,
      showClips: props.showClips ?? true,
      showInOutBoundaryLines: props.showInOutBoundaryLines ?? false,
      showInOutPoints: props.showInOutPoints ?? true,
      showKeyframes,
      showRulerLabels: props.showRulerLabels ?? true,
      showSnapLines: props.showSnapLines ?? true,
      theme: resolvedTheme,
    }
  }

  function teardown(): void {
    unsubscribeFromEngine()
    resizeObserver?.disconnect()
    resizeObserver = null
    worker?.terminate()
    worker = null
    const element = container.value
    element?.querySelector(':scope > canvas.timeline-canvas')?.remove()
  }

  function unsubscribeFromEngine(): void {
    unsubscribeEngine.forEach(unsubscribe => unsubscribe())
    unsubscribeEngine = []
  }

  function updateState(): void {
    worker?.postMessage({
      keyframeGeometry: createKeyframeGeometry(),
      keyframesRequested: props.showKeyframes ?? props.keyframeProperty !== undefined,
      state: props.engine.getState(),
      type: 'UPDATE_STATE',
    })
  }

  function subscribeToEngine(): void {
    unsubscribeFromEngine()
    if (!worker)
      return

    const engine = props.engine
    unsubscribeEngine = [
      engine.on('render', updateState),
      engine.on('playhead:scrub', (time: RationalTime) => worker?.postMessage({ time, type: 'UPDATE_PLAYHEAD' })),
    ]
  }

  function updateCanvasClassName(): void {
    const canvas = container.value?.querySelector<HTMLCanvasElement>(':scope > canvas.timeline-canvas')
    if (canvas)
      canvas.className = `timeline-canvas ${props.className ?? ''}`.trim()
  }

  function bind(): void {
    const element = container.value
    if (!element)
      return

    const canvas = document.createElement('canvas')
    canvas.className = `timeline-canvas ${props.className ?? ''}`.trim()
    Object.assign(canvas.style, {
      display: 'block',
      height: '100%',
      inset: '0',
      pointerEvents: 'none',
      position: 'absolute',
      width: '100%',
    })
    element.appendChild(canvas)

    const rect = element.getBoundingClientRect()
    const dpr = window.devicePixelRatio || 1
    containerSize = { height: Math.max(0, rect.height), width: Math.max(0, rect.width) }
    canvas.width = bitmapSize(rect.width, dpr)
    canvas.height = bitmapSize(rect.height, dpr)

    if (typeof Worker === 'undefined') {
      report({ message: 'CanvasRenderer requires browser Worker support.', reason: 'worker-unavailable' })
      canvas.remove()
      return
    }
    if (typeof canvas.transferControlToOffscreen !== 'function') {
      report({ message: 'CanvasRenderer requires HTMLCanvasElement.transferControlToOffscreen support.', reason: 'offscreen-unavailable' })
      canvas.remove()
      return
    }

    try {
      worker = createRendererWorker()
    }
    catch (cause) {
      report({ cause: asError(cause), message: 'CanvasRenderer worker could not be created.', reason: 'worker-failed' })
      canvas.remove()
      return
    }

    worker.onerror = (event: ErrorEvent) => {
      report({
        message: event.message || 'CanvasRenderer worker failed.',
        reason: 'worker-failed',
        ...(event.error instanceof Error ? { cause: event.error } : {}),
      })
    }
    worker.onmessage = (event: MessageEvent<WorkerMessage>) => {
      if (event.data.type === 'RENDER_STATS') {
        events.renderStats(event.data.stats)
        return
      }
      report({
        cause: workerErrorCause(event.data.error),
        message: event.data.error.message || 'CanvasRenderer worker render failed.',
        reason: 'worker-failed',
      })
    }

    let offscreen: OffscreenCanvas
    try {
      offscreen = canvas.transferControlToOffscreen()
    }
    catch (cause) {
      report({ cause: asError(cause), message: 'CanvasRenderer could not transfer its canvas to an OffscreenCanvas.', reason: 'offscreen-unavailable' })
      worker.terminate()
      worker = null
      canvas.remove()
      return
    }

    worker.postMessage({
      canvas: offscreen,
      diagnosticsEnabled: true,
      dpr,
      keyframesRequested: props.showKeyframes ?? props.keyframeProperty !== undefined,
      options: createRenderOptions(element),
      state: props.engine.getState(),
      type: 'INIT',
    }, [offscreen])

    subscribeToEngine()

    resizeObserver = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const currentDpr = window.devicePixelRatio || 1
        containerSize = {
          height: Math.max(0, entry.contentRect.height),
          width: Math.max(0, entry.contentRect.width),
        }
        worker?.postMessage({
          dpr: currentDpr,
          height: bitmapSize(entry.contentRect.height, currentDpr),
          keyframeGeometry: createKeyframeGeometry(),
          keyframesRequested: props.showKeyframes ?? props.keyframeProperty !== undefined,
          type: 'RESIZE',
          width: bitmapSize(entry.contentRect.width, currentDpr),
        })
      }
    })
    resizeObserver.observe(element)
  }

  onMounted(() => {
    bind()

    // NOTICE: Recorder replaces its projected TimelineEngine after persisted edits and undo/redo.
    // Mirroring the upstream React effect would remove the canvas and terminate its worker on every
    // engine identity change, exposing a blank frame; see
    // `https://github.com/techsquidtv/canvas-timeline/blob/1536a2dbc54e3a333ace360894a2e4508b295cf1/packages/renderer/src/CanvasRenderer.tsx#L296-L449`.
    // Keep the transferred OffscreenCanvas and worker alive, and swap only subscriptions and state.
    stopEngineWatch = watch(
      () => props.engine,
      () => {
        subscribeToEngine()
        updateState()
      },
    )
    stopClassNameWatch = watch(() => props.className, updateCanvasClassName)
  })

  watch(
    () => [
      props.showClipLabels,
      props.showClipDropFeedback,
      props.showClips,
      props.showInOutBoundaryLines,
      props.showInOutPoints,
      props.showKeyframes,
      props.keyframeProperty,
      props.showRulerLabels,
      props.showSnapLines,
      props.ruler,
      props.theme,
      props.themeKey,
    ],
    () => {
      const element = container.value
      if (!element || !worker)
        return
      worker.postMessage({
        keyframesRequested: props.showKeyframes ?? props.keyframeProperty !== undefined,
        options: createRenderOptions(element),
        type: 'UPDATE_OPTIONS',
      })
    },
    { deep: true },
  )

  onBeforeUnmount(() => {
    stopClassNameWatch?.()
    stopEngineWatch?.()
    teardown()
  })
}

function asError(cause: unknown): Error {
  return cause instanceof Error ? cause : new Error(String(cause))
}

function bitmapSize(cssSize: number, dpr: number): number {
  return Math.ceil(cssSize * dpr)
}

function workerErrorCause(error: WorkerRenderErrorMessage['error']): Error {
  const cause = new Error(error.message)
  cause.name = error.name ?? 'CanvasRendererWorkerError'
  cause.stack = error.stack
  return cause
}
