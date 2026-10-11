import type { RecorderMinecraftApiV1PlayExtension } from '@proj-airi/recorder-minecraft-api'

import type { ExtensionAssetAccess, PlayExtensionModule } from '../../extensions/domain'
import type { EpisodeReplaySource, NormalizedTimelineItem } from '../domain'
import type { PlayExtensionDataPayload } from './playExtensionPayload'
import type { TimelineDataItem, TimelinePlayerDataTrackProvider } from './types'

import { browserExtensionAssetAccess, extensionViewId } from '../../extensions/domain'
import { playExtensionModules } from '../../extensions/registry'
import { PLAY_EXTENSION_PROVIDER_ID } from './playExtensionPayload'

export { isPlayExtensionPayload, PLAY_EXTENSION_PROVIDER_ID } from './playExtensionPayload'
export type { PlayExtensionDataPayload } from './playExtensionPayload'

/**
 * Re-implements per-Play extension tracks (`extensions/<type>/manifest.json`) on the data track
 * contract: one track per extension type and player lane, merging the items of every Play.
 */
export function createPlayExtensionDataTrackProvider(
  customModules?: readonly PlayExtensionModule[],
  assets: ExtensionAssetAccess = browserExtensionAssetAccess,
): TimelinePlayerDataTrackProvider {
  return {
    describe(target) {
      // Resolved lazily: the extension registry imports views that import this module's consumers.
      const modules = customModules ?? playExtensionModules
      const byType = new Map<string, { entries: { descriptor: RecorderMinecraftApiV1PlayExtension, play: EpisodeReplaySource }[], module: PlayExtensionModule }>()
      for (const play of target.plays) {
        for (const descriptor of play.replay?.extensions ?? []) {
          const module = modules.find(candidate => candidate.extensionType === descriptor.extensionType)
          if (!module)
            continue
          const group = byType.get(module.extensionType) ?? { entries: [], module }
          group.entries.push({ descriptor, play })
          byType.set(module.extensionType, group)
        }
      }

      return [...byType.entries()].map(([extensionType, { entries, module }]) => ({
        key: extensionType,
        label: module.view.label,
        async load() {
          const perPlay = await Promise.all(entries.map(async ({ descriptor, play }) => {
            const projection = await module.loadTrack(descriptor, assets)
            return projection.items.map(item => toDataItem(play.connectionId, descriptor, item))
          }))
          return perPlay.flat()
        },
        order: 100,
        viewId: extensionViewId(extensionType),
      }))
    },
    id: PLAY_EXTENSION_PROVIDER_ID,
    scope: 'player',
  }
}

export const playExtensionDataTrackProvider = createPlayExtensionDataTrackProvider()

function toDataItem(connectionId: string, descriptor: RecorderMinecraftApiV1PlayExtension, item: NormalizedTimelineItem): TimelineDataItem {
  const payload: PlayExtensionDataPayload = { connectionId, descriptor, item }
  const base = { color: item.color, connectionId, id: `${connectionId}:${item.id}`, label: item.label, payload }
  return item.kind === 'point'
    ? { ...base, kind: 'point', serverTick: item.serverTick }
    : { ...base, endServerTick: item.endServerTick, kind: 'interval', startServerTick: item.startServerTick }
}
