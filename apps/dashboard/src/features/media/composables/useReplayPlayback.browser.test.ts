import { afterEach, describe, expect, it, vi } from 'vitest'
import { effectScope, shallowRef } from 'vue'

import { replayDurationTicks, useReplayPlayback } from './useReplayPlayback'

afterEach(() => vi.restoreAllMocks())

describe('replay playback duration', () => {
  it('uses the replay server-tick range', () => {
    expect(replayDurationTicks({ endServerTick: '350', startServerTick: '100' })).toBe(250)
  })

  it('falls back to replay timestamps when end ticks are unavailable', () => {
    expect(replayDurationTicks({
      endedAt: '2026-08-03T08:00:02.500Z',
      startedAt: '2026-08-03T08:00:00.000Z',
    })).toBe(50)
  })

  it('follows the bound media clock and delegates playback controls', async () => {
    const replay = shallowRef({ connectionId: 'replay-1', endServerTick: '100', startServerTick: '0' })
    const target = new EventTarget()
    const media = Object.assign(target, {
      currentTime: 0,
      pause: vi.fn(),
      play: vi.fn().mockResolvedValue(undefined),
    })

    const scope = effectScope()
    const playback = scope.run(() => useReplayPlayback(replay))!
    playback.bindMedia(media)
    playback.play()
    await Promise.resolve()
    target.dispatchEvent(new Event('playing'))
    target.dispatchEvent(new CustomEvent('time-update', { detail: { currentTime: 0.064 } }))

    expect(media.play).toHaveBeenCalledOnce()
    expect(playback.isPlaying.value).toBe(true)
    expect(playback.playheadTick.value).toBe(1)
    playback.seekToTick(40)
    expect(media.currentTime).toBe(2)
    playback.pause()
    expect(media.pause).toHaveBeenCalledOnce()
    scope.stop()
  })
})
