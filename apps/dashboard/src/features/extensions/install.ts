import { registerTimelineDataTrackProvider } from '../timeline/data-tracks/registry'
import { extensionDataTrackProviders } from './registry'

/**
 * Registers every extension data track provider on the page-wide timeline registry. Returns a
 * function that unregisters them. Kept apart from `registry.ts` because the built-in Play
 * extension provider imports that module.
 */
export function installExtensionDataTrackProviders(): () => void {
  const unregister = extensionDataTrackProviders.map(provider => registerTimelineDataTrackProvider(provider))
  return () => unregister.forEach(dispose => dispose())
}
