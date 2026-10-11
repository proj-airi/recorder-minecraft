import type { TimelineSession } from './composables/useTimelineSession'
import type { useEpisodeStore } from './stores/episode'

import { registerTimelineDataTrackProvider } from './data-tracks/registry'

export interface RecorderTimelineDevtools {
  registerTimelineDataTrackProvider: typeof registerTimelineDataTrackProvider
  session: TimelineSession
  store: ReturnType<typeof useEpisodeStore>
}

declare global {
  interface Window {
    __recorderTimeline?: RecorderTimelineDevtools
  }
}

/**
 * Development builds only: exposes the timeline store, session, and data track registry as
 * `window.__recorderTimeline`, so local scripts can add sessions and register fixture providers
 * without shipping fixture data. Production builds remove this branch.
 */
export function exposeTimelineDevtools(devtools: Omit<RecorderTimelineDevtools, 'registerTimelineDataTrackProvider'>): void {
  if (!import.meta.env.DEV || typeof window === 'undefined')
    return
  window.__recorderTimeline = { ...devtools, registerTimelineDataTrackProvider }
}
