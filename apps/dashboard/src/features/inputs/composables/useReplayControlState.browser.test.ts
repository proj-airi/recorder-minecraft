import { describe, expect, it } from 'vitest'

import { parseControlStateLine, sampleAtOrBefore } from './useReplayControlState'

describe('replay control state', () => {
  it('parses sparse protobuf JSON and locates the playhead sample', () => {
    const first = parseControlStateLine('{"identity":{"serverTick":"1422"},"controlState":{"state":{"forward":true,"cameraYaw":-33.3}}}')
    const second = parseControlStateLine('{"identity":{"serverTick":"1423"},"controlState":{"state":{"right":true,"cameraDeltaYaw":1.5}}}')

    expect(first).toMatchObject({ cameraYaw: -33.3, forward: true, right: false, serverTick: 1422 })
    expect(second).toMatchObject({ cameraDeltaYaw: 1.5, forward: false, right: true, serverTick: 1423 })
    expect(sampleAtOrBefore([first!, second!], 1422.5)).toBe(first)
  })
})
