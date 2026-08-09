import type { RecorderMinecraftApiV1Replay } from '@proj-airi/recorder-minecraft-api'

import { describe, expect, it } from 'vitest'

import { createTimelineEngine } from './core/adapter'
import { addReplayToEpisode, createEmptyEpisode } from './replay'

function replay(connectionId: string, startedAt: string): RecorderMinecraftApiV1Replay {
  return {
    connectionId,
    endServerTick: '300',
    eventsUrl: `/events/${connectionId}.jsonl`,
    playerName: connectionId,
    serverName: 'test-server',
    startedAt,
    startServerTick: '100',
    video: { url: `/video/${connectionId}.mp4` },
  }
}

describe('replay timeline projection', () => {
  it('starts with no mock tracks or clips', () => {
    expect(createEmptyEpisode()).toMatchObject({ durationTicks: 0, segments: [], tracks: [] })
  })

  it('aligns overlapping views by recording time', () => {
    const first = addReplayToEpisode(createEmptyEpisode(), replay('alice', '2026-08-03T10:00:00.000Z'))!
    const second = addReplayToEpisode(first, replay('bob', '2026-08-03T10:00:02.000Z'))!

    expect(second.segments.map(segment => [segment.startTick, segment.endTick])).toEqual([
      [0, 200],
      [40, 240],
    ])
    expect(second.durationTicks).toBe(240)
    expect(second.tracks[1]?.replay).toMatchObject({
      eventsUrl: '/events/bob.jsonl',
      startServerTick: '100',
    })
  })

  it('shifts existing clips when an earlier replay establishes a new origin', () => {
    const later = addReplayToEpisode(createEmptyEpisode(), replay('bob', '2026-08-03T10:00:02.000Z'))!
    const aligned = addReplayToEpisode(later, replay('alice', '2026-08-03T10:00:00.000Z'))!

    expect(aligned.segments.map(segment => [segment.id, segment.startTick])).toEqual([
      ['clip:bob', 40],
      ['clip:alice', 0],
    ])
  })

  it('rejects a duplicate replay view', () => {
    const episode = addReplayToEpisode(createEmptyEpisode(), replay('alice', '2026-08-03T10:00:00.000Z'))!
    expect(addReplayToEpisode(episode, replay('alice', '2026-08-03T10:00:00.000Z'))).toBeNull()
  })

  it('adds a replay with events but no first-person video as a data track', () => {
    const eventOnly = replay('client', '2026-08-03T10:00:00.000Z')
    delete eventOnly.video

    const episode = addReplayToEpisode(createEmptyEpisode(), eventOnly)!

    expect(episode.tracks[0]).toMatchObject({
      kind: 'data',
      replay: {
        eventsUrl: '/events/client.jsonl',
        videoUrl: undefined,
      },
    })
    expect(episode.segments[0]).toMatchObject({ endTick: 200, startTick: 0 })
  })

  it('rejects a replay without video or events', () => {
    const empty = replay('empty', '2026-08-03T10:00:00.000Z')
    delete empty.eventsUrl
    delete empty.video

    expect(addReplayToEpisode(createEmptyEpisode(), empty)).toBeNull()
  })

  it('keeps editing behavior behind the editable projection flag', () => {
    const episode = addReplayToEpisode(createEmptyEpisode(), replay('alice', '2026-08-03T10:00:00.000Z'))!

    expect(createTimelineEngine(episode, null, undefined, false).getClip('clip:alice')?.clip).toMatchObject({
      movable: false,
      resizable: false,
    })
    expect(createTimelineEngine(episode, null, undefined, true).getClip('clip:alice')?.clip).toMatchObject({
      movable: true,
      resizable: true,
    })
  })
})
