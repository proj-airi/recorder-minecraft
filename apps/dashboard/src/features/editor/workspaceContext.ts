import type { RecorderMinecraftApiV1Replay } from '@proj-airi/recorder-minecraft-api'
import type { InjectionKey, Ref } from 'vue'

import type { ReplayPlayback } from '../media/composables/useReplayPlayback'
import type { ArtifactCatalog } from '../resources/composables/useArtifactCatalog'
import type { TimelineSession } from '../timeline/composables/useTimelineSession'
import type { EpisodeDraft } from '../timeline/domain'

export interface EditorWorkspaceContext {
  addReplay: (replay: RecorderMinecraftApiV1Replay) => void
  canRedo: Readonly<Ref<boolean>>
  canUndo: Readonly<Ref<boolean>>
  catalog: ArtifactCatalog
  close: () => void
  cutSegment: (segmentId: string, atTick: number) => void
  episode: () => EpisodeDraft
  redo: () => void
  reorderTrack: (sourceIndex: number, targetIndex: number) => void
  replayPlayback: ReplayPlayback
  session: TimelineSession
  undo: () => void
}

export const editorWorkspaceContextKey: InjectionKey<EditorWorkspaceContext> = Symbol('editor-workspace-context')
