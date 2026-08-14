import type { RecorderMinecraftApiV1Replay } from '@proj-airi/recorder-minecraft-api'

import type { CommitSegmentEdit, EpisodeDraft } from '../domain'

import { defineStore } from 'pinia'
import { computed, shallowRef } from 'vue'

import { loadSupportedExtensionTracks } from '../../extensions/registry'
import {
  addReplayToEpisode,
  commitPlacementEdit,
  createEmptyEpisode,
  cutPlacement,
  deletePlacement,
  reorderPlacementTracks,
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

  async function addReplay(replay: RecorderMinecraftApiV1Replay): Promise<boolean> {
    if (!replay.connectionId || episode.value.placements.some(placement => placement.connectionId === replay.connectionId))
      return false

    const extensions = await loadSupportedExtensionTracks(replay.extensions)
    const nextEpisode = addReplayToEpisode(episode.value, replay, extensions)
    if (!nextEpisode)
      return false

    episode.value = nextEpisode
    history.value = { redo: [], undo: [] }
    return true
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

  function reorderTrack(sourceIndex: number, targetIndex: number): void {
    commit(reorderPlacementTracks(episode.value, sourceIndex, targetIndex))
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
