import { describe, expect, it } from 'vitest'

import { createTimelineEngine } from './core/adapter'
import { testReplay } from './fixtures/replays'
import {
  addReplaysToEpisode,
  addReplayToEpisode,
  commitPlacementEdit,
  createEmptyEpisode,
  cutPlacement,
  deletePlacement,
  episodeAlignment,
  reorderLaneGroups,
} from './replay'

const ALICE_LANE = 'lane:session:s1:uuid:uuid-alice'
const BOB_LANE = 'lane:session:s1:uuid:uuid-bob'

function primarySpans(episode: ReturnType<typeof createEmptyEpisode>): [string, number, number][] {
  return episode.segments
    .filter(segment => segment.editable)
    .map(segment => [segment.id, segment.startTick, segment.endTick])
}

describe('session alignment', () => {
  it('starts with no mock tracks or clips', () => {
    expect(createEmptyEpisode()).toMatchObject({ durationTicks: 0, placements: [], segments: [], sessions: [], tracks: [] })
    expect(episodeAlignment(createEmptyEpisode()).mode).toBe('empty')
  })

  it('aligns Plays of one session by server tick, ignoring wall-clock skew', () => {
    // Bob's wall clock claims he joined 5 s (100 ticks) after Alice, but the shared Server tick
    // axis says 10 ticks. Inside one session the Server tick wins.
    const alice = testReplay('alice', { end: 916, sessionId: 's1', start: 232, startedAt: '2026-10-10T10:00:00.000Z' })
    const bob = testReplay('bob', { end: 896, sessionId: 's1', start: 242, startedAt: '2026-10-10T10:00:05.000Z' })
    const episode = addReplayToEpisode(addReplayToEpisode(createEmptyEpisode(), alice)!, bob)!

    expect(primarySpans(episode)).toEqual([
      ['play:alice:primary', 0, 684],
      ['play:bob:primary', 10, 664],
    ])
    expect(episode.sessions).toHaveLength(1)
    expect(episode.sessions[0]).toMatchObject({ anchorServerTick: 232, anchorTick: 0, placement: 'origin', sessionId: 's1' })
    expect(episodeAlignment(episode)).toEqual({ label: 'Aligned by server tick', mode: 'server-tick' })
    expect(episode.placements.map(placement => [placement.sessionId, placement.playerUuid, placement.playerName])).toEqual([
      ['s1', 'uuid-alice', 'alice'],
      ['s1', 'uuid-bob', 'bob'],
    ])
  })

  it('aligns the same session by server tick in any insertion order', () => {
    const alice = testReplay('alice', { end: 916, sessionId: 's1', start: 232, startedAt: '2026-10-10T10:00:00.000Z' })
    const bob = testReplay('bob', { end: 896, sessionId: 's1', start: 242, startedAt: '2026-10-10T10:00:05.000Z' })
    const bobFirst = addReplayToEpisode(addReplayToEpisode(createEmptyEpisode(), bob)!, alice)!
    const together = addReplaysToEpisode(createEmptyEpisode(), [bob, alice])!

    for (const episode of [bobFirst, together]) {
      expect(episode.placements.find(placement => placement.connectionId === 'alice')?.startTick).toBe(0)
      expect(episode.placements.find(placement => placement.connectionId === 'bob')?.startTick).toBe(10)
    }
  })

  it('falls back to wall clock across sessions and says so', () => {
    const alice = testReplay('alice', { end: 300, sessionId: 's1', start: 100, startedAt: '2026-10-10T10:00:00.000Z' })
    // Server ticks restart in a new session; only the wall clock relates the two.
    const carol = testReplay('carol', { end: 250, sessionId: 's2', start: 50, startedAt: '2026-10-10T10:00:10.000Z' })
    const episode = addReplayToEpisode(addReplayToEpisode(createEmptyEpisode(), alice)!, carol)!

    expect(primarySpans(episode)).toEqual([
      ['play:alice:primary', 0, 200],
      ['play:carol:primary', 200, 400],
    ])
    expect(episode.sessions.map(session => [session.key, session.placement, session.anchorTick])).toEqual([
      ['session:s1', 'origin', 0],
      ['session:s2', 'wall-clock', 200],
    ])
    expect(episodeAlignment(episode)).toEqual({ label: 'Sessions aligned by wall clock', mode: 'wall-clock' })
  })

  it('shifts every session when an earlier session establishes a new origin', () => {
    const carol = testReplay('carol', { end: 250, sessionId: 's2', start: 50, startedAt: '2026-10-10T10:00:10.000Z' })
    const alice = testReplay('alice', { end: 300, sessionId: 's1', start: 100, startedAt: '2026-10-10T10:00:00.000Z' })
    const episode = addReplayToEpisode(addReplayToEpisode(createEmptyEpisode(), carol)!, alice)!

    expect(primarySpans(episode)).toEqual([
      ['play:carol:primary', 200, 400],
      ['play:alice:primary', 0, 200],
    ])
    expect(episode.originTimeMs).toBe(Date.parse('2026-10-10T10:00:00.000Z'))
  })

  it('appends a session without wall clock and labels the alignment as sequential', () => {
    const alice = testReplay('alice', { end: 300, sessionId: 's1', start: 100, startedAt: '2026-10-10T10:00:00.000Z' })
    const dave = testReplay('dave', { end: 80, sessionId: 's3', start: 0 })
    const episode = addReplayToEpisode(addReplayToEpisode(createEmptyEpisode(), alice)!, dave)!

    expect(episode.placements[1]).toMatchObject({ endTick: 280, startTick: 200 })
    expect(episode.sessions[1]?.placement).toBe('sequential')
    expect(episodeAlignment(episode).mode).toBe('sequential')
  })
})

describe('world session anchor', () => {
  it('anchors a session on its world start when the world started first', () => {
    const alice = testReplay('alice', { end: 916, sessionId: 's1', start: 232, startedAt: '2026-10-10T10:00:10.000Z' })
    const episode = addReplaysToEpisode(createEmptyEpisode(), [alice], { endServerTick: 1_106, sessionId: 's1', startServerTick: 200 })!

    expect(episode.sessions[0]).toMatchObject({ anchorServerTick: 200, anchorTick: 0, world: { sessionId: 's1' } })
    expect(episode.placements[0]).toMatchObject({ endTick: 716, startTick: 32 })
    expect(episode.segments.find(segment => segment.id === 'session:s1:world')).toMatchObject({ endTick: 906, startTick: 0 })
    expect(episode.durationTicks).toBe(906)
    // The wall clock of tick 0 is derived from the Play's start minus 32 ticks.
    expect(episode.originTimeMs).toBe(Date.parse('2026-10-10T10:00:10.000Z') - 1_600)
  })
})

describe('lanes and groups', () => {
  it('groups rows by session with one lane per player', () => {
    const replays = [
      testReplay('alice-1', { end: 200, player: 'alice', sessionId: 's1', start: 100, startedAt: '2026-10-10T10:00:00.000Z' }),
      testReplay('bob-1', { end: 300, player: 'bob', sessionId: 's1', start: 120, startedAt: '2026-10-10T10:00:01.000Z' }),
      testReplay('alice-2', { end: 400, player: 'alice', sessionId: 's1', start: 250, startedAt: '2026-10-10T10:00:07.500Z' }),
      testReplay('alice-3', { end: 90, player: 'alice', sessionId: 's2', start: 10, startedAt: '2026-10-10T10:01:00.000Z' }),
    ]
    const episode = replays.reduce((current, replay) => addReplayToEpisode(current, replay)!, createEmptyEpisode())

    expect(episode.tracks.map(track => [track.role, track.id])).toEqual([
      ['session', 'session:s1:header'],
      ['world', 'session:s1:world'],
      ['primary', ALICE_LANE],
      ['primary', BOB_LANE],
      ['session', 'session:s2:header'],
      ['world', 'session:s2:world'],
      ['primary', 'lane:session:s2:uuid:uuid-alice'],
    ])
    expect(episode.placements.filter(placement => placement.laneId === ALICE_LANE).map(placement => placement.connectionId)).toEqual(['alice-1', 'alice-2'])
    expect(episode.segments.filter(segment => segment.trackId === ALICE_LANE).map(segment => [segment.startTick, segment.endTick])).toEqual([[0, 100], [150, 300]])
    expect(episode.tracks.find(track => track.id === ALICE_LANE)).toMatchObject({ label: 'alice', playerName: 'alice', playerUuid: 'uuid-alice' })
    expect(episode.tracks[0]).toMatchObject({ label: 'Session s1', sessionKey: 'session:s1' })
    expect(episode.segments.find(segment => segment.id === 'session:s1:world')).toMatchObject({ editable: false, endTick: 300, startTick: 0 })
  })

  it('cuts a clip into two halves on the same lane', () => {
    const episode = addReplayToEpisode(createEmptyEpisode(), testReplay('alice', { player: 'alice', sessionId: 's1', startedAt: '2026-10-10T10:00:00.000Z' }))!
    const cut = cutPlacement(episode, 'play:alice:primary', 80)!

    expect(cut.placements.map(placement => [placement.id, placement.laneId, placement.sourceStartServerTick, placement.sourceEndServerTick])).toEqual([
      ['play:alice', ALICE_LANE, 100, 180],
      ['play:alice:cut:3', ALICE_LANE, 180, 300],
    ])
    expect(cut.tracks.filter(track => track.role === 'primary').map(track => track.id)).toEqual([ALICE_LANE])
    expect(cut.segments.filter(segment => segment.editable).map(segment => [segment.trackId, segment.startTick, segment.endTick])).toEqual([
      [ALICE_LANE, 0, 80],
      [ALICE_LANE, 80, 200],
    ])

    const removedHalf = deletePlacement(cut, 'play:alice:primary')!
    expect(removedHalf.tracks.map(track => track.id)).toContain(ALICE_LANE)
    const removedAll = deletePlacement(removedHalf, 'play:alice:cut:3:primary')!
    expect(removedAll).toMatchObject({ durationTicks: 0, sessions: [], tracks: [] })
  })

  it('reorders players inside a session and whole sessions', () => {
    const replays = [
      testReplay('alice', { sessionId: 's1', startedAt: '2026-10-10T10:00:00.000Z' }),
      testReplay('bob', { sessionId: 's1', startedAt: '2026-10-10T10:00:00.000Z' }),
      testReplay('carol', { sessionId: 's2', startedAt: '2026-10-10T10:01:00.000Z' }),
    ]
    const episode = replays.reduce((current, replay) => addReplayToEpisode(current, replay)!, createEmptyEpisode())
    const players = reorderLaneGroups(episode, BOB_LANE, ALICE_LANE)!
    expect(players.tracks.filter(track => track.role === 'primary').map(track => track.label)).toEqual(['bob', 'alice', 'carol'])

    const sessions = reorderLaneGroups(players, 'session:s2:header', 'session:s1:header')!
    expect(sessions.tracks.filter(track => track.role === 'session').map(track => track.sessionKey)).toEqual(['session:s2', 'session:s1'])
    expect(reorderLaneGroups(episode, ALICE_LANE, 'session:s1:world')).toBeNull()
  })

  it('moves and trims a clip only on its own lane', () => {
    const replays = [
      testReplay('alice', { sessionId: 's1', startedAt: '2026-10-10T10:00:00.000Z' }),
      testReplay('bob', { sessionId: 's1', startedAt: '2026-10-10T10:00:00.000Z' }),
    ]
    const episode = addReplaysToEpisode(createEmptyEpisode(), replays)!
    const moved = commitPlacementEdit(episode, 'play:alice:primary', 10, 210, ALICE_LANE)!
    expect(moved.placements[0]).toMatchObject({ endTick: 210, sourceStartServerTick: 100, startTick: 10 })

    const trimmed = commitPlacementEdit(moved, 'play:alice:primary', 20, 200, ALICE_LANE)!
    expect(trimmed.placements[0]).toMatchObject({ sourceEndServerTick: 290, sourceStartServerTick: 110 })
    expect(commitPlacementEdit(episode, 'play:alice:primary', 10, 210, BOB_LANE)).toBeNull()
  })
})

describe('replay validation', () => {
  it('rejects a duplicate replay view', () => {
    const replay = testReplay('alice', { sessionId: 's1' })
    expect(addReplayToEpisode(addReplayToEpisode(createEmptyEpisode(), replay)!, replay)).toBeNull()
  })

  it('adds a replay with events but no first-person video as a data lane', () => {
    const episode = addReplayToEpisode(createEmptyEpisode(), testReplay('client', { sessionId: 's1', video: false }))!

    expect(episode.tracks.find(track => track.role === 'primary')).toMatchObject({ kind: 'data' })
    expect(episode.placements[0]?.source).toMatchObject({ eventsUrl: '/events/client.jsonl', videoUrl: undefined })
  })

  it('rejects a replay without video or events', () => {
    const empty = testReplay('empty', { video: false })
    delete empty.eventsUrl
    expect(addReplayToEpisode(createEmptyEpisode(), empty)).toBeNull()
  })

  it('groups a Play without a session id by itself', () => {
    const episode = addReplayToEpisode(createEmptyEpisode(), testReplay('legacy'))!
    expect(episode.sessions[0]).toMatchObject({ key: 'connection:legacy', label: 'legacy · no session id' })
  })

  it('keeps editing behavior behind the editable projection flag', () => {
    const episode = addReplayToEpisode(createEmptyEpisode(), testReplay('alice', { sessionId: 's1' }))!

    expect(createTimelineEngine(episode, { editable: false }).getClip('play:alice:primary')?.clip).toMatchObject({ movable: false, resizable: false })
    expect(createTimelineEngine(episode, { editable: true }).getClip('play:alice:primary')?.clip).toMatchObject({ movable: true, resizable: true })
    expect(createTimelineEngine(episode).getClip('session:s1:world')?.clip).toMatchObject({ movable: false })
  })
})
