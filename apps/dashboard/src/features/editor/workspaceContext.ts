import type { RecorderMinecraftApiV1PlayExtension, RecorderMinecraftApiV1Replay } from '@proj-airi/recorder-minecraft-api'
import type { InjectionKey, Ref } from 'vue'

import type { ExtensionAssetAccess } from '../extensions/domain'
import type { ReplayPlayback } from '../media/composables/useReplayPlayback'
import type { ArtifactCatalog } from '../resources/composables/useArtifactCatalog'
import type { TimelineSession } from '../timeline/composables/useTimelineSession'
import type { SelectedTimelineDataItem, TimelineDataItemRef, TimelineDataTrack } from '../timeline/data-tracks/types'
import type { EpisodeDraft, NormalizedTimelineItem, PlayPlacement, TimelineWorldSessionSource } from '../timeline/domain'

import { isPlayExtensionPayload, PLAY_EXTENSION_PROVIDER_ID } from '../timeline/data-tracks/playExtensionPayload'
import { placementContainsTick, playServerTickAt } from '../timeline/ticks'

export interface EditorWorkspaceContext {
  addReplay: (replay: RecorderMinecraftApiV1Replay) => void
  /** Adds every Play of one session in a single undoable edit, aligned by Server tick. */
  addSession: (sessionId: string, replays: readonly RecorderMinecraftApiV1Replay[], world?: TimelineWorldSessionSource) => void
  canRedo: Readonly<Ref<boolean>>
  canUndo: Readonly<Ref<boolean>>
  catalog: ArtifactCatalog
  close: () => void
  cutSegment: (segmentId: string, atTick: number) => void
  /** Latest-starting data item at an episode tick (defaults to the playhead) among matching tracks. */
  dataItemAt: (filter: (track: TimelineDataTrack) => boolean, episodeTick?: number) => null | SelectedTimelineDataItem
  episode: () => EpisodeDraft
  extensionAssets: ExtensionAssetAccess
  extensionAtPlayhead: (extensionType: string) => null | SelectedPlayExtension
  redo: () => void
  reorderTrack: (sourceTrackId: string, targetTrackId: string) => void
  replayPlayback: ReplayPlayback
  selectDataItem: (item: null | TimelineDataItemRef) => void
  /** The data item selected on the timeline, resolved against the current episode. */
  selectedDataItem: Readonly<Ref<null | SelectedTimelineDataItem>>
  selectedExtension: Readonly<Ref<null | SelectedPlayExtension>>
  session: TimelineSession
  undo: () => void
}

export interface SelectedPlayExtension {
  descriptor: RecorderMinecraftApiV1PlayExtension
  item: NormalizedTimelineItem
  placement: PlayPlacement
  playServerTick: null | number
}

export const editorWorkspaceContextKey: InjectionKey<EditorWorkspaceContext> = Symbol('editor-workspace-context')

/** Track filter for the Play extension data tracks of one extension type. */
export function isPlayExtensionTrack(extensionType: string): (track: TimelineDataTrack) => boolean {
  return track => track.providerId === PLAY_EXTENSION_PROVIDER_ID && track.descriptorKey === extensionType
}

/** Adapts a selected data item from the Play extension provider to the extension view shape. */
export function toSelectedPlayExtension(selected: null | SelectedTimelineDataItem, episodeTick: number): null | SelectedPlayExtension {
  const payload = selected?.item.payload
  if (!selected || selected.track.providerId !== PLAY_EXTENSION_PROVIDER_ID || !isPlayExtensionPayload(payload) || !selected.placement)
    return null

  const placement = selected.placement
  return {
    descriptor: payload.descriptor,
    item: payload.item,
    placement,
    playServerTick: placementContainsTick(placement, episodeTick) || episodeTick === placement.endTick ? playServerTickAt(placement, episodeTick) : null,
  }
}
