import { describe, expect, it } from 'vitest'

import { moveRangeEdge, serverTickForVideoTime } from './playRange'

const play = { endServerTick: 9546, startServerTick: 6334 }

describe('play range selection', () => {
  it('clamps an edge to the Play and never lets the range collapse', () => {
    const full = { ...play }
    expect(moveRangeEdge(full, 'start', 100, play)).toEqual({ endServerTick: 9546, startServerTick: 6334 })
    expect(moveRangeEdge(full, 'start', 7150.4, play)).toEqual({ endServerTick: 9546, startServerTick: 7150 })
    expect(moveRangeEdge({ endServerTick: 7000, startServerTick: 6500 }, 'start', 8000, play)).toEqual({ endServerTick: 7000, startServerTick: 6999 })
    expect(moveRangeEdge({ endServerTick: 7000, startServerTick: 6500 }, 'end', 6000, play)).toEqual({ endServerTick: 6501, startServerTick: 6500 })
    expect(moveRangeEdge(full, 'end', 99_999, play)).toEqual(full)
  })

  it('maps a video time to the tick of the last anchor at or before it', () => {
    // A hold: the image of tick 120 stays on screen from 1 s to 4 s of video.
    const anchors = { serverTicks: Float64Array.from([100, 120, 121]), videoSeconds: Float64Array.from([0, 1, 4]) }
    expect(serverTickForVideoTime(anchors, 0.5)).toBe(100)
    expect(serverTickForVideoTime(anchors, 3.9)).toBe(120)
    expect(serverTickForVideoTime(anchors, 10)).toBe(121)
    expect(serverTickForVideoTime({ serverTicks: new Float64Array(), videoSeconds: new Float64Array() }, 1)).toBeNull()
  })
})
