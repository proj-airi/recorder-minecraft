import type { EditorWorkspaceContext } from '../../editor/workspaceContext'
import type { EpisodeDraft } from '../../timeline/domain'

import { expect, it, vi } from 'vitest'
import { render } from 'vitest-browser-vue'
import { defineComponent, h, nextTick, provide, shallowRef } from 'vue'

import InputMonitorPanel from './InputMonitorPanel.vue'

import { editorWorkspaceContextKey } from '../../editor/workspaceContext'
import { testPlacement } from '../../timeline/fixtures/replays'

it('shows inputs for the selected clip at the Server tick its placement maps to', async () => {
  const fetchMock = vi.fn(() => Promise.resolve(new Response([
    '{"identity":{"serverTick":"115"},"controlState":{"state":{"forward":false}}}',
    '{"identity":{"serverTick":"125"},"controlState":{"state":{"forward":true}}}',
  ].join('\n'), { status: 200 })))
  vi.stubGlobal('fetch', fetchMock)

  const selectedSegmentId = shallowRef<null | string>(null)
  // Alice's clip is trimmed (its source starts at Server tick 110, 10 ticks into the Play) and
  // moved to episode tick 40. Playhead 55 therefore shows Server tick 125; the old
  // `startServerTick + offset` mapping would have read tick 115.
  const playheadTick = shallowRef(55)
  const episode: EpisodeDraft = {
    durationTicks: 200,
    id: 'input-selection',
    placements: [
      testPlacement({
        connectionId: 'alice',
        endTick: 130,
        playEndServerTick: 200,
        playStartServerTick: 100,
        source: { connectionId: 'alice', eventsUrl: '/events/alice.jsonl', playerName: 'Alice', serverName: 'Test server', startServerTick: '100' },
        sourceEndServerTick: 200,
        sourceStartServerTick: 110,
        startTick: 40,
      }),
      testPlacement({ connectionId: 'plain', laneId: 'lane:plain' }),
    ],
    revision: 1,
    segments: [
      { color: '#31b899', editable: true, endTick: 130, id: 'clip:alice', label: 'Alice', placementId: 'play:alice', startTick: 40, trackId: 'lane:alice' },
      { color: '#3d8bd9', editable: true, endTick: 100, id: 'clip:plain', label: 'Plain video', placementId: 'play:plain', startTick: 0, trackId: 'lane:plain' },
      { color: '#71717a', editable: false, endTick: 130, id: 'world', label: 'World', startTick: 0, trackId: 'world' },
    ],
    sessions: [],
    title: 'Input selection',
    tracks: [],
  }
  const context = {
    episode: () => episode,
    session: { playheadTick, selectedSegmentId },
  } as unknown as EditorWorkspaceContext
  const Host = defineComponent({
    setup() {
      provide(editorWorkspaceContextKey, context)
      return () => h(InputMonitorPanel)
    },
  })
  const screen = await render(Host)

  try {
    await expect.element(screen.getByText('Select a replay track in the timeline to inspect its inputs.')).toBeVisible()
    expect(fetchMock).not.toHaveBeenCalled()

    selectedSegmentId.value = 'world'
    await nextTick()
    await expect.element(screen.getByText('Select a replay track in the timeline to inspect its inputs.')).toBeVisible()

    selectedSegmentId.value = 'clip:plain'
    await nextTick()
    await expect.element(screen.getByText('The selected replay does not expose an events stream.')).toBeVisible()
    expect(fetchMock).not.toHaveBeenCalled()

    selectedSegmentId.value = 'clip:alice'
    await nextTick()
    await expect.element(screen.getByLabelText('Reconstructed keyboard controls').getByText('Tick 125', { exact: true })).toBeVisible()
    expect(fetchMock).toHaveBeenCalledOnce()
  }
  finally {
    await screen.unmount()
    vi.unstubAllGlobals()
  }
})
