import type { RecorderMinecraftApiV1Replay } from '@proj-airi/recorder-minecraft-api'
import type { InjectionKey, Ref } from 'vue'

import type { ExtensionAssetAccess } from '../extensions/domain'
import type { ReplayPlayback } from '../media/composables/useReplayPlayback'
import type { ArtifactCatalog } from '../resources/composables/useArtifactCatalog'
import type { TimelineSession } from '../timeline/composables/useTimelineSession'
import type { EpisodeDraft, NormalizedTimelineItem, PlayPlacement } from '../timeline/domain'

import { playServerTickAt } from '../timeline/replay'

export interface EditorWorkspaceContext {
  addReplay: (replay: RecorderMinecraftApiV1Replay) => void
  canRedo: Readonly<Ref<boolean>>
  canUndo: Readonly<Ref<boolean>>
  catalog: ArtifactCatalog
  close: () => void
  cutSegment: (segmentId: string, atTick: number) => void
  episode: () => EpisodeDraft
  extensionAssets: ExtensionAssetAccess
  extensionAtPlayhead: (extensionType: string) => null | SelectedPlayExtension
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

export function findPlayExtensionAt(
  episode: EpisodeDraft,
  extensionType: string,
  episodeTick: number,
): null | SelectedPlayExtension {
  const segment = episode.segments.reduce<EpisodeDraft['segments'][number] | undefined>((current, candidate) => {
    if (episodeTick < candidate.startTick || episodeTick >= candidate.endTick)
      return current

    const track = episode.tracks.find(item => item.id === candidate.trackId)
    if (track?.extension?.descriptor.extensionType !== extensionType)
      return current

    return !current || candidate.startTick >= current.startTick ? candidate : current
  }, undefined)

  return playExtensionFromSegment(episode, segment?.id ?? null, episodeTick)
}

export function findSelectedPlayExtension(
  episode: EpisodeDraft,
  segmentId: null | string,
  episodeTick: number,
): null | SelectedPlayExtension {
  return playExtensionFromSegment(episode, segmentId, episodeTick)
}

function playExtensionFromSegment(
  episode: EpisodeDraft,
  segmentId: null | string,
  episodeTick: number,
): null | SelectedPlayExtension {
  const segment = episode.segments.find(candidate => candidate.id === segmentId)
  const track = episode.tracks.find(candidate => candidate.id === segment?.trackId)
  const placement = episode.placements.find(candidate => candidate.id === segment?.placementId)
  const item = track?.extension?.items.find(candidate => candidate.id === segment?.sourceItemId)
  if (!track?.extension || !placement || !item)
    return null

  const playServerTick = episodeTick < placement.startTick || episodeTick > placement.endTick
    ? null
    : playServerTickAt(placement, episodeTick)
  return { descriptor: track.extension.descriptor, item, placement, playServerTick }
}
