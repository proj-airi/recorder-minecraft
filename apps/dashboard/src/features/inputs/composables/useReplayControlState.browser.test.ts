import { describe, expect, it } from 'vitest'

import { parseClickActionsLine, parseControlStateLine, sampleAtOrBefore } from './useReplayControlState'

describe('replay control state', () => {
  it('parses sparse protobuf JSON and locates the playhead sample', () => {
    const first = parseControlStateLine('{"identity":{"serverTick":"1422"},"controlState":{"state":{"forward":true,"cameraYaw":-33.3}}}')
    const second = parseControlStateLine('{"identity":{"serverTick":"1423"},"controlState":{"state":{"right":true,"cameraDeltaYaw":1.5}}}')

    expect(first).toMatchObject({ cameraYaw: -33.3, forward: true, right: false, serverTick: 1422 })
    expect(second).toMatchObject({ cameraDeltaYaw: 1.5, forward: false, right: true, serverTick: 1423 })
    expect(sampleAtOrBefore([first!, second!], 1422.5)).toBe(first)
  })

  it('reconstructs momentary mouse actions from applied packets', () => {
    const swing = parseClickActionsLine('{"identity":{"serverTick":"1424"},"packetApply":{"packet":{"identity":{"packetType":"minecraft:swing"},"actionKind":"swing"}}}')
    const useItem = parseClickActionsLine('{"identity":{"serverTick":"1425"},"packetApply":{"packet":{"identity":{"packetType":"minecraft:use_item"},"actionKind":"use"}}}')
    const attack = parseClickActionsLine('{"identity":{"serverTick":"1426"},"packetApply":{"packet":{"actionKind":"interact","interaction":"attack"}}}')

    expect(swing).toEqual({ actions: { leftClick: true, rightClick: false }, serverTick: 1424 })
    expect(useItem).toEqual({ actions: { leftClick: false, rightClick: true }, serverTick: 1425 })
    expect(attack).toEqual({ actions: { leftClick: true, rightClick: false }, serverTick: 1426 })
  })
})
