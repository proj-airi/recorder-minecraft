import type { Clip, Track } from '@techsquidtv/canvas-timeline-core'

import type { CommitSegmentEdit, EpisodeDraft, EpisodeSegment, TimelineTrackKind } from '../domain'
import type { TimelineLayout } from '../layout'

import { TimelineEngine } from '@techsquidtv/canvas-timeline-core'

import { SERVER_TICK_RATE } from '../domain'
import { buildTimelineLayout } from '../layout'

export interface CreateTimelineEngineOptions {
  editable?: boolean
  /** Visible rows; defaults to the draft rows without data tracks. */
  layout?: TimelineLayout
  previous?: TimelineEngine
  selectedSegmentId?: null | string
}

export interface TickTime {
  r: number
  v: number
}

export function createTimelineEngine(episode: EpisodeDraft, options: CreateTimelineEngineOptions = {}): TimelineEngine {
  const { editable = true, previous, selectedSegmentId = null } = options
  const layout = options.layout ?? buildTimelineLayout(episode, [], new Set())
  const segmentsByTrack = new Map<string, EpisodeSegment[]>()
  for (const segment of episode.segments) {
    const list = segmentsByTrack.get(segment.trackId) ?? []
    list.push(segment)
    segmentsByTrack.set(segment.trackId, list)
  }
  const tracks: Track<TimelineTrackKind>[] = layout.rows.map(row => ({
    clips: (segmentsByTrack.get(row.id) ?? [])
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
    height: row.height,
    id: row.id,
    kind: row.kind,
    locked: false,
    muted: false,
    name: row.label,
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
