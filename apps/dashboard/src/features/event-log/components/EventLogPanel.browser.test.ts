import type { EditorWorkspaceContext } from '../../editor/workspaceContext'

import { afterEach, expect, it, vi } from 'vitest'
import { render } from 'vitest-browser-vue'
import { defineComponent, h, nextTick, provide, shallowRef } from 'vue'

import EventLogPanel from './EventLogPanel.vue'

import { editorWorkspaceContextKey } from '../../editor/workspaceContext'
import { testReplay } from '../../timeline/fixtures/replays'
import { addReplaysToEpisode, createEmptyEpisode } from '../../timeline/replay'
import { clearCaptureEventsCache } from '../useCaptureEvents'

import 'uno.css'

function click(slot: number): Record<string, unknown> {
  return { packetApply: { packet: { actionKind: 'inventory', containerId: 1, identity: { packetType: 'serverbound/minecraft:container_click' }, slotNumber: slot } } }
}

function line(tick: number, sequence: number, record: Record<string, unknown>): string {
  return JSON.stringify({ identity: { sequence: String(sequence), serverTick: String(tick) }, ...record })
}

// Alice opens a chest at Server tick 110. Bob clicks container slots every 2 ticks from 120 to
// 518, which makes the list long enough to scroll.
const files: Record<string, string> = {
  '/events/alice.jsonl': [
    line(110, 1, { containerView: { containerId: 1, kind: 'CONTAINER_VIEW_KIND_OPENED', source: { blockEntityType: 'minecraft:chest', blockPos: { x: 4, y: -60, z: 4 } } } }),
    ...Array.from({ length: 50 }, (_, index) => line(100 + index, 10 + index, { controlState: { state: { forward: true } } })),
  ].join('\n'),
  '/events/bob.jsonl': Array.from({ length: 200 }, (_, index) => line(120 + index * 2, index, click(index))).join('\n'),
}

afterEach(() => {
  clearCaptureEventsCache()
  vi.unstubAllGlobals()
})

it('lists capture events of every player on the timeline, filters them, and follows the playhead', async () => {
  vi.stubGlobal('fetch', vi.fn((input: string | URL) => {
    const path = new URL(String(input), window.location.href).pathname
    return Promise.resolve(new Response(files[path] ?? '', { status: files[path] ? 200 : 404 }))
  }))
  const episode = addReplaysToEpisode(createEmptyEpisode(), [
    testReplay('alice', { end: 600, player: 'alice', sessionId: 's1', start: 100 }),
    testReplay('bob', { end: 600, player: 'bob', sessionId: 's1', start: 100 }),
  ])!
  const playheadTick = shallowRef(0)
  const seekToTick = vi.fn((tick: number) => {
    playheadTick.value = tick
  })
  const context = {
    episode: () => episode,
    session: { isPlaying: shallowRef(false), playheadTick, seekToTick },
  } as unknown as EditorWorkspaceContext
  const Host = defineComponent({
    setup() {
      provide(editorWorkspaceContextKey, context)
      return () => h('div', { style: 'height: 420px; width: 640px' }, [h(EventLogPanel)])
    },
  })
  const screen = await render(Host)

  try {
    // Default filters: 2 joins + 1 chest + 200 clicks + 2 leaves; per-tick control states are hidden.
    await expect.element(screen.getByText('205 / 255 events')).toBeVisible()
    await expect.element(screen.getByText('Opened chest at 4 -60 4 (#1, container)')).toBeVisible()

    // Search narrows to bob's clicks on one slot.
    await screen.getByRole('searchbox', { name: 'Search events' }).fill('slot 42 in')
    await expect.element(screen.getByText('1 / 255 events')).toBeVisible()
    await screen.getByRole('searchbox', { name: 'Search events' }).fill('')

    // Player chips: showing only alice hides bob's clicks.
    await screen.getByRole('button', { name: 'Player alice' }).click()
    await expect.element(screen.getByText('3 / 255 events')).toBeVisible()
    await screen.getByRole('button', { name: 'Player alice' }).click()

    // Type filters: per-tick state adds alice's 50 control samples.
    await screen.getByRole('button', { name: 'Event type filters' }).click()
    await screen.getByRole('checkbox', { name: /Per-tick state/ }).click()
    await expect.element(screen.getByText('255 / 255 events')).toBeVisible()
    await screen.getByRole('checkbox', { name: /Per-tick state/ }).click()

    // Clicking a row seeks the playhead to its episode tick and makes it the current row.
    await screen.getByRole('button', { name: /Seek to alice opened at Server tick 110/ }).click()
    expect(seekToTick).toHaveBeenLastCalledWith(10)
    await expect.element(screen.getByRole('button', { name: /Seek to alice opened at Server tick 110/ })).toHaveAttribute('aria-current', 'step')

    // Following: a late playhead scrolls bob's click at Server tick 498 (slot 189) into view.
    playheadTick.value = 399
    await nextTick()
    const late = screen.getByRole('button', { name: /Seek to bob container_click at Server tick 498/ })
    await expect.element(late).toHaveAttribute('aria-current', 'step')
    await expect.element(late).toBeInViewport()

    // Paused follow keeps the list where it is while the playhead moves.
    await screen.getByRole('button', { name: 'Follow playhead' }).click()
    await expect.element(screen.getByRole('button', { name: 'Follow playhead' })).toHaveAttribute('aria-pressed', 'false')
    playheadTick.value = 10
    await nextTick()
    await new Promise(resolve => setTimeout(resolve, 100))
    await expect.element(late).toBeInViewport()
    expect(screen.container.querySelector('[aria-current="step"]')).toBeNull()

    // Expanding a row shows its raw JSON.
    await screen.getByRole('button', { name: 'Show raw JSON' }).first().click()
    await expect.element(screen.getByText(/"serverTick": "/).first()).toBeVisible()
  }
  finally {
    await screen.unmount()
  }
})
