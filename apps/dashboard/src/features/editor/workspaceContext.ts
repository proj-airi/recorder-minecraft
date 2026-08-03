import type { InjectionKey, Ref } from 'vue'

import type { ArtifactCatalog } from '../resources/composables/useArtifactCatalog'
import type { TimelineSession } from '../timeline/composables/useTimelineSession'
import type { EpisodeDraft } from '../timeline/domain'

export interface EditorWorkspaceContext {
  canRedo: Readonly<Ref<boolean>>
  canUndo: Readonly<Ref<boolean>>
  catalog: ArtifactCatalog
  close: () => void
  cutSegment: (segmentId: string, atTick: number) => void
  episode: () => EpisodeDraft
  redo: () => void
  reorderTrack: (sourceIndex: number, targetIndex: number) => void
  session: TimelineSession
  undo: () => void
}

export const editorWorkspaceContextKey: InjectionKey<EditorWorkspaceContext> = Symbol('editor-workspace-context')
