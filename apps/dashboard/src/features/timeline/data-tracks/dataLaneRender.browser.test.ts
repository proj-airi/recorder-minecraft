import type { TimelineDataItem, TimelineDataTrack } from './types'

import { defaultTimelineRendererTheme, renderTimeline } from '@proj-airi/canvas-timeline-renderer'
import { describe, expect, it } from 'vitest'

import { describeTargets } from '../composables/useTimelineDataTracks'
import { createTimelineEngine } from '../core/adapter'
import { testReplay } from '../fixtures/replays'
import { buildTimelineLayout } from '../layout'
import { addReplaysToEpisode, createEmptyEpisode } from '../replay'
import { projectDataTrack } from './projection'

const WIDTH = 900
const HEIGHT = 240
const ALICE_LANE = 'lane:session:s1:uuid:uuid-alice'

function renderLane(items: TimelineDataItem[], selectedIndex = -1) {
  const episode = addReplaysToEpisode(createEmptyEpisode(), [testReplay('alice', { end: 2_100, sessionId: 's1', start: 100 })])!
  const target = describeTargets(episode).find(candidate => candidate.key === ALICE_LANE)!
  const track: TimelineDataTrack = { descriptorKey: 'test', id: 'data:test', items, label: 'Test', order: 0, providerId: 'test', status: 'ready', target }
  const layout = buildTimelineLayout(episode, [track], new Set())
  const engine = createTimelineEngine(episode, { layout })
  const lane = projectDataTrack(episode, track)
  const canvas = new OffscreenCanvas(WIDTH, HEIGHT)
  const context = canvas.getContext('2d')!
  const started = performance.now()
  renderTimeline(context, canvas, engine.getState(), 1, {}, {
    lanes: new Map([[lane.trackId, lane]]),
    selection: selectedIndex >= 0 ? { index: selectedIndex, trackId: lane.trackId } : null,
  })
  const elapsed = performance.now() - started
  const row = layout.rows.find(candidate => candidate.id === 'data:test')!
  const midY = defaultTimelineRendererTheme.metrics.rulerHeight + row.top + row.height / 2
  // zoomScale 18 px/s at 20 ticks/s: one episode tick is 0.9 px.
  const pixel = (tick: number, y = midY) => [...context.getImageData(Math.round(tick * 0.9), Math.round(y), 1, 1).data]
  return { elapsed, pixel, row }
}

describe('data lane rendering', () => {
  it('draws point markers and interval bars on the data row', () => {
    const { pixel } = renderLane([
      { color: '#ff0000', id: 'point', kind: 'point', serverTick: 300 },
      { color: '#00ff00', endServerTick: 800, id: 'span', kind: 'interval', startServerTick: 500 },
    ])

    const [red, green, blue] = pixel(200)
    expect(red).toBeGreaterThan(200)
    expect(green).toBeLessThan(60)
    expect(blue).toBeLessThan(60)

    const [ir, ig, ib] = pixel(550)
    expect(ig).toBeGreaterThan(ir + 40)
    expect(ig).toBeGreaterThan(ib + 40)

    // Nothing is drawn between the marker and the bar.
    const [er, eg] = pixel(300)
    expect(Math.abs(er - eg)).toBeLessThan(20)
  })

  it('highlights the selected point', () => {
    const items: TimelineDataItem[] = [{ color: '#0000ff', id: 'point', kind: 'point', serverTick: 300 }]
    const plain = renderLane(items)
    const selected = renderLane(items, 0)
    // The selected marker is larger, so a pixel just outside the plain marker becomes colored.
    expect(selected.pixel(200 + 5 / 0.9)).not.toEqual(plain.pixel(200 + 5 / 0.9))
  })

  it('aggregates thousands of points into a fast histogram', () => {
    const items: TimelineDataItem[] = Array.from({ length: 20_000 }, (_, index) => ({ color: '#ffaa00', id: `p${index}`, kind: 'point', serverTick: 100 + (index % 1_000) }))
    const { elapsed, pixel, row } = renderLane(items)
    const bottom = defaultTimelineRendererTheme.metrics.rulerHeight + row.top + row.height - 5
    const [red, green] = pixel(500, bottom)
    expect(red).toBeGreaterThan(150)
    expect(green).toBeGreaterThan(80)
    expect(elapsed).toBeLessThan(100)
  })
})
