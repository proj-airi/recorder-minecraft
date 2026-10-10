import type { RecorderMinecraftApiV1Replay } from '@proj-airi/recorder-minecraft-api'

import type { CommitSegmentEdit, EpisodeDraft, TimelineWorldSessionSource } from '../domain'

import { defineStore } from 'pinia'
import { computed, shallowRef } from 'vue'

import {
  addReplaysToEpisode,
  addReplayToEpisode,
  commitPlacementEdit,
  createEmptyEpisode,
  cutPlacement,
  deletePlacement,
  reorderLaneGroups,
} from '../replay'

interface EpisodeHistory {
  redo: EpisodePatch[]
  undo: EpisodePatch[]
}

interface EpisodePatch {
  after: EpisodeDraft
  before: EpisodeDraft
}

export const useEpisodeStore = defineStore('episode', () => {
  const episode = shallowRef(createEmptyEpisode())
  const history = shallowRef<EpisodeHistory>({ redo: [], undo: [] })
  const canRedo = computed(() => history.value.redo.length > 0)
  const canUndo = computed(() => history.value.undo.length > 0)

  /** Adds one Play as an undoable edit. Extension data loads lazily through data tracks. */
  function addReplay(replay: RecorderMinecraftApiV1Replay): boolean {
    return commit(addReplayToEpisode(episode.value, replay))
  }

  /**
   * Adds every Play of a session in one undoable edit, aligned by Server tick. Plays from other
   * sessions in `replays` are ignored.
   */
  function addSession(sessionId: string, replays: readonly RecorderMinecraftApiV1Replay[], world?: TimelineWorldSessionSource): boolean {
    const sessionReplays = replays.filter(replay => replay.sessionId === sessionId)
    return commit(addReplaysToEpisode(episode.value, sessionReplays, world?.sessionId === sessionId ? world : undefined))
  }

  function commit(nextEpisode: EpisodeDraft | null): boolean {
    if (!nextEpisode)
      return false
    const patch = { after: nextEpisode, before: episode.value }
    episode.value = nextEpisode
    history.value = { redo: [], undo: [...history.value.undo, patch] }
    return true
  }

  function commitSegmentEdit(edit: CommitSegmentEdit): void {
    commit(commitPlacementEdit(episode.value, edit.segmentId, edit.startTick, edit.endTick, edit.trackId))
  }

  function cutSegment(segmentId: string, atTick: number): boolean {
    return commit(cutPlacement(episode.value, segmentId, atTick))
  }

  function deleteSegment(segmentId: string): boolean {
    return commit(deletePlacement(episode.value, segmentId))
  }

  /** Moves the lane group of `sourceTrackId` to the position of `targetTrackId`. */
  function reorderTrack(sourceTrackId: string, targetTrackId: string): void {
    commit(reorderLaneGroups(episode.value, sourceTrackId, targetTrackId))
  }

  function redo(): void {
    const patch = history.value.redo.at(-1)
    if (!patch)
      return

    episode.value = patch.after
    history.value = {
      redo: history.value.redo.slice(0, -1),
      undo: [...history.value.undo, patch],
    }
  }

  function undo(): void {
    const patch = history.value.undo.at(-1)
    if (!patch)
      return

    episode.value = patch.before
    history.value = {
      redo: [...history.value.redo, patch],
      undo: history.value.undo.slice(0, -1),
    }
  }

  return {
    addReplay,
    addSession,
    canRedo,
    canUndo,
    commitSegmentEdit,
    cutSegment,
    deleteSegment,
    episode,
    history,
    redo,
    reorderTrack,
    undo,
  }
})
