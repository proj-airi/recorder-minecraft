import type { RecorderMinecraftApiV1Replay } from '@proj-airi/recorder-minecraft-api'

import type { EditorWorkspaceContext } from '../editor/workspaceContext'
import type { TimelineSession } from '../timeline/composables/useTimelineSession'
import type { EpisodeDraft } from '../timeline/domain'

import { afterEach, describe, expect, it, vi } from 'vitest'
import { render } from 'vitest-browser-vue'
import { defineComponent, h, provide, shallowRef } from 'vue'

import MultiViewMonitorPanel from './components/MultiViewMonitorPanel.vue'

import { editorWorkspaceContextKey } from '../editor/workspaceContext'
import { testReplay } from '../timeline/fixtures/replays'
import { addReplaysToEpisode, commitPlacementEdit, createEmptyEpisode, cutPlacement, replaySource } from '../timeline/replay'
import {
  frameIndexAnchors,
  monitorLaneViews,
  parseFpvManifest,
  parseFramesIndex,
  videoPositionForServerTick,
} from './videoTime'

const framesJsonl = [
  // Rendering started 7 ticks after the Play began, like the recorded fpv frames.
  '{"ordinal":"1","serverTick":"1422","path":"frame_000001.png"}',
  '{"ordinal":"3","serverTick":"1424","path":"frame_000003.png"}',
  '{"ordinal":"2","serverTick":"1423","path":"frame_000002.png"}',
  'not json',
  '{"ordinal":"4","serverTick":"1430","path":"frame_000004.png"}',
].join('\n')

// Trimmed from an Airicraft `renders/fpv.json` (proj-airi/airicraft#109): the host waits ~315 s for
// players while the capture holds one image, then anchors follow the Server ticks closely. The
// first anchor omits `videoSeconds` because ProtoJSON drops zero values.
const airiManifest = JSON.stringify({
  complete: true,
  connectionId: 'airi',
  durationSeconds: 492,
  frameCount: '9840',
  frames: [
    { serverTick: '22' },
    { serverTick: '6335', videoSeconds: 315.6 },
    { serverTick: '6337', videoSeconds: 315.8 },
    { serverTick: '6343', videoSeconds: 316 },
    { serverTick: '7597', videoSeconds: 378.7 },
    { serverTick: '7599', videoSeconds: 378.8 },
    { serverTick: '7602', videoSeconds: 378.95 },
    { serverTick: '9543', videoSeconds: 476 },
    { serverTick: '9545', videoSeconds: 476.1 },
  ],
  framesPerSecond: 20,
  schemaVersion: 1,
})

function airiReplay(timing: Partial<NonNullable<NonNullable<RecorderMinecraftApiV1Replay['video']>['timing']>> = {}): RecorderMinecraftApiV1Replay {
  const replay = testReplay('airi', { end: 9_901, player: 'Airi', sessionId: 's1', start: 11 })
  replay.video = {
    durationSeconds: 492,
    framesPerSecond: 20,
    timing: { anchorCount: '9', firstServerTick: '22', format: 'FORMAT_FPV_MANIFEST', lastServerTick: '9545', url: '/assets/airi/renders/fpv.json', ...timing },
    url: '/assets/airi/renders/fpv.mp4',
  }
  return replay
}

function episodeWithTwoPlayers() {
  const alice = testReplay('alice', { end: 2_721, player: 'alice', sessionId: 's1', start: 1_415 })
  ;(alice as Record<string, unknown>).framesIndexUrl = '/assets/alice/frames.jsonl'
  const bob = testReplay('bob', { end: 2_700, player: 'bob', sessionId: 's1', start: 1_500 })
  return addReplaysToEpisode(createEmptyEpisode(), [alice, bob])!
}

async function renderMonitor(episode: EpisodeDraft, playheadTick: number) {
  const playhead = shallowRef(playheadTick)
  const session = { isPlaying: shallowRef(false), playheadTick: playhead } as unknown as TimelineSession
  const context = { episode: () => episode, session } as unknown as EditorWorkspaceContext
  const Host = defineComponent({
    setup() {
      provide(editorWorkspaceContextKey, context)
      return () => h(MultiViewMonitorPanel)
    },
  })
  const screen = await render(Host)
  const tile = (connectionId: string) => screen.container.querySelector<HTMLElement>(`article[data-connection-id="${connectionId}"]`)
  return { playhead, screen, tile }
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('video timing files', () => {
  it('parses fpv.json with omitted zeros and string int64 ticks', () => {
    const anchors = parseFpvManifest(airiManifest)
    expect([...anchors.serverTicks]).toEqual([22, 6335, 6337, 6343, 7597, 7599, 7602, 9543, 9545])
    expect(anchors.videoSeconds[0]).toBe(0)
    expect(anchors.videoSeconds[1]).toBe(315.6)
    expect(anchors.framesPerSecond).toBe(20)

    // An empty anchor is {videoSeconds: 0, serverTick: 0}; numeric int64 values are accepted too.
    const sparse = parseFpvManifest('{"frames":[{},{"videoSeconds":1.5,"serverTick":30}]}')
    expect([...sparse.serverTicks]).toEqual([0, 30])
    expect([...sparse.videoSeconds]).toEqual([0, 1.5])
    expect(sparse.framesPerSecond).toBeUndefined()
    expect(parseFpvManifest('{}').serverTicks).toHaveLength(0)
  })

  it('seeks to the last fpv.json anchor at or before the Server tick', () => {
    const anchors = parseFpvManifest(airiManifest)
    const at = (tick: number) => videoPositionForServerTick(anchors, tick)!

    // Before the first anchor the first image is held.
    expect(at(11)).toMatchObject({ hold: 'before', windowStart: 0 })
    expect(at(11).time).toBeCloseTo(0.025)
    // Exactly on an anchor: half a frame into it.
    expect(at(6335).hold).toBeUndefined()
    expect(at(6335).time).toBeCloseTo(315.625)
    // Between anchors: still the earlier anchor; its image lasts until the next anchor.
    expect(at(7600).time).toBeCloseTo(378.825)
    expect(at(7600).windowEnd).toBeCloseTo(378.95)
    // During the empty-host pause the single held image is shown, not (tick - start) / 20 ≈ 149 s.
    expect(at(3000).time).toBeCloseTo(0.025)
    expect(at(3000)).toMatchObject({ windowEnd: 315.6, windowStart: 0 })
    expect(at(6334).time).toBeCloseTo(0.025)
    // After the last anchor the last image is held.
    expect(at(9545).hold).toBeUndefined()
    expect(at(9800)).toMatchObject({ hold: 'after' })
    expect(at(9800).time).toBeCloseTo(476.125)
  })

  it('maps Server ticks to video time through the frames index', () => {
    const index = parseFramesIndex(framesJsonl)
    expect([...index.serverTicks]).toEqual([1422, 1423, 1424, 1430])
    const at = (tick: number, framesPerSecond = 20) => videoPositionForServerTick(frameIndexAnchors(index, framesPerSecond), tick)!.time
    expect(at(1_422)).toBeCloseTo(0.5 / 20)
    expect(at(1_429)).toBeCloseTo(2.5 / 20)
    expect(at(1_430, 30)).toBeCloseTo(3.5 / 30)
    // Before the first rendered frame the first frame is shown.
    expect(at(1_415)).toBeCloseTo(0.5 / 20)
  })

  it('reads timing from the catalog and falls back to the frames index URL', () => {
    expect(replaySource(airiReplay()).videoTiming).toEqual({
      // `complete` is omitted by ProtoJSON when false.
      complete: false,
      firstServerTick: 22,
      format: 'fpv-manifest',
      lastServerTick: 9545,
      url: '/assets/airi/renders/fpv.json',
    })
    const legacy = testReplay('old')
    legacy.framesIndexUrl = '/assets/old/frames.jsonl'
    expect(replaySource(legacy).videoTiming).toEqual({ complete: true, format: 'frame-index', url: '/assets/old/frames.jsonl' })
    expect(replaySource(testReplay('none')).videoTiming).toBeUndefined()
    expect(replaySource(airiReplay({ format: 'FORMAT_UNSPECIFIED' })).videoTiming).toBeUndefined()
  })
})

describe('monitor tiles', () => {
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
    const { playhead, screen, tile } = await renderMonitor(moved, 2)
    try {
      await expect.poll(() => tile('alice')?.dataset.serverTick).toBe('1427')
      await expect.poll(() => tile('alice')?.dataset.videoTime).toBe((2.5 / 20).toFixed(3))
      expect(tile('alice')?.dataset.timingFormat).toBe('frame-index')
      expect(fetchMock).toHaveBeenCalledOnce()
      expect(tile('bob')?.textContent).toContain('Waiting for clip')

      // Bob has no timing: the old start-aligned assumption, flagged as approximate.
      playhead.value = 100
      await expect.poll(() => tile('bob')?.dataset.serverTick).toBe('1515')
      expect(tile('bob')?.dataset.videoTime).toBe((15 / 20).toFixed(3))
      expect(tile('bob')?.querySelector('[data-badge="timing-unknown"]')?.textContent).toBe('timing unknown')
      expect(tile('alice')?.querySelector('[data-badge="timing-unknown"]')).toBeNull()
    }
    finally {
      await screen.unmount()
    }
  })

  it('maps fpv.json anchors, shows loading, and flags an incomplete manifest', async () => {
    let respond!: (response: Response) => void
    const fetchMock = vi.fn(() => new Promise<Response>((resolve) => {
      respond = resolve
    }))
    vi.stubGlobal('fetch', fetchMock)
    // Episode tick 0 is Server tick 11; tick 2989 is Server tick 3000, inside the pause.
    const episode = addReplaysToEpisode(createEmptyEpisode(), [airiReplay({ url: '/assets/airi-loading/fpv.json' })])!
    const { playhead, screen, tile } = await renderMonitor(episode, 2_989)
    try {
      await expect.poll(() => tile('airi')?.dataset.timingStatus).toBe('loading')
      expect(tile('airi')?.textContent).toContain('Loading video timing')
      expect(tile('airi')?.dataset.videoTime).toBeUndefined()

      respond(new Response(airiManifest, { status: 200 }))
      await expect.poll(() => tile('airi')?.dataset.timingStatus).toBe('ready')
      expect(tile('airi')?.dataset.serverTick).toBe('3000')
      expect(tile('airi')?.dataset.videoTime).toBe('0.025')
      expect(tile('airi')?.querySelector('[data-badge="incomplete"]')).not.toBeNull()
      expect(tile('airi')?.querySelector('[data-badge="timing-unknown"]')).toBeNull()

      playhead.value = 7_589
      await expect.poll(() => tile('airi')?.dataset.videoTime).toBe('378.825')

      playhead.value = 0
      await expect.poll(() => tile('airi')?.dataset.videoHold).toBe('before')
      expect(tile('airi')?.querySelector('[data-badge="waiting"]')).not.toBeNull()
    }
    finally {
      await screen.unmount()
    }
  })

  it('shows an empty state for a Play without video', async () => {
    const episode = addReplaysToEpisode(createEmptyEpisode(), [testReplay('quiet', { video: false })])!
    const { screen, tile } = await renderMonitor(episode, 10)
    try {
      await expect.poll(() => tile('quiet')?.querySelector('[data-testid="monitor-no-video"]')).not.toBeNull()
      expect(tile('quiet')?.textContent).toContain('No video for this Play')
      expect(tile('quiet')?.textContent).toContain('recorder-minecraft render')
      expect(tile('quiet')?.querySelector('video')).toBeNull()
      expect(tile('quiet')?.querySelector('[data-badge]')).toBeNull()
    }
    finally {
      await screen.unmount()
    }
  })
})
