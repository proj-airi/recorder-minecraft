import type { RecorderMinecraftApiV1PlayExtension } from '@proj-airi/recorder-minecraft-api'

import type { ExtensionAssetAccess, ExtensionTrackProjection, PlayExtensionModule } from './domain'

import { airicraftPlannerModule } from './airicraft-planner/module'
import { browserExtensionAssetAccess } from './domain'

export const playExtensionModules: readonly PlayExtensionModule[] = [airicraftPlannerModule]

export function extensionLabel(extensionType: string): string {
  return playExtensionModules.find(module => module.extensionType === extensionType)?.view.label ?? extensionType
}

export async function loadSupportedExtensionTracks(
  descriptors: RecorderMinecraftApiV1PlayExtension[] = [],
  assets: ExtensionAssetAccess = browserExtensionAssetAccess,
): Promise<ExtensionTrackProjection[]> {
  const projections = await Promise.all(descriptors.map(async (descriptor) => {
    const module = playExtensionModules.find(candidate => candidate.extensionType === descriptor.extensionType)
    if (!module)
      return null
    try {
      return await module.loadTrack(descriptor, assets)
    }
    catch {
      return null
    }
  }))
  return projections.filter((projection): projection is ExtensionTrackProjection => projection !== null)
}
