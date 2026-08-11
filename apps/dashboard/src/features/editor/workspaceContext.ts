import type { RecorderMinecraftApiV1Replay } from '@proj-airi/recorder-minecraft-api'
import type { InjectionKey, Ref } from 'vue'

import type { ExtensionAssetAccess } from '../extensions/domain'
import type { ReplayPlayback } from '../media/composables/useReplayPlayback'
import type { ArtifactCatalog } from '../resources/composables/useArtifactCatalog'
import type { TimelineSession } from '../timeline/composables/useTimelineSession'
import type { EpisodeDraft, NormalizedTimelineItem, PlayPlacement } from '../timeline/domain'

export interface EditorWorkspaceContext {
  addReplay: (replay: RecorderMinecraftApiV1Replay) => void
  canRedo: Readonly<Ref<boolean>>
  canUndo: Readonly<Ref<boolean>>
  catalog: ArtifactCatalog
  close: () => void
  cutSegment: (segmentId: string, atTick: number) => void
  episode: () => EpisodeDraft
  extensionAssets: ExtensionAssetAccess
  redo: () => void
  reorderTrack: (sourceIndex: number, targetIndex: number) => void
  replayPlayback: ReplayPlayback
  selectedExtension: Readonly<Ref<null | SelectedPlayExtension>>
  session: TimelineSession
  undo: () => void
}

export interface SelectedPlayExtension {
  descriptor: NonNullable<EpisodeDraft['tracks'][number]['extension']>['descriptor']
  item: NormalizedTimelineItem
  placement: PlayPlacement
  playServerTick: null | number
}

export const editorWorkspaceContextKey: InjectionKey<EditorWorkspaceContext> = Symbol('editor-workspace-context')
