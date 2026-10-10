import { describe, expect, it } from 'vitest'

import { testReplay } from './fixtures/replays'
import { addReplaysToEpisode, commitPlacementEdit, createEmptyEpisode, cutPlacement } from './replay'
import {
  episodeTickToServerTick,
  placementAt,
  playServerTickAt,
  playTickAt,
  serverTickToEpisodeTick,
} from './ticks'

describe('tick helpers', () => {
  const base = addReplaysToEpisode(createEmptyEpisode(), [
    testReplay('alice', { end: 916, sessionId: 's1', start: 232 }),
    testReplay('bob', { end: 896, sessionId: 's1', start: 242 }),
  ])!

  it('maps episode ticks to Server ticks through a session anchor', () => {
    const session = base.sessions[0]!
    expect(episodeTickToServerTick(session, 0)).toBe(232)
    expect(episodeTickToServerTick(session, 100)).toBe(332)
    expect(serverTickToEpisodeTick(session, 242)).toBe(10)
  })

  it('maps through a trimmed and moved placement', () => {
    // Trim Bob's start by 20 ticks, then move the clip 30 ticks later.
    const trimmed = commitPlacementEdit(base, 'play:bob:primary', 30, 664, 'lane:session:s1:uuid:uuid-bob')!
    const moved = commitPlacementEdit(trimmed, 'play:bob:primary', 60, 694, 'lane:session:s1:uuid:uuid-bob')!
    const bob = moved.placements.find(placement => placement.connectionId === 'bob')!

    expect(bob).toMatchObject({ sourceStartServerTick: 262, startTick: 60 })
    expect(playServerTickAt(bob, 60)).toBe(262)
    expect(episodeTickToServerTick(bob, 100)).toBe(302)
    expect(serverTickToEpisodeTick(bob, 302)).toBe(100)
    // Offset into the Play counts from the Play's first Server tick, not from the trimmed start.
    expect(playTickAt(bob, 60)).toBe(20)
    expect(playTickAt(bob, 59)).toBeNull()
    expect(playTickAt(bob, bob.endTick)).toBeNull()
    // The session anchor is not affected by the clip edit.
    expect(episodeTickToServerTick(moved.sessions[0]!, 100)).toBe(332)
  })

  it('finds the clip under the playhead on a lane after a cut', () => {
    const cut = cutPlacement(base, 'play:alice:primary', 300)!
    const lane = 'lane:session:s1:uuid:uuid-alice'
    expect(placementAt(cut, lane, 299)?.id).toBe('play:alice')
    expect(placementAt(cut, lane, 300)?.id).toBe('play:alice:cut:3')
    expect(playServerTickAt(placementAt(cut, lane, 300)!, 300)).toBe(532)
    expect(placementAt(cut, lane, 10_000)).toBeNull()
  })
})
