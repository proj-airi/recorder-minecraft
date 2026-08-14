import type { EpisodeDraft } from '../timeline/domain'

import { describe, expect, it } from 'vitest'

import { findPlayExtensionAt } from './workspaceContext'

describe('playhead extension selection', () => {
  it('selects the latest matching segment that contains the playhead', () => {
    const episode: EpisodeDraft = {
      durationTicks: 40,
      id: 'episode',
      placements: [{
        connectionId: 'connection',
        endTick: 40,
        id: 'play:connection',
        playEndServerTick: 140,
        playStartServerTick: 100,
        sourceEndServerTick: 140,
        sourceStartServerTick: 100,
        startTick: 0,
      }],
      revision: 1,
      segments: [
        { color: '#8b5cf6', editable: false, endTick: 25, id: 'segment:call-1', label: 'Call 1', placementId: 'play:connection', sourceItemId: 'call-1', startTick: 10, trackId: 'track:planner' },
        { color: '#8b5cf6', editable: false, endTick: 30, id: 'segment:call-2', label: 'Call 2', placementId: 'play:connection', sourceItemId: 'call-2', startTick: 20, trackId: 'track:planner' },
      ],
      title: 'Episode',
      tracks: [{
        extension: {
          descriptor: { extensionType: 'airicraft.planner' },
          items: [
            { color: '#8b5cf6', data: { callId: 'call-1' }, endServerTick: 125, id: 'call-1', kind: 'interval', label: 'Call 1', startServerTick: 110 },
            { color: '#8b5cf6', data: { callId: 'call-2' }, endServerTick: 130, id: 'call-2', kind: 'interval', label: 'Call 2', startServerTick: 120 },
          ],
        },
        id: 'track:planner',
        kind: 'data',
        label: 'Planner',
        placementId: 'play:connection',
        role: 'extension',
      }],
    }

    expect(findPlayExtensionAt(episode, 'airicraft.planner', 15)?.item.id).toBe('call-1')
    expect(findPlayExtensionAt(episode, 'airicraft.planner', 22)).toMatchObject({
      item: { id: 'call-2' },
      playServerTick: 122,
    })
    expect(findPlayExtensionAt(episode, 'airicraft.planner', 30)).toBeNull()
    expect(findPlayExtensionAt(episode, 'another.extension', 22)).toBeNull()
  })
})
