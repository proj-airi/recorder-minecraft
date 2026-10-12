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
    // Slot 9 is the first backpack slot; zero-valued fields such as armor are omitted by protojson.
    line(125, { playerState: { foodLevel: 17, health: 13.5, inventory: [{ count: 3, itemId: 'minecraft:oak_log', slot: 0 }, { count: 2, itemId: 'minecraft:bread', slot: 9 }], maxHealth: 20, selectedSlot: 2 } }, 126),
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
    await expect.element(alice.getByRole('listitem', { name: 'Hotbar 3, empty, held' })).toBeVisible()
    // Without an icon server the cell falls back to the full item name, and the summary leads with
    // what is pressed and held.
    await expect.element(alice.getByRole('listitem', { name: 'Hotbar 1: 3 Oak Log' })).toHaveTextContent('Oak Log')
    // The summary names only what the cluster and hotbar cannot: W is down, and the hand is empty, so
    // there is nothing held to name.
    await expect.element(alice.getByTestId('input-summary')).toHaveTextContent(/^W$/)
    // Survival state: health with half points, food; armor is 0 and therefore not shown.
    const status = alice.getByRole('group', { name: 'Player status' })
    await expect.element(status.getByLabelText('Health 13.5 / 20')).toBeVisible()
    await expect.element(status.getByLabelText('Food 17 / 20, saturation 0')).toBeVisible()
    expect(status.element().textContent).not.toContain('Armor')
    // The backpack is folded behind a count and holds the bread in its first slot.
    const backpackToggle = alice.getByRole('button', { name: 'Toggle backpack' })
    await expect.element(backpackToggle).toHaveTextContent('Backpack · 1 stack')
    await backpackToggle.click()
    await expect.element(alice.getByRole('listitem', { name: 'Backpack 1: 2 Bread' })).toBeVisible()
    await expect.element(alice.getByRole('listitem', { name: 'Head, empty' })).toBeVisible()
    await backpackToggle.click()
    // Bob: jump held from tick 110, slot 5; the swing at Server tick 125 is not shown yet.
    await expect.element(bob.getByRole('group', { name: 'Pressed: Jump' })).toBeVisible()
    await expect.element(bob.getByRole('listitem', { name: 'Hotbar 5, empty, held' })).toBeVisible()

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
