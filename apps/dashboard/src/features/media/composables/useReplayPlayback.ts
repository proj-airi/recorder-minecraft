import type { RecorderMinecraftApiV1Replay } from '@proj-airi/recorder-minecraft-api'
import type { ComputedRef, Ref, ShallowRef } from 'vue'

import { computed, onScopeDispose, readonly, shallowRef, watch } from 'vue'

import { SERVER_TICK_RATE } from '../../timeline/domain'
import { replayDurationTicks } from '../../timeline/replay'

export interface ReplayPlayback {
  bindMedia: (media: null | ReplayPlaybackMedia) => void
  durationTicks: ComputedRef<number>
  goToEnd: () => void
  goToStart: () => void
  isPlaying: Readonly<ShallowRef<boolean>>
  pause: () => void
  play: () => void
  playheadTick: Readonly<ShallowRef<number>>
  seekByTicks: (deltaTicks: number) => void
  seekToTick: (tick: number) => void
}

export interface ReplayPlaybackMedia extends EventTarget {
  currentTime: number
  pause: () => void
  play: () => Promise<void>
}

export { replayDurationTicks } from '../../timeline/replay'

export function useReplayPlayback(replay: Readonly<Ref<null | RecorderMinecraftApiV1Replay>>): ReplayPlayback {
  const playheadTick = shallowRef(0)
  const isPlaying = shallowRef(false)
  const durationTicks = computed(() => replayDurationTicks(replay.value))
  let media: null | ReplayPlaybackMedia = null

  function onTimeUpdate(event: Event): void {
    const currentTime = Number((event as CustomEvent<{ currentTime?: unknown }>).detail?.currentTime)
    if (Number.isFinite(currentTime))
      playheadTick.value = Math.min(durationTicks.value, Math.max(0, Math.floor(currentTime * SERVER_TICK_RATE)))
  }

  function onPlaying(): void {
    isPlaying.value = true
  }

  function onPaused(): void {
    isPlaying.value = false
  }

  function bindMedia(nextMedia: null | ReplayPlaybackMedia): void {
    if (media === nextMedia)
      return

    media?.removeEventListener('time-update', onTimeUpdate)
    media?.removeEventListener('playing', onPlaying)
    media?.removeEventListener('pause', onPaused)
    media?.removeEventListener('ended', onPaused)
    media = nextMedia
    media?.addEventListener('time-update', onTimeUpdate)
    media?.addEventListener('playing', onPlaying)
    media?.addEventListener('pause', onPaused)
    media?.addEventListener('ended', onPaused)
    if (media)
      media.currentTime = playheadTick.value / SERVER_TICK_RATE
  }

  function pause(): void {
    media?.pause()
    isPlaying.value = false
  }

  function seekToTick(tick: number): void {
    const nextTick = Math.min(durationTicks.value, Math.max(0, Math.round(tick)))
    playheadTick.value = nextTick
    if (media)
      media.currentTime = nextTick / SERVER_TICK_RATE
  }

  function play(): void {
    if (isPlaying.value || !replay.value || durationTicks.value === 0 || !media)
      return

    if (playheadTick.value >= durationTicks.value)
      seekToTick(0)
    void media.play().catch(() => onPaused())
  }

  function seekByTicks(deltaTicks: number): void {
    seekToTick(playheadTick.value + deltaTicks)
  }

  function goToStart(): void {
    pause()
    seekToTick(0)
  }

  function goToEnd(): void {
    pause()
    seekToTick(durationTicks.value)
  }

  watch(() => replay.value?.connectionId, () => {
    pause()
    seekToTick(0)
  })
  onScopeDispose(() => {
    pause()
    bindMedia(null)
  })

  return {
    bindMedia,
    durationTicks,
    goToEnd,
    goToStart,
    isPlaying: readonly(isPlaying),
    pause,
    play,
    playheadTick: readonly(playheadTick),
    seekByTicks,
    seekToTick,
  }
}
