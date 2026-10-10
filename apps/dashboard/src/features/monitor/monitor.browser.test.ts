import type { EditorWorkspaceContext } from '../editor/workspaceContext'
import type { TimelineSession } from '../timeline/composables/useTimelineSession'

import { describe, expect, it, vi } from 'vitest'
import { render } from 'vitest-browser-vue'
import { defineComponent, h, provide, shallowRef } from 'vue'

import MultiViewMonitorPanel from './components/MultiViewMonitorPanel.vue'

import { editorWorkspaceContextKey } from '../editor/workspaceContext'
import { testReplay } from '../timeline/fixtures/replays'
import { addReplaysToEpisode, commitPlacementEdit, createEmptyEpisode, cutPlacement } from '../timeline/replay'
import { monitorLaneViews, parseFramesIndex, videoTimeForServerTick } from './videoTime'

const framesJsonl = [
  // Rendering started 7 ticks after the Play began, like the recorded fpv frames.
  '{"ordinal":"1","serverTick":"1422","path":"frame_000001.png"}',
  '{"ordinal":"3","serverTick":"1424","path":"frame_000003.png"}',
  '{"ordinal":"2","serverTick":"1423","path":"frame_000002.png"}',
  'not json',
  '{"ordinal":"4","serverTick":"1430","path":"frame_000004.png"}',
].join('\n')

function episodeWithTwoPlayers() {
  const alice = testReplay('alice', { end: 2_721, player: 'alice', sessionId: 's1', start: 1_415 })
  ;(alice as Record<string, unknown>).framesIndexUrl = '/assets/alice/frames.jsonl'
  const bob = testReplay('bob', { end: 2_700, player: 'bob', sessionId: 's1', start: 1_500 })
  return addReplaysToEpisode(createEmptyEpisode(), [alice, bob])!
}

describe('monitor video time', () => {
  it('maps Server ticks to video time through the frames index', () => {
    const episode = episodeWithTwoPlayers()
    const alice = episode.placements[0]!
    const index = parseFramesIndex(framesJsonl)
    expect([...index.serverTicks]).toEqual([1422, 1423, 1424, 1430])
    expect(videoTimeForServerTick(alice, 1_422, index)).toBeCloseTo(0.5 / 20)
    expect(videoTimeForServerTick(alice, 1_429, index)).toBeCloseTo(2.5 / 20)
    expect(videoTimeForServerTick(alice, 1_430, index, 30)).toBeCloseTo(3.5 / 30)
    // Before the first rendered frame the first frame is shown.
    expect(videoTimeForServerTick(alice, 1_415, index)).toBeCloseTo(0.5 / 20)
    // Without an index the video is assumed to start at the Play's first tick.
    expect(videoTimeForServerTick(alice, 1_422, null)).toBeCloseTo(7 / 20)
  })

  it('shows one tile per player lane at the playhead', () => {
    const episode = cutPlacement(episodeWithTwoPlayers(), 'play:alice:primary', 400)!
    expect(monitorLaneViews(episode, 50).map(view => [view.lane.label, view.placement.id, view.state])).toEqual([
      ['alice', 'play:alice', 'active'],
      ['bob', 'play:bob', 'waiting'],
    ])
    expect(monitorLaneViews(episode, 500).map(view => [view.placement.id, view.state])).toEqual([
      ['play:alice:cut:3', 'active'],
      ['play:bob', 'active'],
    ])
    expect(monitorLaneViews(episode, 5_000).map(view => view.state)).toEqual(['ended', 'ended'])
  })

  it('respects trims and the frames index in every tile', async () => {
    const fetchMock = vi.fn(() => Promise.resolve(new Response(framesJsonl, { status: 200 })))
    vi.stubGlobal('fetch', fetchMock)
    // Trim 10 ticks off the start of Alice's clip; it still starts at episode tick 0.
    const base = episodeWithTwoPlayers()
    const episode = commitPlacementEdit(base, 'play:alice:primary', 10, base.placements[0]!.endTick, base.placements[0]!.laneId)!
    const moved = commitPlacementEdit(episode, 'play:alice:primary', 0, episode.placements[0]!.endTick - 10, episode.placements[0]!.laneId)!
    const playheadTick = shallowRef(2)
    const session = { isPlaying: shallowRef(false), playheadTick } as unknown as TimelineSession
    const context = { episode: () => moved, session } as unknown as EditorWorkspaceContext
    const Host = defineComponent({
      setup() {
        provide(editorWorkspaceContextKey, context)
        return () => h(MultiViewMonitorPanel)
      },
    })
    const screen = await render(Host)
    try {
      const tile = (connectionId: string) => screen.container.querySelector<HTMLElement>(`article[data-connection-id="${connectionId}"]`)
      await expect.poll(() => tile('alice')?.dataset.serverTick).toBe('1427')
      await expect.poll(() => tile('alice')?.dataset.videoTime).toBe((2.5 / 20).toFixed(3))
      expect(fetchMock).toHaveBeenCalledOnce()
      expect(tile('bob')?.textContent).toContain('Waiting for clip')

      playheadTick.value = 100
      await expect.poll(() => tile('bob')?.dataset.serverTick).toBe('1515')
      expect(tile('bob')?.dataset.videoTime).toBe((15 / 20).toFixed(3))
    }
    finally {
      await screen.unmount()
      vi.unstubAllGlobals()
    }
  })
})
