import type { RecorderMinecraftApiV1Replay } from '@proj-airi/recorder-minecraft-api'

import { describe, expect, it } from 'vitest'

import { createTimelineEngine } from './core/adapter'
import { addReplayToEpisode, commitPlacementEdit, createEmptyEpisode, cutPlacement, deletePlacement, reorderPlacementTracks } from './replay'

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
    expect(createEmptyEpisode()).toMatchObject({ durationTicks: 0, placements: [], segments: [], tracks: [] })
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
      ['play:bob:primary', 40],
      ['play:alice:primary', 0],
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

    expect(createTimelineEngine(episode, null, undefined, false).getClip('play:alice:primary')?.clip).toMatchObject({
      movable: false,
      resizable: false,
    })
    expect(createTimelineEngine(episode, null, undefined, true).getClip('play:alice:primary')?.clip).toMatchObject({
      movable: true,
      resizable: true,
    })
  })

  it('projects extension intervals and points through the Play placement', () => {
    const episode = addReplayToEpisode(createEmptyEpisode(), replay('alice', '2026-08-03T10:00:00.000Z'), [{
      descriptor: { extensionType: 'airicraft.planner' },
      items: [
        { color: '#8b5cf6', data: {}, endServerTick: 140, id: 'call-1', kind: 'interval', label: 'Call 1', startServerTick: 120 },
        { color: '#f59e0b', data: {}, id: 'call-1:applied', kind: 'point', label: 'Applied 1', serverTick: 150 },
      ],
      label: 'Airicraft planner',
      viewId: 'extension:airicraft.planner',
    }])!

    expect(episode.tracks.map(track => [track.role, track.label])).toEqual([
      ['primary', 'alice · test-server'],
      ['extension', 'Airicraft planner'],
    ])
    expect(episode.segments.map(segment => [segment.label, segment.startTick, segment.endTick, segment.editable])).toEqual([
      ['alice · test-server', 0, 200, true],
      ['Call 1', 20, 40, false],
      ['Applied 1', 50, 51, false],
    ])
  })

  it('moves and trims every track through one placement', () => {
    const added = addReplayToEpisode(createEmptyEpisode(), replay('alice', '2026-08-03T10:00:00.000Z'), [{
      descriptor: { extensionType: 'airicraft.planner' },
      items: [{ color: '#8b5cf6', data: {}, endServerTick: 160, id: 'call-1', kind: 'interval', label: 'Call 1', startServerTick: 120 }],
      label: 'Airicraft planner',
      viewId: 'extension:airicraft.planner',
    }])!
    const moved = commitPlacementEdit(added, 'play:alice:primary', 10, 210, 'play:alice:primary')!

    expect(moved.segments.map(segment => [segment.startTick, segment.endTick])).toEqual([[10, 210], [30, 70]])

    const trimmed = commitPlacementEdit(moved, 'play:alice:primary', 20, 200, 'play:alice:primary')!
    expect(trimmed.placements[0]).toMatchObject({ sourceEndServerTick: 290, sourceStartServerTick: 110 })
    expect(trimmed.segments.map(segment => [segment.startTick, segment.endTick])).toEqual([[20, 200], [30, 70]])
  })

  it('removes and reorders all tracks for a placement as one group', () => {
    const extension = [{
      descriptor: { extensionType: 'airicraft.planner' },
      items: [],
      label: 'Airicraft planner',
      viewId: 'extension:airicraft.planner' as const,
    }]
    const first = addReplayToEpisode(createEmptyEpisode(), replay('alice', '2026-08-03T10:00:00.000Z'), extension)!
    const second = addReplayToEpisode(first, replay('bob', '2026-08-03T10:00:02.000Z'), extension)!
    const reordered = reorderPlacementTracks(second, 0, 2)!

    expect(reordered.tracks.map(track => track.placementId)).toEqual(['play:bob', 'play:bob', 'play:alice', 'play:alice'])

    const removed = deletePlacement(reordered, 'play:alice:primary')!
    expect(removed.placements.map(placement => placement.id)).toEqual(['play:bob'])
    expect(removed.tracks.every(track => track.placementId === 'play:bob')).toBe(true)
  })

  it('keeps both sides of a cut grouped with their extension tracks', () => {
    const added = addReplayToEpisode(createEmptyEpisode(), replay('alice', '2026-08-03T10:00:00.000Z'), [{
      descriptor: { extensionType: 'airicraft.planner' },
      items: [{ color: '#8b5cf6', data: {}, endServerTick: 220, id: 'call-1', kind: 'interval', label: 'Call 1', startServerTick: 180 }],
      label: 'Airicraft planner',
      viewId: 'extension:airicraft.planner',
    }])!
    const cut = cutPlacement(added, 'play:alice:primary', 100)!

    expect(cut.tracks.map(track => track.placementId)).toEqual([
      'play:alice',
      'play:alice',
      'play:alice:cut:3',
      'play:alice:cut:3',
    ])
    expect(cut.segments.map(segment => [segment.placementId, segment.label, segment.startTick, segment.endTick])).toEqual([
      ['play:alice', 'alice · test-server', 0, 100],
      ['play:alice', 'Call 1', 80, 100],
      ['play:alice:cut:3', 'alice · test-server', 100, 200],
      ['play:alice:cut:3', 'Call 1', 100, 120],
    ])
  })
})
