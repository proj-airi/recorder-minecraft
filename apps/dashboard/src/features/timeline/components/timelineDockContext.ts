import type { InjectionKey, Ref, ShallowRef } from 'vue'

import type { TimelineSession } from '../composables/useTimelineSession'
import type { EpisodeDraft } from '../domain'

import { inject } from 'vue'

export interface TimelineDockContext {
  canCut: Readonly<Ref<boolean>>
  canRedo: Readonly<Ref<boolean>>
  canUndo: Readonly<Ref<boolean>>
  cutAtPlayhead: () => void
  episode: Readonly<Ref<EpisodeDraft>>
  redo: () => void
  reorderTrack: (sourceIndex: number, targetIndex: number) => void
  session: TimelineSession
  setVerticalScrollTop: (scrollTop: number) => void
  undo: () => void
  verticalScrollTop: Readonly<ShallowRef<number>>
  zoomBy: (factor: number) => void
}

export const timelineDockContextKey: InjectionKey<TimelineDockContext> = Symbol('timeline-dock-context')

export function useTimelineDockContext(): TimelineDockContext {
  const context = inject<TimelineDockContext>(timelineDockContextKey)
  if (!context)
    throw new Error('Timeline Dockview panels must be mounted inside TimelineEditor')

  return context
}
