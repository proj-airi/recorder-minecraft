import type { PlayExtensionModule } from '../extensions/domain'

import { describe, expect, it } from 'vitest'
import { defineComponent, effectScope, nextTick, shallowRef } from 'vue'

import { useTimelineSession } from '../timeline/composables/useTimelineSession'
import { createPlayExtensionDataTrackProvider } from '../timeline/data-tracks/playExtensionProvider'
import { testReplay } from '../timeline/fixtures/replays'
import { addReplaysToEpisode, createEmptyEpisode } from '../timeline/replay'
import { isPlayExtensionTrack, toSelectedPlayExtension } from './workspaceContext'

const plannerModule: PlayExtensionModule = {
  extensionType: 'airicraft.planner',
  loadTrack: async descriptor => ({
    descriptor,
    items: [
      { color: '#8b5cf6', data: { callId: 'call-1' }, endServerTick: 125, id: 'call-1', kind: 'interval', label: 'Call 1', startServerTick: 110 },
      { color: '#8b5cf6', data: { callId: 'call-2' }, endServerTick: 130, id: 'call-2', kind: 'interval', label: 'Call 2', startServerTick: 120 },
    ],
    label: 'Planner',
  }),
  view: { component: defineComponent({ render: () => null }), icon: 'i-mingcute-ai-line', label: 'Planner' },
}

describe('data item selection', () => {
  it('selects data items and adapts Play extension items at the playhead', async () => {
    const replay = testReplay('connection', { end: 140, sessionId: 's1', start: 100 })
    replay.extensions = [{ extensionType: 'airicraft.planner' }]
    const episode = shallowRef(addReplaysToEpisode(createEmptyEpisode(), [replay])!)
    const scope = effectScope()
    const session = scope.run(() => useTimelineSession(episode, () => undefined, true, {
      providers: () => [createPlayExtensionDataTrackProvider([plannerModule])],
    }))!

    try {
      await expect.poll(() => session.dataTracks.value[0]?.status).toBe('ready')
      const planner = isPlayExtensionTrack('airicraft.planner')
      const at = (tick: number) => toSelectedPlayExtension(session.dataItemAt(planner, tick), tick)

      expect(at(15)?.item.id).toBe('call-1')
      expect(at(22)).toMatchObject({ item: { id: 'call-2' }, playServerTick: 122 })
      expect(at(30)).toBeNull()
      expect(session.dataItemAt(isPlayExtensionTrack('another.extension'), 22)).toBeNull()

      const track = session.dataTracks.value[0]!
      session.selectDataItem({ itemId: 'connection:call-1', trackId: track.id })
      await nextTick()
      expect(session.selectedDataItem.value).toMatchObject({
        episodeTick: 10,
        item: { id: 'connection:call-1', kind: 'interval' },
        placement: { id: 'play:connection' },
        serverTick: 110,
        track: { id: track.id, viewId: 'extension:airicraft.planner' },
      })
      expect(session.dataLaneSelection.value).toEqual({ index: 0, trackId: track.id })
      expect(toSelectedPlayExtension(session.selectedDataItem.value, 12)).toMatchObject({ item: { id: 'call-1' }, playServerTick: 112 })

      session.selectDataItem({ itemId: 'missing', trackId: track.id })
      expect(session.selectedDataItem.value).toBeNull()
    }
    finally {
      scope.stop()
    }
  })
})
