import type { TimelineDataInterval, TimelineDataItem, TimelineDataPoint, TimelineDataTrackDescriptor, TimelinePlayerTarget, TimelineSessionTarget } from '../../timeline/data-tracks/types'
import type { EpisodeDraft } from '../../timeline/domain'
import type { RunName } from './fixtures/demo'
import type { DivergencePayload, VisibilityPayload, WorldEventPayload } from './payload'

import { describe, expect, it } from 'vitest'
import { shallowRef } from 'vue'

import run2PerceptionUrl from './fixtures/run2-alice-perception.jsonl?url'

import { describeTargets } from '../../timeline/composables/useTimelineDataTracks'
import { addReplaysToEpisode, createEmptyEpisode } from '../../timeline/replay'
import { demoReplays, demoSourcesOptions, RUNS } from './fixtures/demo'
import { createEvidencePlayerProvider, createEvidenceSessionProvider } from './providers'
import { EvidenceSources, loadPerceptionInWorker } from './sources'

function aliceDivergenceSpans(items: readonly TimelineDataItem[]): [number, number][] {
  return (items as TimelineDataInterval[]).map(item => [item.startServerTick, item.endServerTick])
}

/** Describes until the descriptor list stops changing (each settled file can add rows). */
async function describeSettled(describe: () => readonly TimelineDataTrackDescriptor[]): Promise<readonly TimelineDataTrackDescriptor[]> {
  let previous = ''
  for (let round = 0; round < 6; round += 1) {
    const descriptors = describe()
    const labels = descriptors.map(descriptor => descriptor.label).join('|')
    // Placeholder rows start their loads when the timeline shows them.
    await Promise.all(descriptors.map(descriptor => descriptor.load({ signal: new AbortController().signal }).catch(() => [])))
    await settle()
    if (labels === previous)
      return descriptors
    previous = labels
  }
  return describe()
}

function episodeFor(run: RunName, options: { bobPerception?: boolean } = {}): EpisodeDraft {
  return addReplaysToEpisode(createEmptyEpisode(), demoReplays(run, options))!
}

async function load(descriptors: readonly TimelineDataTrackDescriptor[], label: string): Promise<readonly TimelineDataItem[]> {
  const descriptor = descriptors.find(candidate => candidate.label === label)
  expect(descriptor, `row "${label}" in ${descriptors.map(candidate => candidate.label).join(', ')}`).toBeDefined()
  return descriptor!.load({ signal: new AbortController().signal })
}

function playerTarget(episode: EpisodeDraft, playerName: string): TimelinePlayerTarget {
  return describeTargets(episode).find((target): target is TimelinePlayerTarget => target.scope === 'player' && target.playerName === playerName)!
}

function sessionTarget(episode: EpisodeDraft): TimelineSessionTarget {
  return describeTargets(episode).find((target): target is TimelineSessionTarget => target.scope === 'session')!
}

async function settle(): Promise<void> {
  for (let index = 0; index < 4; index += 1)
    await new Promise(resolve => setTimeout(resolve, 0))
}

describe('evidence data tracks from the Sally-Anne demo', () => {
  it('lists one divergence row per container, plus interactions and visibility rows', async () => {
    const sources = new EvidenceSources(demoSourcesOptions())
    const provider = createEvidencePlayerProvider(sources, shallowRef({ visibilityRows: 'aligned' as const }))
    const target = playerTarget(episodeFor('run1'), 'alice')

    // Before the world session and alignment arrive, one combined row stands in.
    expect(provider.describe(target).map(descriptor => descriptor.label)).toEqual(['Observation divergences', 'Container views & clicks', 'Visibility'])

    const descriptors = await describeSettled(() => provider.describe(target))
    expect(descriptors.map(descriptor => descriptor.label)).toEqual([
      'Divergence · chest (4, -60, 0)',
      'Divergence · chest (4, -60, 4)',
      'Container views & clicks',
      'Visible · bob',
      'Visible · chest (4, -60, 0)',
      'Visible · chest (4, -60, 4)',
    ])
    expect(descriptors.every(descriptor => descriptor.viewId === 'extension:evidence')).toBe(true)

    const chestA = await load(descriptors, 'Divergence · chest (4, -60, 0)')
    expect(aliceDivergenceSpans(chestA)).toEqual([[587, 817]])
    expect(chestA[0]).toMatchObject({ connectionId: RUNS.run1.alice, label: 'observed: Diamond ×1 · world: empty' })
    expect(chestA[0]!.tooltip).toContain('Ended at tick 817: re-observed.')

    const interactions = await load(descriptors, 'Container views & clicks') as TimelineDataPoint[]
    expect(interactions.map(item => `${item.serverTick}:${item.label}`)).toEqual([
      '340:opened · chest (4, -60, 0)',
      '340:contents sent · chest (4, -60, 0)',
      '360:closed · chest (4, -60, 0)',
      '384:opened · chest (4, -60, 4)',
      '384:contents sent · chest (4, -60, 4)',
      '403:closed · chest (4, -60, 4)',
      '817:opened · chest (4, -60, 0)',
      '817:contents sent · chest (4, -60, 0)',
      '836:closed · chest (4, -60, 0)',
      '860:opened · chest (4, -60, 4)',
      '860:contents sent · chest (4, -60, 4)',
      '879:closed · chest (4, -60, 4)',
    ])
  })

  it('shows the same divergence structure in both runs but different co-presence facts', async () => {
    const facts = await Promise.all((['run1', 'run2'] as const).map(async (run) => {
      const sources = new EvidenceSources(demoSourcesOptions())
      const provider = createEvidencePlayerProvider(sources)
      const descriptors = await describeSettled(() => provider.describe(playerTarget(episodeFor(run), 'alice')))
      const [item] = await load(descriptors, 'Divergence · chest (4, -60, 0)')
      const payload = item!.payload as DivergencePayload
      return {
        coPresence: payload.divergence.coPresence,
        end: payload.divergence.end,
        observed: payload.divergence.observed.slots.map(slot => slot.itemId),
        span: aliceDivergenceSpans([item!])[0],
        truth: payload.divergence.truth.slots.map(slot => slot.itemId),
      }
    }))

    expect(facts.map(fact => [fact.end, fact.observed, fact.truth])).toEqual([
      ['REOBSERVED', ['minecraft:diamond'], []],
      ['REOBSERVED', ['minecraft:diamond'], []],
    ])
    expect(facts.map(fact => fact.span)).toEqual([[587, 817], [465, 661]])
    expect(facts[0]!.coPresence).toMatchObject({ container: 'NOT_VISIBLE', entities: [], status: 'SAMPLED' })
    expect(facts[1]!.coPresence).toMatchObject({
      container: 'VISIBLE',
      entities: [{ participantConnectionId: RUNS.run2.bob, participantContainerOpen: true, visibility: 'VISIBLE' }],
      status: 'SAMPLED',
    })
  })

  it('turns perception samples into visibility intervals for aligned targets', async () => {
    const sources = new EvidenceSources(demoSourcesOptions())
    const provider = createEvidencePlayerProvider(sources)
    const descriptors = await describeSettled(() => provider.describe(playerTarget(episodeFor('run2'), 'alice')))
    const bob = await load(descriptors, 'Visible · bob') as TimelineDataInterval[]
    expect(bob.map(item => [item.startServerTick, item.endServerTick])).toEqual([[458, 471]])
    const payload = bob[0]!.payload as VisibilityPayload
    expect(payload).toMatchObject({ actorName: 'alice', intervalTicks: 1, run: { samples: 13, state: 'visible' }, targetLabel: 'bob' })
    expect(payload.run.support.clear).toBeGreaterThan(0)
    expect(payload.header?.assumptions.camera.verticalFovDegrees).toBe(70)
  })

  it('places world container events on the session row, colored by reason', async () => {
    const sources = new EvidenceSources(demoSourcesOptions())
    const provider = createEvidenceSessionProvider(sources)
    const descriptors = provider.describe(sessionTarget(episodeFor('run2')))
    expect(descriptors.map(descriptor => descriptor.label)).toEqual(['World container events'])
    const items = await load(descriptors, 'World container events') as TimelineDataPoint[]
    expect(items.map(item => `${item.serverTick}:${item.label}`)).toEqual([
      '0:chest (4, -60, 0) · present at session start',
      '0:chest (4, -60, 4) · present at session start',
      '102:chest (4, -60, 0) · destroyed',
      '105:chest (4, -60, 4) · destroyed',
      '107:chest (4, -60, 0) · loaded',
      '109:chest (4, -60, 4) · loaded',
      '111:chest (4, -60, 0) · contents changed',
      '465:chest (4, -60, 0) · contents changed',
      '537:chest (4, -60, 4) · contents changed',
    ])
    expect(new Set(items.map(item => item.color)).size).toBe(4)
    expect((items[6]!.payload as WorldEventPayload).event).toMatchObject({ kind: 'snapshot', slots: [{ itemId: 'minecraft:diamond' }] })
  })

  it('reports missing sources as labeled empty rows, not errors', async () => {
    const options = demoSourcesOptions({ withoutAlignment: true })
    const sources = new EvidenceSources(options)
    const provider = createEvidencePlayerProvider(sources)

    const alice = await describeSettled(() => provider.describe(playerTarget(episodeFor('run2'), 'alice')))
    expect(alice[0]!.label).toBe('Observation divergences · no session alignment')
    expect(await load(alice, 'Observation divergences · no session alignment')).toEqual([])
    expect(alice.some(descriptor => descriptor.label === 'Container views & clicks')).toBe(false)
    // Without an alignment, rows come from the perception file itself.
    expect(alice.map(descriptor => descriptor.label)).toContain('Visible · player 8e289159')

    // Bob's perception URL is listed but the file is absent: rows load as empty.
    const bob = await describeSettled(() => provider.describe(playerTarget(episodeFor('run2'), 'bob')))
    expect(bob.map(descriptor => descriptor.label)).toEqual(['Observation divergences · no session alignment', 'Visibility'])
    expect(await load(bob, 'Visibility')).toEqual([])

    // A Play without perception says so.
    const noPerception = provider.describe(playerTarget(episodeFor('run2', { bobPerception: false }), 'bob'))
    expect(noPerception.map(descriptor => descriptor.label)).toContain('Visibility · no perception.jsonl')

    // Plays without a world session or perception get no evidence rows at all.
    const legacy = addReplaysToEpisode(createEmptyEpisode(), demoReplays('run1').map(replay => ({ ...replay, perceptionUrl: undefined, worldSessionId: undefined })))!
    expect(provider.describe(playerTarget(legacy, 'alice'))).toEqual([])
    expect(createEvidenceSessionProvider(sources).describe(sessionTarget(legacy))).toEqual([])
  })

  it('lists every perceived target in "all" mode, players first', async () => {
    const sources = new EvidenceSources(demoSourcesOptions())
    const provider = createEvidencePlayerProvider(sources, shallowRef({ visibilityRows: 'all' as const }))
    const descriptors = await describeSettled(() => provider.describe(playerTarget(episodeFor('run2'), 'alice')))
    expect(descriptors.filter(descriptor => descriptor.label.startsWith('Visible')).map(descriptor => descriptor.label)).toEqual([
      'Visible · player 8e289159',
      'Visible · chest (4, -60, 0)',
      'Visible · chest (4, -60, 4)',
    ])
  })

  it('fetches each file once and parses perception in a worker', async () => {
    const options = demoSourcesOptions()
    const sources = new EvidenceSources(options)
    const provider = createEvidencePlayerProvider(sources)
    const target = playerTarget(episodeFor('run1'), 'alice')
    const descriptors = await describeSettled(() => provider.describe(target))
    await Promise.all(descriptors.map(descriptor => descriptor.load({ signal: new AbortController().signal })))
    expect(options.requests.filter(url => url.endsWith('session-alignment.jsonl'))).toHaveLength(1)
    expect(options.requests.filter(url => url.endsWith('perception.jsonl'))).toHaveLength(1)

    const summary = await loadPerceptionInWorker(run2PerceptionUrl)
    expect(summary).toMatchObject({ firstTick: 458, lastTick: 470, sampleCount: 13 })
    expect(summary.targets.map(candidate => candidate.key)).toContain(`entity:8e289159-2034-3a16-96b9-9fa637848b3b`)
  })
})
