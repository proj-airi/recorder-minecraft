import type { VideoFrameMetadataLike, VideoFrameSource } from '../src/utils/videoFrames'

import { describe, expect, it, vi } from 'vitest'

import { startVideoFrameLoop, supportsVideoFrameCallbacks } from '../src/utils/videoFrames'

describe('video frame synchronization', () => {
  it('uses presented-frame callbacks until playback pauses', () => {
    const callbacks = new Map<number, (now: number, metadata: VideoFrameMetadataLike) => void>()
    let nextHandle = 1
    const source = {
      cancelVideoFrameCallback: vi.fn((handle: number) => callbacks.delete(handle)),
      ended: false,
      paused: false,
      requestVideoFrameCallback: vi.fn((callback: (now: number, metadata: VideoFrameMetadataLike) => void) => {
        const handle = nextHandle++
        callbacks.set(handle, callback)
        return handle
      }),
    }
    const frames: number[] = []

    const stop = startVideoFrameLoop(source, mediaTime => frames.push(mediaTime))
    expect(supportsVideoFrameCallbacks(source)).toBe(true)
    expect(callbacks.size).toBe(1)

    callbacks.get(1)?.(0, { mediaTime: 2.25 })
    expect(frames).toEqual([2.25])
    expect(callbacks.has(2)).toBe(true)

    source.paused = true
    callbacks.get(2)?.(0, { mediaTime: 2.3 })
    expect(frames).toEqual([2.25, 2.3])
    expect(callbacks.has(3)).toBe(false)
    stop()
  })

  it('cancels an outstanding presented-frame callback', () => {
    const cancel = vi.fn()
    const source: VideoFrameSource = {
      cancelVideoFrameCallback: cancel,
      ended: false,
      paused: false,
      requestVideoFrameCallback: () => 42,
    }

    startVideoFrameLoop(source, () => {})()

    expect(cancel).toHaveBeenCalledWith(42)
  })
})
