import type {
  RecorderMinecraftApiV1PlayExtension,
  RecorderMinecraftApiV1PlayExtensionAsset,
} from '@proj-airi/recorder-minecraft-api'
import type { Component } from 'vue'

import type { NormalizedTimelineItem } from '../timeline/domain'

export interface ExtensionAssetAccess {
  text: (asset: RecorderMinecraftApiV1PlayExtensionAsset) => Promise<string>
}

export interface ExtensionTrackProjection {
  descriptor: RecorderMinecraftApiV1PlayExtension
  items: NormalizedTimelineItem[]
  label: string
}

export interface PlayExtensionModule {
  extensionType: string
  loadTrack: (
    descriptor: RecorderMinecraftApiV1PlayExtension,
    assets: ExtensionAssetAccess,
  ) => Promise<ExtensionTrackProjection>
  view: {
    component: Component
    icon: string
    label: string
  }
}

export const browserExtensionAssetAccess: ExtensionAssetAccess = {
  async text(asset) {
    if (!asset.url)
      throw new Error('The extension asset URL is missing')

    const response = await fetch(asset.url)
    if (!response.ok)
      throw new Error(`Extension asset request failed with status ${response.status}`)
    return response.text()
  },
}

export function extensionViewId(extensionType?: string): `extension:${string}` {
  return `extension:${extensionType ?? 'unknown'}`
}
