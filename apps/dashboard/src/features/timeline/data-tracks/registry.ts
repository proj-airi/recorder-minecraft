import type { ShallowRef } from 'vue'

import type { TimelineDataTrackProvider } from './types'

import { shallowRef } from 'vue'

import { playExtensionDataTrackProvider } from './playExtensionProvider'

const providers = shallowRef<readonly TimelineDataTrackProvider[]>([playExtensionDataTrackProvider])

/** Reactive list of registered providers; the timeline re-describes its tracks when it changes. */
export const timelineDataTrackProviders: Readonly<ShallowRef<readonly TimelineDataTrackProvider[]>> = providers

/**
 * Registers a data track provider for every timeline in this page. Returns an unregister
 * function. Registering an id twice replaces the earlier provider.
 */
export function registerTimelineDataTrackProvider(provider: TimelineDataTrackProvider): () => void {
  providers.value = [...providers.value.filter(candidate => candidate.id !== provider.id), provider]
  return () => {
    providers.value = providers.value.filter(candidate => candidate !== provider)
  }
}
