import type { EditorWorkspaceContext } from '../../editor/workspaceContext'
import type { TimelineDataTrackProvider } from '../data-tracks/types'

import { defaultTimelineRendererTheme } from '@proj-airi/canvas-timeline-renderer'
import { createPinia, setActivePinia } from 'pinia'
import { describe, expect, it } from 'vitest'
import { render } from 'vitest-browser-vue'
import { defineComponent, h, provide, shallowRef, useTemplateRef } from 'vue'

import TimelineEditor from './TimelineEditor.vue'
import TimelineTrackHeaders from './TimelineTrackHeaders.vue'

import { editorWorkspaceContextKey } from '../../editor/workspaceContext'
import { useTimelineSession } from '../composables/useTimelineSession'
import { testReplay } from '../fixtures/replays'
import { buildTimelineLayout } from '../layout'
import { addReplaysToEpisode, createEmptyEpisode } from '../replay'
import { useEpisodeStore } from '../stores/episode'

import 'splitpanes/dist/splitpanes.css'

const RULER = defaultTimelineRendererTheme.metrics.rulerHeight
const ALICE_LANE = 'lane:session:s1:uuid:uuid-alice'

const markersProvider: TimelineDataTrackProvider = {
  describe: target => target.playerName === 'alice'
    ? [{
        key: 'markers',
        label: 'Markers',
        load: async () => [
          { color: '#f43f5e', id: 'seen', kind: 'point', label: 'Seen', serverTick: 150, tooltip: 'Container seen' },
          { color: '#22c55e', endServerTick: 260, id: 'open', kind: 'interval', label: 'Open', startServerTick: 200 },
        ],
      }]
    : [],
  id: 'test.markers',
  scope: 'player',
}

function mountTimeline(height = 600) {
  const pinia = createPinia()
  setActivePinia(pinia)
  const store = useEpisodeStore(pinia)
  const episode = shallowRef(store.episode)
  let session!: ReturnType<typeof useTimelineSession>
  const Host = defineComponent({
    setup() {
      session = useTimelineSession(episode, store.commitSegmentEdit, true, { providers: () => [markersProvider] })
      provide(editorWorkspaceContextKey, { addReplay: store.addReplay, catalog: { replays: shallowRef([]) } } as unknown as EditorWorkspaceContext)
      return () => h('div', { style: { height: `${height}px`, width: '1000px' } }, [
        h(TimelineEditor, {
          canRedo: store.canRedo,
          canUndo: store.canUndo,
          episode: episode.value,
          onCutSegment: (segmentId: string, tick: number) => store.cutSegment(segmentId, tick),
          onReorderTrack: store.reorderTrack,
          session,
        }),
      ])
    },
  })
  const sync = () => {
    episode.value = store.episode
  }
  return { Host, session: () => session, store, sync }
}

function pointer(canvas: HTMLCanvasElement, type: string, x: number, y: number): void {
  const bounds = canvas.getBoundingClientRect()
  canvas.dispatchEvent(new PointerEvent(type, { bubbles: true, button: 0, buttons: 1, clientX: bounds.left + x, clientY: bounds.top + y, pointerId: 3, pointerType: 'mouse' }))
}

function rowLabels(container: HTMLElement): string[] {
  return [...container.querySelectorAll<HTMLElement>('[data-track-id]')]
    .sort((left, right) => Number.parseFloat(left.style.top) - Number.parseFloat(right.style.top))
    .map(row => `${row.dataset.trackRole}:${row.querySelector('p')?.firstChild?.textContent?.trim() ?? ''}`)
}

describe('timeline lane groups', () => {
  it('shows session groups, player lanes, data tracks and the alignment label', async () => {
    const { Host, session, store, sync } = mountTimeline()
    store.addSession('s1', [
      testReplay('bob', { end: 320, player: 'bob', sessionId: 's1', start: 120, startedAt: '2026-10-10T10:00:01.000Z' }),
      testReplay('alice', { end: 300, player: 'alice', sessionId: 's1', start: 100, startedAt: '2026-10-10T10:00:00.000Z' }),
      testReplay('other-session', { sessionId: 's9' }),
    ])
    store.addReplay(testReplay('carol', { end: 250, player: 'carol', sessionId: 's2', start: 50, startedAt: '2026-10-10T10:00:10.000Z' }))
    sync()
    const screen = await render(Host)

    try {
      expect(store.canUndo).toBe(true)
      expect(store.history.undo).toHaveLength(2)
      await expect.element(screen.getByTestId('timeline-alignment')).toHaveTextContent('Sessions aligned by wall clock')
      await expect.poll(() => rowLabels(screen.container)).toEqual([
        'session:Session s1',
        'world:World',
        'primary:alice',
        'data:Markers',
        'primary:bob',
        'session:Session s2',
        'world:World',
        'primary:carol',
      ])
      expect(screen.container.querySelector(`[data-track-id="session:s2:header"]`)?.textContent).toContain('wall clock')
      await expect.poll(() => session().dataTracks.value[0]?.status).toBe('ready')

      // Collapse the first session: its world slot, lanes and data tracks disappear.
      await screen.getByRole('button', { name: 'Collapse Session s1' }).click()
      await expect.poll(() => rowLabels(screen.container)).toEqual(['session:Session s1', 'session:Session s2', 'world:World', 'primary:carol'])
      await screen.getByRole('button', { name: 'Expand Session s1' }).click()
      await expect.poll(() => rowLabels(screen.container)).toHaveLength(8)

      // Click the point marker (Server tick 150 = episode tick 50) on the data row.
      const dataRow = session().layout.value.rows.find(row => row.role === 'data')!
      const canvas = screen.container.querySelector<HTMLCanvasElement>('[role="application"]')!
      Object.defineProperty(canvas, 'setPointerCapture', { configurable: true, value: () => {} })
      const y = RULER + dataRow.top + dataRow.height / 2
      const engine = session().engine.value
      const x = (tick: number) => tick / 20 * engine.zoomScale - engine.scrollLeft
      pointer(canvas, 'pointermove', x(50) + 1, y)
      await expect.element(screen.getByTestId('timeline-data-tooltip')).toHaveTextContent('Container seen')
      pointer(canvas, 'pointerdown', x(50) + 1, y)
      pointer(canvas, 'pointerup', x(50) + 1, y)
      await expect.poll(() => session().selectedDataItem.value?.item.id).toBe('seen')
      expect(session().selectedDataItem.value).toMatchObject({ episodeTick: 50, placement: { connectionId: 'alice' }, serverTick: 150 })

      // Click the interval (episode ticks 100–160).
      pointer(canvas, 'pointerdown', x(130), y)
      pointer(canvas, 'pointerup', x(130), y)
      await expect.poll(() => session().selectedDataItem.value?.item.id).toBe('open')

      // Clicking an empty area clears the selection.
      pointer(canvas, 'pointerdown', x(190), y)
      pointer(canvas, 'pointerup', x(190), y)
      await expect.poll(() => session().selectedDataItem.value).toBeNull()
    }
    finally {
      await screen.unmount()
    }
  })

  it('cuts the selected clip at the playhead and keeps both halves on the same lane', async () => {
    const { Host, session, store, sync } = mountTimeline()
    store.addReplay(testReplay('alice', { end: 300, player: 'alice', sessionId: 's1', start: 100 }))
    sync()
    const screen = await render(Host)
    try {
      session().selectSegment('play:alice:primary')
      session().seekToTick(80)
      await screen.getByRole('button', { name: 'Cut selected segment at the playhead' }).click()
      expect(store.episode.placements.map(placement => [placement.laneId, placement.startTick, placement.endTick])).toEqual([
        [ALICE_LANE, 0, 80],
        [ALICE_LANE, 80, 200],
      ])
      sync()
      await expect.poll(() => session().engine.value.getState().tracks.find(track => track.id === ALICE_LANE)?.clips.length).toBe(2)
      await expect.poll(() => screen.container.querySelectorAll('[data-track-role="primary"]').length).toBe(1)

      store.undo()
      expect(store.episode.placements).toHaveLength(1)
      store.undo()
      expect(store.episode.placements).toHaveLength(0)
    }
    finally {
      await screen.unmount()
    }
  })

  it('virtualizes rows with mixed heights from known offsets', async () => {
    const replays = Array.from({ length: 60 }, (_, index) => testReplay(`player-${index}`, { player: `player-${index}`, sessionId: 's1' }))
    const episode = addReplaysToEpisode(createEmptyEpisode(), replays)!
    const layout = buildTimelineLayout(episode, [], new Set())
    const scrollTop = shallowRef(0)
    const Host = defineComponent({
      setup() {
        const scroller = useTemplateRef<HTMLDivElement>('scroller')
        return () => h('div', { ref: 'scroller', style: { height: '240px', overflowY: 'auto' } }, [
          h(TimelineTrackHeaders, { alignmentLabel: 'Aligned by server tick', editable: false, layout, scrollContainer: scroller.value, scrollTop: scrollTop.value, sessions: episode.sessions }),
        ])
      },
    })
    const screen = await render(Host)
    try {
      await expect.poll(() => screen.container.querySelector('[aria-label="Timeline rows"]')?.getAttribute('data-row-count')).toBe('62')
      await expect.poll(() => screen.container.querySelectorAll('[data-track-id]').length).toBeLessThan(20)
      expect(screen.container.querySelector('[data-track-id="session:s1:header"]')).not.toBeNull()

      scrollTop.value = 40 * 64
      await expect.poll(() => screen.container.querySelector('[data-track-id="lane:session:s1:uuid:uuid-player-40"]')).not.toBeNull()
      expect(screen.container.querySelector('[data-track-id="session:s1:header"]')).toBeNull()
      const row = screen.container.querySelector<HTMLElement>('[data-track-id="lane:session:s1:uuid:uuid-player-40"]')!
      // Header 28 + world 32 + 40 lanes of 64.
      expect(row.style.top).toBe(`${28 + 32 + 40 * 64}px`)
    }
    finally {
      await screen.unmount()
    }
  })
})
