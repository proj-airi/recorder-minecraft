import { describe, expect, it } from 'vitest'

import run2Alignment from './fixtures/run2-session-alignment.jsonl?raw'
import run2World from './fixtures/run2-world-events.jsonl?raw'

import { parseAlignmentRecord, parsePerceptionRecord, parseWorldEvent } from './formats'
import { readJsonl } from './jsonl'
import { summarizePerception } from './perception'

function lines(text: string): unknown[] {
  return text.split('\n').filter(Boolean).map(line => JSON.parse(line))
}

describe('evidence formats', () => {
  it('restores omitted zero values and int64 strings in world events', () => {
    const events = lines(run2World).map(parseWorldEvent)
    // Line 1: serverTick 0 and z 0 are omitted by ProtoJSON.
    expect(events[0]).toMatchObject({
      blockPos: { x: 4, y: -60, z: 0 },
      kind: 'snapshot',
      reason: 'SESSION_START',
      sequence: 1,
      serverTick: 0,
    })
    expect(events[2]).toMatchObject({ blockPos: { x: 4, y: -60, z: 0 }, cause: 'DESTROYED', kind: 'removed', serverTick: 102 })
    expect(events.at(-1)).toMatchObject({ blockPos: { x: 4, y: -60, z: 4 }, kind: 'snapshot', reason: 'CHANGED', serverTick: 537, slots: [{ count: 1, itemId: 'minecraft:diamond', slot: 0 }] })
  })

  it('reads loot-ungenerated snapshots, numeric enums, and unknown records', () => {
    expect(parseWorldEvent({ containerSnapshot: { blockPos: {}, contentsState: 2, lootTable: 'minecraft:chests/simple_dungeon', reason: 'REASON_LOADED' }, identity: { sequence: '9', serverTick: '12' } }))
      .toMatchObject({ blockPos: { x: 0, y: 0, z: 0 }, contentsState: 'LOOT_UNGENERATED', lootTable: 'minecraft:chests/simple_dungeon', reason: 'LOADED', slots: [] })
    expect(parseWorldEvent({ entitySpawned: {}, identity: {} })).toBeNull()
    expect(parseAlignmentRecord({ somethingNew: {} })).toBeNull()
  })

  it('parses alignment headers, aligned events, and divergences', () => {
    const records = lines(run2Alignment).map(parseAlignmentRecord)
    const header = records[0]
    expect(header?.kind).toBe('header')
    if (header?.kind !== 'header')
      return
    expect(header.header).toMatchObject({ divergenceCount: 2, sessionId: '024245d6-b07a-4ca5-8811-247c0f7bf2b4', usesFutureContext: true, worldCoverage: { firstTick: 0, lastTick: 993 } })
    expect(header.header.participants.map(participant => participant.playerName)).toEqual(['alice', 'bob'])

    const divergences = records.flatMap(record => record?.kind === 'divergence' ? [record.divergence] : [])
    expect(divergences[0]).toMatchObject({
      blockPos: { x: 4, y: -60, z: 0 },
      coPresence: {
        container: 'VISIBLE',
        entities: [{ participantConnectionId: '5f33e770-0d8c-4dd4-8f2b-f5bdc5cc82a0', participantContainerOpen: true, participantMenu: { sequence: 1070, serverTick: 437, stream: 'CAPTURE_EVENTS' }, visibility: 'VISIBLE' }],
        sample: { sequence: 0, serverTick: 465, stream: 'PERCEPTION' },
        status: 'SAMPLED',
      },
      end: 'REOBSERVED',
      endTick: 661,
      lastTick: 660,
      observed: { slots: [{ count: 1, itemId: 'minecraft:diamond' }] },
      startTick: 465,
      truth: { slots: [], sources: [{ sequence: 8, serverTick: 465, stream: 'WORLD_EVENTS' }] },
    })

    const join = records.find(record => record?.kind === 'event' && record.event.kind === 'ACTOR_JOINED')
    expect(join?.kind === 'event' && join.event.blockPos).toBe(undefined)
  })

  it('keeps an open divergence open and an origin block position', () => {
    const record = parseAlignmentRecord({ divergence: { blockPos: {}, dimension: 'minecraft:the_nether', end: 'DIVERGENCE_END_WORLD_COVERAGE_END', lastTick: '40', startTick: '30' } })
    expect(record).toMatchObject({ divergence: { blockPos: { x: 0, y: 0, z: 0 }, end: 'WORLD_COVERAGE_END', lastTick: 40, startTick: 30 } })
    expect(record?.kind === 'divergence' && 'endTick' in record.divergence).toBe(false)
    expect(record?.kind === 'divergence' && record.divergence.coPresence.status).toBe('UNSPECIFIED')
  })

  it('folds perception samples into visibility runs with zero-omitted ray support', () => {
    const header = { header: { assumptions: { samplingIntervalTicks: 2 }, sampleCount: '5' }, schemaVersion: 1 }
    const bob = { distance: 3, support: { clear: 9, inView: 9, points: 9 }, typeId: 'minecraft:player', uuid: 'bob' }
    const chest = { blockPos: { x: 1 }, dimension: 'minecraft:overworld', distance: 2.5, support: { inView: 9, points: 9, unknown: 9 }, typeId: 'minecraft:chest' }
    const summary = summarizePerception([
      header,
      { sample: { serverTick: '10', visibleEntities: [bob] } },
      { sample: { serverTick: '12', undeterminedBlockEntities: [chest], visibleEntities: [bob] } },
      // Tick 16 is two intervals later: the run of bob breaks although bob is visible again.
      { sample: { serverTick: '16', undeterminedBlockEntities: [chest], visibleEntities: [bob] } },
      { sample: { serverTick: '18' } },
      { sample: { serverTick: '20', visibleBlockEntities: [chest] } },
    ])

    expect(summary).toMatchObject({ firstTick: 10, intervalTicks: 2, lastTick: 20, sampleCount: 5 })
    const bobTarget = summary.targets.find(target => target.key === 'entity:bob')!
    expect(bobTarget.runs.map(run => [run.startTick, run.lastTick, run.samples])).toEqual([[10, 12, 2], [16, 16, 1]])
    expect(bobTarget.runs[0]!.support).toEqual({ blocked: 0, clear: 18, inView: 18, points: 18, unknown: 0 })

    const chestTarget = summary.targets.find(target => target.kind === 'block-entity')!
    expect(chestTarget.key).toBe('block:minecraft:overworld|1,0,0')
    expect(chestTarget.runs.map(run => [run.state, run.startTick, run.lastTick])).toEqual([['undetermined', 12, 12], ['undetermined', 16, 16], ['visible', 20, 20]])
    expect(chestTarget).toMatchObject({ undeterminedSamples: 2, visibleSamples: 1 })
  })

  it('parses perception headers with their assumptions', () => {
    const record = parsePerceptionRecord({ header: { assumptions: { camera: { verticalFovDegrees: 70 } }, knownLimitations: ['fov_and_aspect_assumed: x'], ticks: { lastTick: '9' } } })
    expect(record).toMatchObject({ header: { assumptions: { camera: { horizontalFovDegrees: 0, verticalFovDegrees: 70 } }, knownLimitations: ['fov_and_aspect_assumed: x'], ticks: { firstTick: 0, lastTick: 9 } }, kind: 'header' })
  })

  it('streams JSONL split across chunks and reports a missing file', async () => {
    const encoder = new TextEncoder()
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(encoder.encode('{"a":1}\n{"a"'))
        controller.enqueue(encoder.encode(':2}\n\n{"a":3}'))
        controller.close()
      },
    })
    const seen: unknown[] = []
    await readJsonl('/x.jsonl', record => seen.push(record), { fetch: async () => new Response(body) })
    expect(seen).toEqual([{ a: 1 }, { a: 2 }, { a: 3 }])

    await expect(readJsonl('/missing.jsonl', () => {}, { fetch: async () => new Response('', { status: 404 }) }))
      .rejects
      .toMatchObject({ name: 'EvidenceFileMissingError' })
    await expect(readJsonl('/broken.jsonl', () => {}, { fetch: async () => new Response('{"a":1}\nnot json\n') }))
      .rejects
      .toThrow('line 2 is not valid JSON')
  })
})
