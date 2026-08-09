import type { EditorWorkspaceContext } from '../../editor/workspaceContext'
import type { EpisodeDraft } from '../../timeline/domain'

import { expect, it, vi } from 'vitest'
import { render } from 'vitest-browser-vue'
import { defineComponent, h, nextTick, provide, shallowRef } from 'vue'

import InputMonitorPanel from './InputMonitorPanel.vue'

import { editorWorkspaceContextKey } from '../../editor/workspaceContext'

it('shows inputs only for the selected replay track at its timeline-local tick', async () => {
  const fetchMock = vi.fn(() => Promise.resolve(new Response([
    '{"identity":{"serverTick":"125"},"controlState":{"state":{"forward":true}}}',
  ].join('\n'), { status: 200 })))
  vi.stubGlobal('fetch', fetchMock)

  const selectedSegmentId = shallowRef<null | string>(null)
  const playheadTick = shallowRef(65)
  const episode: EpisodeDraft = {
    durationTicks: 200,
    id: 'input-selection',
    revision: 1,
    segments: [
      { color: '#31b899', endTick: 140, id: 'clip:alice', label: 'Alice', startTick: 40, trackId: 'replay:alice' },
      { color: '#3d8bd9', endTick: 100, id: 'clip:plain', label: 'Plain video', startTick: 0, trackId: 'video:plain' },
    ],
    title: 'Input selection',
    tracks: [
      {
        id: 'replay:alice',
        kind: 'data',
        label: 'Alice',
        replay: {
          connectionId: 'alice',
          eventsUrl: '/events/alice.jsonl',
          playerName: 'Alice',
          serverName: 'Test server',
          startServerTick: '100',
        },
      },
      { id: 'video:plain', kind: 'video', label: 'Plain video' },
    ],
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

    selectedSegmentId.value = 'clip:plain'
    await nextTick()
    await expect.element(screen.getByText('Select a replay track in the timeline to inspect its inputs.')).toBeVisible()
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
