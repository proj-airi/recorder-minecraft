import type { CommitSegmentEdit, EpisodeSegment, EpisodeTrack } from '../domain'

import { defineStore } from 'pinia'
import { computed, shallowRef } from 'vue'

import { createEpisode, demoOptions } from '../fixtures/episode'

interface SegmentHistory {
  redo: TimelinePatch[]
  undo: TimelinePatch[]
}

interface SegmentPatch {
  after: EpisodeSegment[]
  before: EpisodeSegment[]
  type: 'segments'
}

type TimelinePatch = SegmentPatch | TrackOrderPatch

interface TrackOrderPatch {
  after: string[]
  before: string[]
  type: 'track-order'
}

export const useEpisodeStore = defineStore('episode', () => {
  // Demo project state is replaced as one immutable revision. The timeline engine only receives projections of it.
  const episode = shallowRef(createEpisode(demoOptions))
  const history = shallowRef<SegmentHistory>({ redo: [], undo: [] })
  const canRedo = computed(() => history.value.redo.length > 0)
  const canUndo = computed(() => history.value.undo.length > 0)

  function applyPatch(patch: TimelinePatch, direction: 'redo' | 'undo'): void {
    if (patch.type === 'track-order') {
      const order = direction === 'redo' ? patch.after : patch.before
      const tracksById = new Map(episode.value.tracks.map(track => [track.id, track]))
      const tracks = order.map((trackId): EpisodeTrack => {
        const track = tracksById.get(trackId)
        if (!track)
          throw new Error(`Cannot restore timeline track order: track "${trackId}" is missing`)
        return track
      })

      episode.value = {
        ...episode.value,
        revision: episode.value.revision + 1,
        tracks,
      }
      return
    }

    const removed = direction === 'redo' ? patch.before : patch.after
    const inserted = direction === 'redo' ? patch.after : patch.before
    const affectedIds = new Set([...patch.before, ...patch.after].map(segment => segment.id))
    const anchor = episode.value.segments.findIndex(segment => removed.some(candidate => candidate.id === segment.id))
    const segments = episode.value.segments.filter(segment => !affectedIds.has(segment.id))
    segments.splice(anchor < 0 ? segments.length : anchor, 0, ...inserted)

    episode.value = {
      ...episode.value,
      revision: episode.value.revision + 1,
      segments,
    }
  }

  function commitPatch(patch: TimelinePatch): void {
    applyPatch(patch, 'redo')
    history.value = {
      redo: [],
      undo: [...history.value.undo, patch],
    }
  }

  function commitSegmentEdit(edit: CommitSegmentEdit): void {
    const segment = episode.value.segments.find(candidate => candidate.id === edit.segmentId)
    const targetTrack = episode.value.tracks.find(track => track.id === edit.trackId)
    const sourceTrack = episode.value.tracks.find(track => track.id === segment?.trackId)

    if (!segment || !targetTrack || !sourceTrack || targetTrack.kind !== sourceTrack.kind)
      return

    const startTick = Math.max(0, Math.round(edit.startTick))
    const endTick = Math.min(episode.value.durationTicks, Math.max(startTick + 1, Math.round(edit.endTick)))
    if (segment.startTick === startTick && segment.endTick === endTick && segment.trackId === edit.trackId)
      return

    commitPatch({
      after: [{ ...segment, endTick, startTick, trackId: edit.trackId }],
      before: [segment],
      type: 'segments',
    })
  }

  function cutSegment(segmentId: string, atTick: number): boolean {
    const segment = episode.value.segments.find(candidate => candidate.id === segmentId)
    const cutTick = Math.round(atTick)
    if (!segment || cutTick <= segment.startTick || cutTick >= segment.endTick)
      return false

    const rightSegmentId = `${segment.id}:cut:${episode.value.revision + 1}`
    commitPatch({
      after: [
        { ...segment, endTick: cutTick },
        { ...segment, id: rightSegmentId, startTick: cutTick },
      ],
      before: [segment],
      type: 'segments',
    })
    return true
  }

  function reorderTrack(sourceIndex: number, targetIndex: number): void {
    const trackCount = episode.value.tracks.length
    if (!Number.isInteger(sourceIndex)
      || !Number.isInteger(targetIndex)
      || sourceIndex < 0
      || sourceIndex >= trackCount
      || targetIndex < 0
      || targetIndex >= trackCount
      || sourceIndex === targetIndex) {
      return
    }

    const before = episode.value.tracks.map(track => track.id)
    const after = [...before]
    const moved = after.splice(sourceIndex, 1)[0]
    if (!moved) {
      return
    }

    after.splice(targetIndex, 0, moved)
    commitPatch({ after, before, type: 'track-order' })
  }

  function redo(): void {
    const patch = history.value.redo.at(-1)
    if (!patch)
      return

    applyPatch(patch, 'redo')
    history.value = {
      redo: history.value.redo.slice(0, -1),
      undo: [...history.value.undo, patch],
    }
  }

  function undo(): void {
    const patch = history.value.undo.at(-1)
    if (!patch)
      return

    applyPatch(patch, 'undo')
    history.value = {
      redo: [...history.value.redo, patch],
      undo: history.value.undo.slice(0, -1),
    }
  }

  return {
    canRedo,
    canUndo,
    commitSegmentEdit,
    cutSegment,
    episode,
    history,
    redo,
    reorderTrack,
    undo,
  }
})
