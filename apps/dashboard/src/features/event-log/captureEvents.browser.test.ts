import { describe, expect, it } from 'vitest'

import { testReplay } from '../timeline/fixtures/replays'
import { addReplaysToEpisode, createEmptyEpisode, cutPlacement } from '../timeline/replay'
import { parseCaptureEvents, streamCaptureEvents } from './captureEvents'
import { buildEventLogRows, currentRowIndex, filterEventLogRows } from './eventLogRows'

function apply(packetType: string, actionKind: string, fields: Record<string, unknown> = {}): Record<string, unknown> {
  return { packetApply: { packet: { actionKind, identity: { packetType: `serverbound/minecraft:${packetType}` }, ...fields } } }
}

function line(tick: number, sequence: number, record: Record<string, unknown>): string {
  return JSON.stringify({ identity: { sequence: String(sequence), serverTick: String(tick) }, ...record })
}

describe('capture event parsing', () => {
  it('summarizes packets with and without the populated payload fields', () => {
    const parsed = parseCaptureEvents([
      line(10, 1, apply('container_click', 'inventory')),
      line(11, 2, apply('container_click', 'inventory', { buttonNumber: 0, clickType: 'PICKUP', containerId: 3, slotNumber: 27 })),
      line(12, 3, apply('use_item_on', 'use', { blockHit: { blockPosition: { x: 4, y: -60, z: 4 }, direction: 'UP' }, hand: 'MAIN_HAND' })),
      line(13, 4, apply('set_carried_item', 'inventory', { slot: 2 })),
      line(14, 5, apply('player_action', 'player_action', { action: 'START_DESTROY_BLOCK', blockPosition: { x: 1, y: 2, z: 3 }, direction: 'NORTH' })),
      line(15, 6, apply('interact', 'interact', { interaction: 'ATTACK', target: { typeId: 'minecraft:zombie' } })),
      line(16, 7, apply('swing', 'swing', { hand: 'MAIN_HAND' })),
      line(17, 8, apply('move_player_pos', 'camera_or_position')),
      line(18, 9, apply('keep_alive', 'protocol_ack')),
    ].join('\n'))

    expect(parsed.entries.map(entry => [entry.kind, entry.label, entry.summary])).toEqual([
      ['action', 'container_click', 'Container click'],
      ['action', 'container_click', 'Container click slot 27, pickup, button 0 in #3'],
      ['action', 'use_item_on', 'Use item in main hand on block 4 -60 4 (up face)'],
      ['action', 'set_carried_item', 'Select hotbar slot 3'],
      ['action', 'player_action', 'Start destroy block at 1 2 3 (north face)'],
      ['action', 'interact', 'Attack zombie'],
      ['action', 'swing', 'Swing main hand'],
      ['movement', 'move_player_pos', 'move_player_pos'],
      ['protocol', 'keep_alive', 'keep_alive'],
    ])
    expect(parsed.clicksByTick.get(12)).toEqual({ leftClick: false, rightClick: true })
    expect(parsed.clicksByTick.get(14)).toEqual({ leftClick: true, rightClick: false })
  })

  it('describes container views, client settings, and notable player-state changes', () => {
    const parsed = parseCaptureEvents([
      line(20, 1, { clientInformation: { language: 'en_us', mainHand: 'right', source: 'CLIENT_INFORMATION_SOURCE_JOIN_SNAPSHOT', viewDistance: 10 } }),
      line(21, 2, { containerView: { containerId: 1, containerSlotCount: 27, kind: 'CONTAINER_VIEW_KIND_OPENED', menuType: 'minecraft:generic_9x3', source: { blockEntityType: 'minecraft:chest', blockPos: { x: 4, y: -60 } } } }),
      line(21, 3, { containerView: { containerId: 1, kind: 'CONTAINER_VIEW_KIND_CONTENTS', slots: [{ count: 1, itemId: 'minecraft:diamond', slot: 0 }] } }),
      line(22, 4, { playerState: { dimension: 'minecraft:overworld', gameMode: 'survival', health: 20 } }),
      line(23, 5, { playerState: { dimension: 'minecraft:overworld', gameMode: 'survival', health: 20 } }),
      line(24, 6, { playerState: { dimension: 'minecraft:the_nether', gameMode: 'survival', health: 17.5 } }),
      line(25, 7, { controlState: { state: { forward: true, selectedSlot: 1 } } }),
      '{not json',
    ].join('\n'))

    expect(parsed.entries.map(entry => [entry.kind, entry.summary])).toEqual([
      ['client', 'Client settings at join: en_us, view 10 chunks, right hand'],
      ['container', 'Opened chest at 4 -60 0 (#1, generic 9x3, 27 slots)'],
      ['container', 'Contents of #1: 1× diamond @0'],
      ['tick-state', 'hp 20'],
      ['tick-state', 'hp 20'],
      ['state-change', 'Dimension overworld → the nether · Health 20 → 17.5 (-2.5)'],
      ['tick-state', 'W · yaw 0.0° pitch 0.0° · slot 2'],
    ])
    expect(parsed.invalidLineCount).toBe(1)
    expect(parsed.controlSamples).toHaveLength(1)
    expect(parsed.hotbarSamples.map(sample => sample.serverTick)).toEqual([22, 23, 24])
  })

  it('streams large files in slices without losing lines across chunk boundaries', async () => {
    const lines = Array.from({ length: 15_000 }, (_, index) => line(index, index, { controlState: { state: { forward: index % 2 === 0 } } }))
    const text = `${lines.join('\n')}\n`
    const bytes = new TextEncoder().encode(text)
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        for (let offset = 0; offset < bytes.length; offset += 7_919)
          controller.enqueue(bytes.slice(offset, offset + 7_919))
        controller.close()
      },
    })
    let progressCalls = 0
    const parsed = await streamCaptureEvents(new Response(body), { budgetMs: 1, onProgress: () => progressCalls++ })

    expect(parsed.lineCount).toBe(15_000)
    expect(parsed.controlSamples.at(-1)?.serverTick).toBe(14_999)
    expect(progressCalls).toBeGreaterThan(0)
  })
})

describe('event log rows', () => {
  it('maps entries through each clip and shows a cut Play once per clip', () => {
    const replays = [
      testReplay('alice', { end: 200, player: 'alice', sessionId: 's1', start: 100 }),
      testReplay('bob', { end: 220, player: 'bob', sessionId: 's1', start: 120 }),
    ]
    let episode = addReplaysToEpisode(createEmptyEpisode(), replays)!
    const alice = parseCaptureEvents([
      line(110, 1, apply('swing', 'swing')),
      line(150, 2, apply('use_item_on', 'use')),
      line(150, 3, { controlState: { state: {} } }),
    ].join('\n'))
    const bob = parseCaptureEvents(line(130, 1, { containerView: { containerId: 1, kind: 'CONTAINER_VIEW_KIND_CLOSED' } }))
    const parsed = new Map([['/events/alice.jsonl', alice], ['/events/bob.jsonl', bob]])

    const rows = buildEventLogRows(episode, parsed)
    expect(rows.map(row => [row.episodeTick, row.playerName, row.entry.label])).toEqual([
      [0, 'alice', 'join'],
      [10, 'alice', 'swing'],
      [20, 'bob', 'join'],
      [30, 'bob', 'closed'],
      [50, 'alice', 'use_item_on'],
      [50, 'alice', 'control_state'],
      [100, 'alice', 'leave'],
      [120, 'bob', 'leave'],
    ])

    // Cutting alice at episode tick 50 keeps each event in exactly one half.
    const aliceSegment = episode.segments.find(segment => segment.placementId === 'play:alice')!
    episode = cutPlacement(episode, aliceSegment.id, 50)!
    const cutRows = buildEventLogRows(episode, parsed).filter(row => row.playerName === 'alice')
    expect(cutRows.map(row => [row.episodeTick, row.entry.label, row.placementId])).toEqual([
      [0, 'join', 'play:alice'],
      [10, 'swing', 'play:alice'],
      [50, 'use_item_on', expect.stringContaining('play:alice:cut:')],
      [50, 'control_state', expect.stringContaining('play:alice:cut:')],
      [100, 'leave', expect.stringContaining('play:alice:cut:')],
    ])

    const visible = filterEventLogRows(rows, { kinds: new Set(['action', 'container']), players: new Set(['uuid:uuid-alice']), query: '' })
    expect(visible.map(row => row.entry.label)).toEqual(['swing', 'use_item_on'])
    expect(filterEventLogRows(rows, { kinds: new Set(['action', 'connection', 'container']), players: new Set(), query: 'closed' }).map(row => row.playerName)).toEqual(['bob'])
    expect(currentRowIndex(visible, 49)).toBe(0)
    expect(currentRowIndex(visible, 50)).toBe(1)
    expect(currentRowIndex(visible, 5)).toBe(-1)
  })
})
