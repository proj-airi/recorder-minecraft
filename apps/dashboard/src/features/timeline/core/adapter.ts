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

  const engine = new TimelineEngine({
    duration: toTickTime(episode.durationTicks),
    playheadTime: previous?.playheadTime ?? toTickTime(0),
    scrollLeft: previous?.scrollLeft ?? 0,
    scrollTop: previous?.scrollTop ?? 0,
    snapEnabled: editable && (previous?.isSnappingEnabled ?? true),
    snapThresholdPixels: 8,
    tracks,
    // NOTICE: The engine already floors zoom at "content fills the viewport". The previous 8 px/s
    // floor sat above that for any session longer than about viewport/8 seconds (1:44 at 830px),
    // so an 8-minute session could never be seen whole. Keep only a tiny positive floor, which
    // the engine requires, and let the content-fit floor decide.
    zoomConstraints: { frameRate: SERVER_TICK_RATE, minZoomScale: 0.001 },
    zoomScale: previous?.zoomScale ?? 18,
  })

  // NOTICE: The constructor clamps zoom and scroll against a 1000px default viewport because it
  // takes no viewport size; the canvas only re-measures a new engine later. Carry the measured size
  // over and re-apply the previous view, otherwise a fitted or zoomed-out timeline snapped back to
  // about viewport/1000 of the session after every edit or data-track load.
  const viewport = previous?.getState()
  if (previous && viewport?.viewportWidth) {
    engine.setViewportWidth(viewport.viewportWidth)
    if (viewport.viewportHeight)
      engine.setViewportHeight(viewport.viewportHeight)
    engine.setZoomScale(previous.zoomScale)
    engine.setScrollLeft(previous.scrollLeft)
  }
  return engine
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
