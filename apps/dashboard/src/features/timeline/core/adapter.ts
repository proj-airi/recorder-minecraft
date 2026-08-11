import type { Clip, Track } from '@techsquidtv/canvas-timeline-core'

import type { CommitSegmentEdit, EpisodeDraft, TimelineTrackKind } from '../domain'

import { TimelineEngine } from '@techsquidtv/canvas-timeline-core'

import { SERVER_TICK_RATE, TIMELINE_TRACK_HEIGHT } from '../domain'

export interface TickTime {
  r: number
  v: number
}

export function createTimelineEngine(episode: EpisodeDraft, selectedSegmentId: null | string, previous?: TimelineEngine, editable = true): TimelineEngine {
  const tracks: Track<TimelineTrackKind>[] = episode.tracks.map(track => ({
    clips: episode.segments
      .filter(segment => segment.trackId === track.id)
      .sort((left, right) => left.startTick - right.startTick)
      .map<Clip>(segment => ({
        color: segment.color,
        id: segment.id,
        label: segment.label,
        metadata: { episodeRevision: episode.revision },
        movable: editable && segment.editable,
        resizable: editable && segment.editable,
        selected: segment.id === selectedSegmentId,
        sourceId: segment.id,
        sourceStart: toTickTime(0),
        timelineEnd: toTickTime(segment.endTick),
        timelineStart: toTickTime(segment.startTick),
      })),
    collapsed: false,
    height: TIMELINE_TRACK_HEIGHT,
    id: track.id,
    kind: track.kind,
    locked: false,
    muted: false,
    name: track.label,
    selected: false,
    visible: true,
  }))

  return new TimelineEngine({
    duration: toTickTime(episode.durationTicks),
    playheadTime: previous?.playheadTime ?? toTickTime(0),
    scrollLeft: previous?.scrollLeft ?? 0,
    scrollTop: previous?.scrollTop ?? 0,
    snapEnabled: editable && (previous?.isSnappingEnabled ?? true),
    snapThresholdPixels: 8,
    tracks,
    zoomConstraints: { frameRate: SERVER_TICK_RATE, minZoomScale: 8 },
    zoomScale: previous?.zoomScale ?? 18,
  })
}

export function readSegmentEdit(engine: TimelineEngine, segmentId: string): CommitSegmentEdit | null {
  const result = engine.getClip(segmentId)
  if (!result)
    return null

  return {
    endTick: toServerTick(result.clip.timelineEnd),
    segmentId,
    startTick: toServerTick(result.clip.timelineStart),
    trackId: result.track.id,
  }
}

export function toServerTick(time: TickTime): number {
  return Math.round(time.v * SERVER_TICK_RATE / time.r)
}

export function toTickTime(tick: number): TickTime {
  return { r: SERVER_TICK_RATE, v: Math.round(tick) }
}
