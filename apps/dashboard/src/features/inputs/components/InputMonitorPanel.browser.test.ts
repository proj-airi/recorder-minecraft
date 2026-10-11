import type { EditorWorkspaceContext } from '../../editor/workspaceContext'
import type { EpisodeDraft } from '../../timeline/domain'

import { afterEach, expect, it, vi } from 'vitest'
import { render } from 'vitest-browser-vue'
import { defineComponent, h, nextTick, provide, shallowRef } from 'vue'

import InputMonitorPanel from './InputMonitorPanel.vue'

import { editorWorkspaceContextKey } from '../../editor/workspaceContext'
import { clearCaptureEventsCache } from '../../event-log/useCaptureEvents'
import { testReplay } from '../../timeline/fixtures/replays'
import { addReplaysToEpisode, commitPlacementEdit, createEmptyEpisode } from '../../timeline/replay'

import 'uno.css'

function line(tick: number, record: Record<string, unknown>, sequence = tick): string {
  return JSON.stringify({ identity: { sequence: String(sequence), serverTick: String(tick) }, ...record })
}

const files: Record<string, string> = {
  '/events/alice.jsonl': [
    line(100, { controlState: { state: { selectedSlot: 0 } } }),
    line(100, { playerState: { health: 20, inventory: [{ count: 3, itemId: 'minecraft:oak_log', slot: 0 }], selectedSlot: 0 } }, 101),
    line(125, { controlState: { state: { forward: true, selectedSlot: 2 } } }),
    line(125, { playerState: { health: 20, selectedSlot: 2 } }, 126),
  ].join('\n'),
  '/events/bob.jsonl': [
    line(110, { controlState: { state: { jump: true, selectedSlot: 4 } } }),
    line(125, { packetApply: { packet: { actionKind: 'swing', identity: { packetType: 'serverbound/minecraft:swing' } } } }),
  ].join('\n'),
}

afterEach(() => {
  clearCaptureEventsCache()
  vi.unstubAllGlobals()
})

it('shows every player on the timeline, each at the Server tick its own clip maps to', async () => {
  vi.stubGlobal('fetch', vi.fn((input: string | URL) => {
    const path = new URL(String(input), window.location.href).pathname
    return Promise.resolve(new Response(files[path] ?? '', { status: files[path] ? 200 : 404 }))
  }))

  // Both Plays are in one session, so they share the Server tick axis (Play start 100 = episode 0).
  let episode: EpisodeDraft = addReplaysToEpisode(createEmptyEpisode(), [
    testReplay('alice', { end: 300, player: 'alice', sessionId: 's1', start: 100 }),
    testReplay('bob', { end: 300, player: 'bob', sessionId: 's1', start: 100 }),
  ])!
  // Move bob's clip 10 ticks to the right: the playhead at episode tick 25 shows his Server tick 115.
  const bobSegment = episode.segments.find(segment => segment.placementId === 'play:bob')!
  episode = commitPlacementEdit(episode, bobSegment.id, bobSegment.startTick + 10, bobSegment.endTick + 10, bobSegment.trackId)!

  const playheadTick = shallowRef(25)
  const selectedSegmentId = shallowRef<null | string>(null)
  const context = {
    episode: () => episode,
    session: { playheadTick, selectedSegmentId },
  } as unknown as EditorWorkspaceContext
  const Host = defineComponent({
    setup() {
      provide(editorWorkspaceContextKey, context)
      return () => h('div', { style: 'height: 600px; width: 320px' }, [h(InputMonitorPanel)])
    },
  })
  const screen = await render(Host)

  try {
    const alice = screen.getByRole('article', { name: 'Inputs of alice' })
    const bob = screen.getByRole('article', { name: 'Inputs of bob' })
    await expect.element(alice.getByText('tick 125')).toBeVisible()
    await expect.element(bob.getByText('tick 115')).toBeVisible()

    // Alice: forward held, hotbar slot 3 selected, slot 1 holds logs from the latest player state.
    await expect.element(alice.getByRole('group', { name: 'Pressed: W' })).toBeVisible()
    await expect.element(alice.getByRole('listitem', { name: 'Slot 3, selected' })).toBeVisible()
    // Bob: jump held from tick 110, slot 5; the swing at Server tick 125 is not shown yet.
    await expect.element(bob.getByRole('group', { name: 'Pressed: Jump' })).toBeVisible()
    await expect.element(bob.getByRole('listitem', { name: 'Slot 5, selected' })).toBeVisible()

    playheadTick.value = 35
    await nextTick()
    await expect.element(bob.getByText('tick 125')).toBeVisible()
    await expect.element(bob.getByRole('group', { name: 'Pressed: Jump, left mouse' })).toBeVisible()

    // Selecting bob's clip highlights his card; "Selected" shows only him.
    selectedSegmentId.value = episode.segments.find(segment => segment.placementId === 'play:bob')!.id
    await nextTick()
    await expect.element(bob).toHaveAttribute('aria-current', 'true')
    await screen.getByRole('button', { name: 'Show the selected player' }).click()
    await expect.element(screen.getByRole('article', { name: 'Inputs of bob' })).toBeVisible()
    expect(screen.container.querySelector('[aria-label="Inputs of alice"]')).toBeNull()

    // Outside every clip of a lane, the card says so instead of showing stale inputs.
    await screen.getByRole('button', { name: 'Show all players' }).click()
    playheadTick.value = 5
    await nextTick()
    await expect.element(screen.getByRole('article', { name: 'Inputs of bob' }).getByText('No clip of this player at the playhead.')).toBeVisible()
  }
  finally {
    await screen.unmount()
  }
})
