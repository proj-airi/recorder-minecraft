import type { PlayExtensionModule } from '../../extensions/domain'
import type { EpisodeDraft } from '../domain'
import type { TimelineDataItem, TimelineDataTrack, TimelineDataTrackProvider } from './types'

import { describe, expect, it, vi } from 'vitest'
import { defineComponent, effectScope, nextTick, shallowRef } from 'vue'

import { describeTargets, useTimelineDataTracks } from '../composables/useTimelineDataTracks'
import { testReplay } from '../fixtures/replays'
import { buildTimelineLayout } from '../layout'
import { addReplaysToEpisode, createEmptyEpisode, cutPlacement } from '../replay'
import { createPlayExtensionDataTrackProvider, PLAY_EXTENSION_PROVIDER_ID } from './playExtensionProvider'
import { dataLaneEntryAt, hitTestDataLane, projectDataTrack } from './projection'

const ALICE_LANE = 'lane:session:s1:uuid:uuid-alice'

async function flush(): Promise<void> {
  for (let index = 0; index < 5; index += 1)
    await Promise.resolve()
  await nextTick()
}

function sessionEpisode(): EpisodeDraft {
  return addReplaysToEpisode(createEmptyEpisode(), [
    testReplay('alice', { end: 300, sessionId: 's1', start: 100, startedAt: '2026-10-10T10:00:00.000Z' }),
    testReplay('bob', { end: 320, sessionId: 's1', start: 120, startedAt: '2026-10-10T10:00:01.000Z' }),
  ])!
}

function track(episode: EpisodeDraft, targetKey: string, items: TimelineDataItem[]): TimelineDataTrack {
  const target = describeTargets(episode).find(candidate => candidate.key === targetKey)!
  return { descriptorKey: 'test', id: `data:test:${targetKey}:test`, items, label: 'Test', order: 0, providerId: 'test', status: 'ready', target }
}

describe('data track projection', () => {
  it('projects player items through the player clips, including both halves of a cut', () => {
    const episode = cutPlacement(sessionEpisode(), 'play:alice:primary', 50)!
    const lane = projectDataTrack(episode, track(episode, ALICE_LANE, [
      { id: 'p1', kind: 'point', serverTick: 110 },
      { id: 'p2', kind: 'point', serverTick: 170 },
      { endServerTick: 160, id: 'span', kind: 'interval', startServerTick: 140 },
      { id: 'outside', kind: 'point', serverTick: 9_999 },
    ]))

    expect([...lane.starts]).toEqual([10, 40, 50, 70])
    expect([...lane.ends]).toEqual([10, 50, 60, 70])
    // The interval is split at the cut: entries 1 and 2 come from the same source item.
    expect([...lane.itemIndexes]).toEqual([0, 2, 2, 1])
    expect(lane.placementIds).toEqual(['play:alice', 'play:alice', 'play:alice:cut:3', 'play:alice:cut:3'])
    expect(lane.message).toBeUndefined()
  })

  it('projects session items through the session anchor', () => {
    const episode = sessionEpisode()
    const lane = projectDataTrack(episode, track(episode, 'session:s1', [{ id: 'w', kind: 'point', serverTick: 150 }]))
    expect([...lane.starts]).toEqual([50])
    expect(lane.placementIds).toEqual([null])
  })

  it('hit-tests points by distance and intervals by containment', () => {
    const episode = sessionEpisode()
    const lane = projectDataTrack(episode, track(episode, ALICE_LANE, [
      { endServerTick: 160, id: 'a', kind: 'interval', startServerTick: 120 },
      { id: 'b', kind: 'point', serverTick: 140 },
    ]))
    expect(hitTestDataLane(lane, 41, 2)).toBe(lane.itemIndexes.indexOf(1))
    expect(hitTestDataLane(lane, 55, 2)).toBe(lane.itemIndexes.indexOf(0))
    expect(hitTestDataLane(lane, 80, 2)).toBe(-1)
    expect(dataLaneEntryAt(lane, 40)).toBe(lane.itemIndexes.indexOf(1))
    expect(dataLaneEntryAt(lane, 59)).toBe(lane.itemIndexes.indexOf(0))
    expect(dataLaneEntryAt(lane, 60)).toBe(-1)
  })

  it('reports loading, empty and error states as lane messages', () => {
    const episode = sessionEpisode()
    expect(projectDataTrack(episode, { ...track(episode, ALICE_LANE, []), status: 'loading' }).message).toBe('Loading…')
    expect(projectDataTrack(episode, { ...track(episode, ALICE_LANE, []), status: 'empty' }).message).toBe('No items')
    expect(projectDataTrack(episode, { ...track(episode, ALICE_LANE, []), error: 'HTTP 404', status: 'error' }).message).toBe('Failed to load: HTTP 404')
  })

  it('projects 10 000 points quickly', () => {
    const episode = sessionEpisode()
    const items: TimelineDataItem[] = Array.from({ length: 10_000 }, (_, index) => ({ id: `p${index}`, kind: 'point', serverTick: 100 + (index % 200) }))
    const started = performance.now()
    const lane = projectDataTrack(episode, track(episode, ALICE_LANE, items))
    expect(lane.starts.length).toBe(10_000)
    expect(performance.now() - started).toBeLessThan(250)
  })
})

describe('data track runtime', () => {
  it('lazily loads visible tracks and lays them out under their lane group', async () => {
    const episode = shallowRef(sessionEpisode())
    const loads: string[] = []
    const provider: TimelineDataTrackProvider = {
      describe: target => target.playerName === 'alice'
        ? [{ key: 'ok', label: 'Perception', load: async () => {
            loads.push('ok')
            return [{ id: 'x', kind: 'point', serverTick: 150 }]
          } }, { key: 'broken', label: 'Broken', load: async () => {
            loads.push('broken')
            throw new Error('HTTP 500')
          } }, { key: 'empty', label: 'Empty', load: async () => [] }]
        : [],
      id: 'test.player',
      scope: 'player',
    }
    const sessionProvider: TimelineDataTrackProvider = {
      describe: target => [{ key: 'world', label: `World events ${target.plays.length}`, load: async () => [{ endServerTick: 200, id: 'w', kind: 'interval', startServerTick: 150 }] }],
      id: 'test.session',
      scope: 'session',
    }
    const scope = effectScope()
    const runtime = scope.run(() => useTimelineDataTracks(episode, () => [provider, sessionProvider]))!

    try {
      expect(runtime.tracks.value.map(candidate => [candidate.id, candidate.status])).toEqual([
        [`data:test.player:${ALICE_LANE}:ok`, 'idle'],
        [`data:test.player:${ALICE_LANE}:broken`, 'idle'],
        [`data:test.player:${ALICE_LANE}:empty`, 'idle'],
        ['data:test.session:session:s1:world', 'idle'],
      ])
      expect(loads).toEqual([])

      const layout = buildTimelineLayout(episode.value, runtime.tracks.value, new Set([ALICE_LANE]))
      expect(layout.rows.map(row => [row.role, row.label])).toEqual([
        ['session', 'Session s1'],
        ['world', 'World'],
        ['data', 'World events 2'],
        ['primary', 'alice'],
        ['primary', 'bob'],
      ])
      runtime.ensureLoaded(layout.rows.filter(row => row.role === 'data').map(row => row.id))
      await flush()
      expect(loads).toEqual([])

      const expanded = buildTimelineLayout(episode.value, runtime.tracks.value, new Set())
      expect(expanded.rows.map(row => row.label)).toEqual(['Session s1', 'World', 'World events 2', 'alice', 'Broken', 'Empty', 'Perception', 'bob'])
      runtime.ensureLoaded(expanded.rows.filter(row => row.role === 'data').map(row => row.id))
      await flush()
      expect(loads.sort()).toEqual(['broken', 'ok'])
      expect(Object.fromEntries(runtime.tracks.value.map(candidate => [candidate.descriptorKey, candidate.status]))).toEqual({ broken: 'error', empty: 'empty', ok: 'ready', world: 'ready' })
      expect(runtime.tracks.value.find(candidate => candidate.descriptorKey === 'broken')?.error).toBe('HTTP 500')
      expect([...runtime.lanes.value.get(`data:test.player:${ALICE_LANE}:ok`)!.starts]).toEqual([50])

      const collapsedSession = buildTimelineLayout(episode.value, runtime.tracks.value, new Set(['session:s1']))
      expect(collapsedSession.rows.map(row => row.role)).toEqual(['session'])
    }
    finally {
      scope.stop()
    }
  })

  it('re-implements Play extension tracks on the contract', async () => {
    const module: PlayExtensionModule = {
      extensionType: 'test.planner',
      loadTrack: async descriptor => ({
        descriptor,
        items: [{ color: '#8b5cf6', data: { call: 1 }, endServerTick: 140, id: 'call-1', kind: 'interval', label: 'Call 1', startServerTick: 120 }],
        label: 'Planner',
      }),
      view: { component: defineComponent({ render: () => null }), icon: 'i-mingcute-ai-line', label: 'Planner' },
    }
    const replay = testReplay('alice', { sessionId: 's1' })
    replay.extensions = [{ extensionType: 'test.planner' }, { extensionType: 'unknown' }]
    const episode = shallowRef(addReplaysToEpisode(createEmptyEpisode(), [replay])!)
    const load = vi.spyOn(module, 'loadTrack')
    const scope = effectScope()
    const runtime = scope.run(() => useTimelineDataTracks(episode, () => [createPlayExtensionDataTrackProvider([module])]))!
    try {
      expect(runtime.tracks.value.map(candidate => [candidate.providerId, candidate.descriptorKey, candidate.viewId])).toEqual([
        [PLAY_EXTENSION_PROVIDER_ID, 'test.planner', 'extension:test.planner'],
      ])
      runtime.ensureLoaded(runtime.tracks.value.map(candidate => candidate.id))
      await flush()
      expect(load).toHaveBeenCalledOnce()
      expect(runtime.tracks.value[0]?.items[0]).toMatchObject({ connectionId: 'alice', id: 'alice:call-1', kind: 'interval', payload: { connectionId: 'alice', item: { id: 'call-1' } } })
      expect([...runtime.lanes.value.get(runtime.tracks.value[0]!.id)!.starts]).toEqual([20])
    }
    finally {
      scope.stop()
    }
  })
})
