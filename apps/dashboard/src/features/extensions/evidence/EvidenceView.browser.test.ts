import type { EditorWorkspaceContext } from '../../editor/workspaceContext'
import type { SelectedTimelineDataItem, TimelineDataItem, TimelineDataTrack, TimelineDataTrackDescriptor, TimelinePlayerTarget, TimelineSessionTarget } from '../../timeline/data-tracks/types'
import type { EpisodeDraft } from '../../timeline/domain'
import type { RunName } from './fixtures/demo'

import { describe, expect, it, vi } from 'vitest'
import { render } from 'vitest-browser-vue'
import { defineComponent, h, provide, shallowRef } from 'vue'

import EvidenceView from './EvidenceView.vue'

import { editorWorkspaceContextKey } from '../../editor/workspaceContext'
import { describeTargets } from '../../timeline/composables/useTimelineDataTracks'
import { addReplaysToEpisode, createEmptyEpisode } from '../../timeline/replay'
import { serverTickToEpisodeTick } from '../../timeline/ticks'
import { demoReplays, demoSourcesOptions, RUNS } from './fixtures/demo'
import { EVIDENCE_PLAYER_PROVIDER_ID, EVIDENCE_SESSION_PROVIDER_ID } from './payload'
import { createEvidencePlayerProvider, createEvidenceSessionProvider } from './providers'
import { EvidenceSources, evidenceSourcesKey } from './sources'

interface Fixture {
  episode: EpisodeDraft
  pick: (label: string, index?: number) => Promise<SelectedTimelineDataItem>
  sources: EvidenceSources
}

async function describeSettled(describe: () => readonly TimelineDataTrackDescriptor[]): Promise<readonly TimelineDataTrackDescriptor[]> {
  let previous = ''
  for (let round = 0; round < 6; round += 1) {
    const descriptors = describe()
    const labels = descriptors.map(descriptor => descriptor.label).join('|')
    await Promise.all(descriptors.map(descriptor => descriptor.load({ signal: new AbortController().signal }).catch(() => [])))
    await settle()
    if (labels === previous)
      return descriptors
    previous = labels
  }
  return describe()
}

async function fixture(run: RunName): Promise<Fixture> {
  const sources = new EvidenceSources(demoSourcesOptions())
  const episode = addReplaysToEpisode(createEmptyEpisode(), demoReplays(run))!
  const targets = describeTargets(episode)
  const alice = targets.find((target): target is TimelinePlayerTarget => target.scope === 'player' && target.playerName === 'alice')!
  const session = targets.find((target): target is TimelineSessionTarget => target.scope === 'session')!
  const player = createEvidencePlayerProvider(sources)
  const aliceRows = await describeSettled(() => player.describe(alice))
  const sessionRows = createEvidenceSessionProvider(sources).describe(session)

  async function pick(label: string, index = 0): Promise<SelectedTimelineDataItem> {
    const isSession = sessionRows.some(row => row.label === label)
    const descriptor = (isSession ? sessionRows : aliceRows).find(row => row.label === label)!
    const items = await descriptor.load({ signal: new AbortController().signal })
    const item = items[index] as TimelineDataItem
    const serverTick = item.kind === 'point' ? item.serverTick : item.startServerTick
    const placement = isSession ? null : episode.placements.find(candidate => candidate.connectionId === item.connectionId) ?? null
    const track = {
      descriptorKey: descriptor.key,
      id: `data:test:${descriptor.key}`,
      items,
      label: descriptor.label,
      order: 0,
      providerId: isSession ? EVIDENCE_SESSION_PROVIDER_ID : EVIDENCE_PLAYER_PROVIDER_ID,
      status: 'ready',
      target: isSession ? session : alice,
      viewId: descriptor.viewId,
    } as TimelineDataTrack
    return { episodeTick: null, item, placement, serverTick, session: episode.sessions[0]!, track }
  }

  return { episode, pick, sources }
}

async function mount(fixtureValue: Fixture, selected: null | SelectedTimelineDataItem) {
  const seekToTick = vi.fn()
  const context = {
    episode: () => fixtureValue.episode,
    selectedDataItem: shallowRef(selected),
    session: { seekToTick },
  } as unknown as EditorWorkspaceContext
  const Host = defineComponent({
    setup() {
      provide(editorWorkspaceContextKey, context)
      provide(evidenceSourcesKey, fixtureValue.sources)
      return () => h('div', { style: 'height: 700px' }, [h(EvidenceView)])
    },
  })
  return { screen: await render(Host), seekToTick }
}

async function settle(): Promise<void> {
  for (let index = 0; index < 4; index += 1)
    await new Promise(resolve => setTimeout(resolve, 0))
}

describe('evidence details view', () => {
  it('shows run 1: Alice\'s divergence on chest A with no co-present participant', async () => {
    const run1 = await fixture('run1')
    const { screen } = await mount(run1, await run1.pick('Divergence · chest (4, -60, 0)'))
    try {
      await expect.element(screen.getByTestId('divergence-title')).toHaveTextContent('alice · chest (4, -60, 0)')
      await expect.element(screen.getByTestId('divergence-start')).toHaveTextContent('tick 587')
      await expect.element(screen.getByTestId('divergence-end')).toHaveTextContent('tick 817')
      await expect.element(screen.getByTestId('divergence-end')).toHaveTextContent('the actor observed the container again')
      await expect.element(screen.getByTestId('observed-contents')).toHaveTextContent('Diamond')
      await expect.element(screen.getByTestId('truth-contents')).toHaveTextContent('empty')
      await expect.element(screen.getByTestId('co-presence-summary')).toHaveTextContent('The container was not visible to alice. No other participant was visible to alice.')
      await expect.element(screen.getByTestId('co-presence-container')).toHaveTextContent('not visible')
      await expect.element(screen.getByTestId('co-presence-no-entities')).toBeVisible()
      await expect.element(screen.getByTestId('hindsight-flag')).toBeInTheDocument()
      await expect.element(screen.getByTestId('perception-fov')).toHaveTextContent('70° vertical')
      expect(document.body.textContent).not.toMatch(/belief/i)
    }
    finally {
      screen.unmount()
    }
  })

  it('shows run 2: Bob visible with chest A open at the divergence start, and seeks to his menu record', async () => {
    const run2 = await fixture('run2')
    const { screen, seekToTick } = await mount(run2, await run2.pick('Divergence · chest (4, -60, 0)'))
    try {
      await expect.element(screen.getByTestId('divergence-start')).toHaveTextContent('tick 465')
      await expect.element(screen.getByTestId('co-presence-summary')).toHaveTextContent('The container was visible to alice. bob was visible to alice with this container open.')
      const row = screen.getByTestId('co-present-entity')
      await expect.element(row).toHaveTextContent('bob')
      await expect.element(row).toHaveTextContent('visible')
      await expect.element(row).toHaveTextContent('yes')

      await screen.getByRole('button', { name: /bob · capture\/events\.jsonl @ 437 #1070/ }).click()
      const bobPlacement = run2.episode.placements.find(placement => placement.connectionId === RUNS.run2.bob)!
      expect(seekToTick).toHaveBeenCalledWith(serverTickToEpisodeTick(bobPlacement, 437))

      // World records seek through the session anchor.
      await screen.getByRole('button', { name: /world-events\.jsonl @ 465 #8/ }).first().click()
      expect(seekToTick).toHaveBeenLastCalledWith(serverTickToEpisodeTick(run2.episode.sessions[0]!, 465))
    }
    finally {
      screen.unmount()
    }
  })

  it('shows the full contents of a world container snapshot', async () => {
    const run2 = await fixture('run2')
    const { screen } = await mount(run2, await run2.pick('World container events', 6))
    try {
      await expect.element(screen.getByText('chest (4, -60, 0)', { exact: true })).toBeVisible()
      await expect.element(screen.getByTestId('world-event-contents-state')).toHaveTextContent('1 of 27 slots filled, 1 item(s)')
      await expect.element(screen.getByTestId('world-event-slots')).toHaveTextContent('Diamond')
    }
    finally {
      screen.unmount()
    }
  })

  it('shows a visibility interval with its samples and ray support', async () => {
    const run2 = await fixture('run2')
    const { screen } = await mount(run2, await run2.pick('Visible · bob'))
    try {
      await expect.element(screen.getByTestId('visibility-title')).toHaveTextContent('bob visible to alice')
      await expect.element(screen.getByTestId('visibility-samples')).toHaveTextContent('13 of 13 expected at 1-tick sampling')
      await expect.element(screen.getByTestId('ray-support')).toHaveTextContent('Clear')
    }
    finally {
      screen.unmount()
    }
  })

  it('shows an empty state with the evidence sources of each session', async () => {
    const run1 = await fixture('run1')
    const { screen } = await mount(run1, null)
    try {
      await expect.element(screen.getByTestId('evidence-empty')).toBeVisible()
      const sources = screen.getByTestId('evidence-session-sources')
      await expect.element(sources).toHaveTextContent('world-events.jsonl')
      await expect.element(sources).toHaveTextContent('session-alignment.jsonl')
      await expect.element(sources).toHaveTextContent('alice perception')

      const empty = { episode: createEmptyEpisode(), pick: run1.pick, sources: run1.sources }
      const second = await mount(empty, null)
      await expect.element(second.screen.getByTestId('evidence-no-sessions')).toBeVisible()
      second.screen.unmount()
    }
    finally {
      screen.unmount()
    }
  })
})
