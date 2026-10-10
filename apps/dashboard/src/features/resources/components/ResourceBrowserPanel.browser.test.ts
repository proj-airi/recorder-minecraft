import type { RecorderMinecraftApiV1Replay } from '@proj-airi/recorder-minecraft-api'

import type { EditorWorkspaceContext } from '../../editor/workspaceContext'
import type { EpisodeDraft, TimelineWorldSessionSource } from '../../timeline/domain'

import { expect, it, vi } from 'vitest'
import { render } from 'vitest-browser-vue'
import { defineComponent, h, provide, shallowRef } from 'vue'

import ResourceBrowserPanel from './ResourceBrowserPanel.vue'

import { editorWorkspaceContextKey } from '../../editor/workspaceContext'
import { addReplaysToEpisode, createEmptyEpisode } from '../../timeline/replay'
import { useArtifactCatalog } from '../composables/useArtifactCatalog'
import { catalogFixture } from '../fixtures'

import 'uno.css'

async function mountPanel() {
  const catalogRefresh = vi.fn(async () => ({}))
  const catalog = useArtifactCatalog({
    artifactsList: async () => ({ data: { serverInstances: catalogFixture() } }),
    catalogRefresh,
    replaysList: async () => ({ data: { replays: [] } }),
  })
  await catalog.load()
  const episode = shallowRef<EpisodeDraft>(createEmptyEpisode())
  const addSession = vi.fn((sessionId: string, replays: readonly RecorderMinecraftApiV1Replay[], world?: TimelineWorldSessionSource) => {
    episode.value = addReplaysToEpisode(episode.value, replays.filter(replay => replay.sessionId === sessionId), world) ?? episode.value
  })
  const addReplay = vi.fn((replay: RecorderMinecraftApiV1Replay) => {
    episode.value = addReplaysToEpisode(episode.value, [replay]) ?? episode.value
  })
  const context = { addReplay, addSession, catalog, episode: () => episode.value } as unknown as EditorWorkspaceContext
  const Host = defineComponent({
    setup() {
      provide(editorWorkspaceContextKey, context)
      return () => h('div', { style: 'height: 720px; width: 260px; display: flex; flex-direction: column' }, [h(ResourceBrowserPanel)])
    },
  })
  const screen = await render(Host)
  return { addReplay, addSession, catalogRefresh, episode, screen }
}

it('shows the session tree, adds a whole session with its world source, and marks it on the timeline', async () => {
  const { addSession, catalogRefresh, episode, screen } = await mountPanel()
  try {
    const tree = screen.getByRole('tree', { name: 'Recordings' })
    await expect.element(tree.getByRole('treeitem', { name: 'Test server' })).toBeVisible()
    await expect.element(tree.getByRole('treeitem', { name: 'Plays without a world session' }).first()).toBeVisible()
    const run1 = tree.getByRole('treeitem', { name: 'Oct 10, 08:00:03' })
    await expect.element(run1).toHaveAttribute('aria-expanded', 'false')
    // Compact indicators: duration, participants, alignment divergences, known gaps, stream failure.
    await expect.element(run1.getByText('0:55')).toBeInTheDocument()
    await expect.element(run1.getByTitle('2 participants')).toBeInTheDocument()
    await expect.element(run1.getByTitle('Session alignment: 57 events, 2 observation divergences')).toBeInTheDocument()
    await expect.element(run1.getByRole('img', { name: '1 known gaps' })).toBeInTheDocument()
    await expect.element(tree.getByRole('treeitem', { name: 'Oct 10, 08:01:22' }).getByRole('img', { name: 'World stream failure' })).toBeInTheDocument()
    // Only eve has a rendered video; data-only Plays get a different icon and label.
    await expect.element(tree.getByRole('treeitem', { name: 'eve, Play with video' })).toBeVisible()
    expect(screen.container.querySelectorAll('[role="treeitem"] .i-mingcute-video-line')).toHaveLength(1)

    // Keyboard: expand run 1 with ArrowRight, then Enter on the session adds both Plays at once.
    await run1.click()
    const element = screen.container.querySelector<HTMLElement>('[role="tree"]')!
    element.dispatchEvent(new KeyboardEvent('keydown', { bubbles: true, key: 'ArrowRight' }))
    await expect.element(run1).toHaveAttribute('aria-expanded', 'true')
    await expect.element(tree.getByRole('treeitem', { name: 'bob, Play' })).toBeVisible()
    element.dispatchEvent(new KeyboardEvent('keydown', { bubbles: true, key: 'Enter' }))
    await expect.poll(() => addSession.mock.calls.length).toBe(1)
    const [sessionId, replays, world] = addSession.mock.calls[0]!
    expect(sessionId).toBe('run1-session')
    expect(replays.map(replay => replay.connectionId).sort()).toEqual(['alice-1', 'bob-1'])
    expect(world).toMatchObject({ endServerTick: 1106, id: '20261010T080003Z--run1', sessionId: 'run1-session', startServerTick: 0 })
    expect(episode.value.placements).toHaveLength(2)
    await expect.element(run1.getByRole('img', { name: 'On the timeline' })).toBeInTheDocument()
    await expect.element(tree.getByRole('treeitem', { name: 'bob, Play' }).getByRole('img', { name: 'On the timeline' })).toBeInTheDocument()

    // ArrowDown moves to alice's Play; the details pane shows its ids, world link, and files.
    element.dispatchEvent(new KeyboardEvent('keydown', { bubbles: true, key: 'ArrowDown' }))
    const details = screen.getByRole('region', { name: 'Details: alice · Play' })
    await expect.element(details).toBeVisible()
    await expect.element(details.getByText('232 – 900 (0:33)')).toBeVisible()
    await expect.element(details.getByText('disconnect')).toBeVisible()
    await expect.element(details.getByText(/Linked by world container truth/)).toBeVisible()
    await expect.element(details.getByText('/assets/alice-1/perception.jsonl')).toBeInTheDocument()
    await expect.element(details.getByText('actions: not available')).toBeInTheDocument()
    await expect.element(details.getByRole('button', { name: 'Copy perception' })).toBeInTheDocument()

    // "Show world session" jumps to the session and shows its details.
    await details.getByRole('button', { name: 'Show world session' }).click()
    const sessionDetails = screen.getByRole('region', { name: 'Details: World session' })
    await expect.element(sessionDetails.getByText('server_shutdown')).toBeVisible()
    await expect.element(sessionDetails.getByText('world_entities_not_recorded')).toBeVisible()
    await expect.element(sessionDetails.getByText(/2 observation divergences/)).toBeInTheDocument()
    await expect.element(sessionDetails.getByText('/assets/world/run1/alignments/session-alignment.jsonl')).toBeInTheDocument()

    await screen.getByRole('button', { name: 'Refresh recordings' }).click()
    await expect.poll(() => catalogRefresh.mock.calls.length).toBe(1)
  }
  finally {
    await screen.unmount()
  }
})

it('searches across players and ids and adds a single Play', async () => {
  const { addReplay, screen } = await mountPanel()
  try {
    await screen.getByRole('searchbox', { name: 'Search recordings' }).fill('dave')
    const tree = screen.getByRole('tree', { name: 'Recordings' })
    await expect.element(tree.getByRole('treeitem', { name: 'dave, Play' })).toBeVisible()
    expect(screen.container.querySelector('[aria-label="carol, Play"]')).toBeNull()
    await expect.element(screen.getByText('0 sessions · 1 Plays')).toBeVisible()

    // The add button is revealed on hover; click it directly.
    tree.getByRole('treeitem', { name: 'dave, Play' }).getByRole('button', { name: 'Add Play to timeline' }).element().dispatchEvent(new MouseEvent('click', { bubbles: true }))
    expect(addReplay).toHaveBeenCalledWith(expect.objectContaining({ connectionId: 'dave-1' }))

    await screen.getByRole('searchbox', { name: 'Search recordings' }).fill('no such player')
    await expect.element(screen.getByText('No recordings match')).toBeVisible()
    await screen.getByRole('button', { name: 'Clear filters' }).click()
    await expect.element(tree.getByRole('treeitem', { name: 'Test server' })).toBeVisible()
  }
  finally {
    await screen.unmount()
  }
})
