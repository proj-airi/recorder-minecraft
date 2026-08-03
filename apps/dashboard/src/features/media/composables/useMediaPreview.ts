import type { Ref } from 'vue'

import { CanvasSink, Input, MP4, UrlSource } from 'mediabunny'
import { onScopeDispose, readonly, shallowRef, watch } from 'vue'

export function useMediaPreview(sourceUrl: Ref<null | string>, playheadTick: Ref<number>, target: Ref<HTMLCanvasElement | null>) {
  const error = shallowRef<null | string>(null)
  const isLoading = shallowRef(false)
  const durationSeconds = shallowRef(0)
  const currentTimeSeconds = shallowRef(0)
  let generation = 0
  let input: Input<UrlSource> | null = null
  let sink: CanvasSink | null = null
  let pendingTick: null | number = null
  let rendering = false

  function dispose(): void {
    generation += 1
    pendingTick = null
    sink = null
    input?.dispose()
    input = null
  }

  async function open(url: null | string): Promise<void> {
    dispose()
    error.value = null
    currentTimeSeconds.value = 0
    durationSeconds.value = 0
    if (!url)
      return

    const currentGeneration = generation
    isLoading.value = true
    try {
      // NOTICE: Source replacement invalidates pending decode work and keeps a two-canvas pool,
      // following MediaBunny's maintained player lifecycle at
      // `https://github.com/Vanilagy/mediabunny/blob/7a871cec4929f03a44620f64fa9363a199f4c70a/examples/media-player/media-player.ts#L107-L199`.
      const nextInput = new Input({ formats: [MP4], source: new UrlSource(url) })
      input = nextInput
      if (!await nextInput.canRead())
        throw new Error('The selected video format cannot be decoded in this browser')
      const videoTrack = await nextInput.getPrimaryVideoTrack()
      if (!videoTrack)
        throw new Error('The selected resource has no video track')
      if (currentGeneration !== generation)
        return
      sink = new CanvasSink(videoTrack, { poolSize: 2 })
      durationSeconds.value = await nextInput.computeDuration([videoTrack])
      requestRender(playheadTick.value)
    }
    catch (caught) {
      if (currentGeneration === generation)
        error.value = String(caught)
    }
    finally {
      if (currentGeneration === generation)
        isLoading.value = false
    }
  }

  function requestRender(tick: number): void {
    pendingTick = tick
    if (!rendering)
      void renderPendingFrames()
  }

  async function renderPendingFrames(): Promise<void> {
    rendering = true
    try {
      while (pendingTick !== null) {
        const tick = pendingTick
        pendingTick = null
        await render(tick)
      }
    }
    finally {
      rendering = false
    }
  }

  async function render(tick: number): Promise<void> {
    const activeSink = sink
    const canvas = target.value
    if (!activeSink || !canvas)
      return
    try {
      const frame = await activeSink.getCanvas(Math.min(durationSeconds.value, Math.max(0, tick / 20)))
      if (!frame || activeSink !== sink)
        return
      draw(canvas, frame.canvas)
      currentTimeSeconds.value = frame.timestamp
    }
    catch (caught) {
      if (activeSink === sink)
        error.value = String(caught)
    }
  }

  watch(sourceUrl, open, { immediate: true })
  watch(playheadTick, requestRender)
  watch(target, () => requestRender(playheadTick.value))
  onScopeDispose(dispose)

  return {
    currentTimeSeconds: readonly(currentTimeSeconds),
    durationSeconds: readonly(durationSeconds),
    error: readonly(error),
    isLoading: readonly(isLoading),
    redraw: () => requestRender(playheadTick.value),
  }
}

function draw(target: HTMLCanvasElement, source: HTMLCanvasElement | OffscreenCanvas): void {
  const rect = target.getBoundingClientRect()
  const scale = window.devicePixelRatio || 1
  const width = Math.max(1, Math.round(rect.width * scale))
  const height = Math.max(1, Math.round(rect.height * scale))
  if (target.width !== width || target.height !== height) {
    target.width = width
    target.height = height
  }
  const context = target.getContext('2d')
  if (!context)
    return
  const sourceWidth = source.width
  const sourceHeight = source.height
  const fit = Math.min(width / sourceWidth, height / sourceHeight)
  const drawWidth = sourceWidth * fit
  const drawHeight = sourceHeight * fit
  context.fillStyle = '#09090b'
  context.fillRect(0, 0, width, height)
  context.drawImage(source, (width - drawWidth) / 2, (height - drawHeight) / 2, drawWidth, drawHeight)
}
