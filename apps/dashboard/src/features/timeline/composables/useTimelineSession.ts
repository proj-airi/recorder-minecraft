import type { TimelineEngine } from '@techsquidtv/canvas-timeline-core'
import type { Ref, ShallowRef } from 'vue'

import type { CommitSegmentEdit, EpisodeDraft } from '../domain'

import { markRaw, onScopeDispose, readonly, shallowRef, watch } from 'vue'

import { createTimelineEngine, toServerTick, toTickTime } from '../core/adapter'
import { SERVER_TICK_RATE } from '../domain'

export interface TimelineSession {
  commitEdit: (edit: CommitSegmentEdit) => void
  engine: ShallowRef<TimelineEngine>
  goToEnd: () => void
  goToStart: () => void
  isPlaying: Readonly<ShallowRef<boolean>>
  pause: () => void
  play: () => void
  playheadTick: Readonly<ShallowRef<number>>
  rebuild: () => void
  renderRevision: Readonly<ShallowRef<number>>
  seekByTicks: (deltaTick: number) => void
  seekToTick: (tick: number) => void
  selectedSegmentId: Readonly<ShallowRef<null | string>>
  selectSegment: (segmentId: null | string) => void
  zoomBy: (factor: number) => void
}

export function useTimelineSession(episode: Ref<EpisodeDraft>, commitEdit: (edit: CommitSegmentEdit) => void): TimelineSession {
  const selectedSegmentId = shallowRef<null | string>(null)
  const renderRevision = shallowRef(0)
  const isPlaying = shallowRef(false)
  const engine = shallowRef(markRaw(createTimelineEngine(episode.value, null)))
  const playheadTick = shallowRef(toServerTick(engine.value.playheadTime))
  let playbackFrame = 0
  let playbackStartTick = 0
  let playbackStartedAt = 0
  let unsubscribeEvents: (() => void)[] = []

  function stopPlaybackFrame(): void {
    cancelAnimationFrame(playbackFrame)
    playbackFrame = 0
  }

  function bindEngineEvents(): void {
    unsubscribeEvents.forEach(unsubscribe => unsubscribe())
    unsubscribeEvents = [
      engine.value.on('render', requestRender),
      engine.value.on('playhead:scrub', (time) => {
        playheadTick.value = toServerTick(time)
        requestRender()
      }),
      engine.value.on('playback:state', (playing) => {
        isPlaying.value = playing
        requestRender()
      }),
    ]
  }

  function requestRender(): void {
    renderRevision.value += 1
  }

  function rebuild(): void {
    stopPlaybackFrame()
    const previous = engine.value
    previous.pause()
    engine.value = markRaw(createTimelineEngine(episode.value, selectedSegmentId.value, previous))
    playheadTick.value = toServerTick(engine.value.playheadTime)
    bindEngineEvents()
    requestRender()
  }

  function selectSegment(segmentId: null | string): void {
    selectedSegmentId.value = segmentId
    engine.value.selectClip(segmentId)
  }

  function play(): void {
    if (isPlaying.value)
      return

    const durationTick = episode.value.durationTicks
    playbackStartTick = playheadTick.value >= durationTick ? 0 : playheadTick.value
    if (playbackStartTick !== playheadTick.value)
      engine.value.updatePlayhead(toTickTime(playbackStartTick))

    // NOTICE: The upstream internal clock converts each ~16ms animation-frame delta separately
    // to the playhead's 20Hz rate. Since `fromSeconds` rounds each delta to the nearest tick, every
    // frame becomes zero and the fractional time is discarded. The relevant paths are
    // `https://github.com/techsquidtv/canvas-timeline/blob/1536a2dbc54e3a333ace360894a2e4508b295cf1/packages/core/src/playback.ts#L46-L58`
    // and
    // `https://github.com/techsquidtv/canvas-timeline/blob/1536a2dbc54e3a333ace360894a2e4508b295cf1/packages/utils/src/time.ts#L44-L56`.
    // Use core's external-clock mode and derive ticks from total elapsed time so sub-tick frame
    // deltas accumulate instead of being lost.
    engine.value.play({ clock: 'external' })
    playbackStartedAt = performance.now()
    playbackFrame = requestAnimationFrame(updatePlayback)
  }

  function pause(): void {
    stopPlaybackFrame()
    engine.value.pause()
  }

  function updatePlayback(time: number): void {
    if (!isPlaying.value)
      return stopPlaybackFrame()

    const durationTick = episode.value.durationTicks
    const elapsedTick = Math.floor((time - playbackStartedAt) * SERVER_TICK_RATE / 1_000)
    const nextTick = Math.min(durationTick, playbackStartTick + elapsedTick)
    if (nextTick !== playheadTick.value)
      engine.value.updatePlayhead(toTickTime(nextTick))

    if (nextTick >= durationTick) {
      pause()
      return
    }
    playbackFrame = requestAnimationFrame(updatePlayback)
  }

  function goToEnd(): void {
    pause()
    engine.value.updatePlayhead(toTickTime(episode.value.durationTicks))
  }

  function goToStart(): void {
    pause()
    engine.value.updatePlayhead(toTickTime(0))
  }

  function seekByTicks(deltaTick: number): void {
    if (isPlaying.value)
      pause()

    seekToTick(playheadTick.value + deltaTick)
  }

  function seekToTick(tick: number): void {
    const nextTick = Math.min(episode.value.durationTicks, Math.max(0, Math.round(tick)))
    if (isPlaying.value) {
      // A manual seek changes the origin of the external playback clock. Without rebasing both
      // values, the next animation frame would snap the playhead back to the pre-seek position.
      playbackStartTick = nextTick
      playbackStartedAt = performance.now()
    }
    engine.value.updatePlayhead(toTickTime(nextTick))
  }

  function zoomBy(factor: number): void {
    engine.value.setZoomScale(engine.value.zoomScale * factor)
  }

  bindEngineEvents()
  watch(episode, rebuild)
  onScopeDispose(() => {
    pause()
    unsubscribeEvents.forEach(unsubscribe => unsubscribe())
  })

  return {
    commitEdit,
    engine,
    goToEnd,
    goToStart,
    isPlaying: readonly(isPlaying),
    pause,
    play,
    playheadTick: readonly(playheadTick),
    rebuild,
    renderRevision: readonly(renderRevision),
    seekByTicks,
    seekToTick,
    selectedSegmentId: readonly(selectedSegmentId),
    selectSegment,
    zoomBy,
  }
}
