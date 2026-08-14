import { describe, expect, it } from 'vitest'

import { loadSupportedExtensionTracks } from '../registry'
import { parsePlannerCalls } from './module'

describe('airicraft planner extension', () => {
  it('reads final planner calls with Server-tick anchors', () => {
    const records = parsePlannerCalls([
      JSON.stringify({
        callId: 'call-1',
        outcome: { assistantContent: { text: 'Mine stone' }, status: 'completed', toolCalls: [] },
        request: { messages: [{ content: 'Continue', role: 'user' }], tools: [] },
        sequence: '1',
        timeline: {
          applied: { serverTick: '130' },
          completed: { serverTick: '129' },
          submitted: { serverTick: '120' },
        },
      }),
      '',
    ].join('\n'))

    expect(records).toHaveLength(1)
    expect(records[0]).toMatchObject({
      callId: 'call-1',
      outcome: { status: 'completed' },
      timeline: { applied: { serverTick: '130' }, submitted: { serverTick: '120' } },
    })
  })

  it('rejects a call without a submitted Server tick', () => {
    expect(() => parsePlannerCalls('{"callId":"call-1","sequence":"1","timeline":{}}'))
      .toThrow('Planner call line 1 does not match airicraft.planner-call.v1')
  })

  it('loads the shipped module and ignores unsupported extension types', async () => {
    const source = JSON.stringify({
      callId: 'call-1',
      sequence: '1',
      timeline: { applied: { serverTick: '130' }, completed: { serverTick: '129' }, submitted: { serverTick: '120' } },
    })
    const tracks = await loadSupportedExtensionTracks([
      {
        assets: [{ mediaType: 'application/x-ndjson', role: 'planner_calls', schema: 'airicraft.planner-call.v1', url: '/planner.jsonl' }],
        extensionType: 'airicraft.planner',
      },
      { extensionType: 'llm.annotation' },
    ], { text: async () => source })

    expect(tracks).toHaveLength(1)
    expect(tracks[0]).toMatchObject({
      descriptor: { extensionType: 'airicraft.planner' },
      label: 'Airicraft planner',
    })
    expect(tracks[0]?.items.map(item => item.kind)).toEqual(['interval', 'point'])
  })
})
