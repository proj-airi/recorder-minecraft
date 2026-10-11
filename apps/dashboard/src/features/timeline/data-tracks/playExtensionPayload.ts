import type { RecorderMinecraftApiV1PlayExtension } from '@proj-airi/recorder-minecraft-api'

import type { NormalizedTimelineItem } from '../domain'

/** Provider id of the built-in Play extension data tracks. */
export const PLAY_EXTENSION_PROVIDER_ID = 'play-extension'

/** `payload` of every item produced by the Play extension provider. */
export interface PlayExtensionDataPayload {
  connectionId: string
  descriptor: RecorderMinecraftApiV1PlayExtension
  item: NormalizedTimelineItem
}

export function isPlayExtensionPayload(value: unknown): value is PlayExtensionDataPayload {
  return typeof value === 'object' && value !== null && 'descriptor' in value && 'item' in value && 'connectionId' in value
}
