import type { TimelineDataTrack } from './data-tracks/types'
import type { EpisodeDraft, EpisodePrimaryTrack, EpisodeSessionTrack, EpisodeTrack, EpisodeWorldTrack, TimelineTrackKind } from './domain'

import {
  TIMELINE_DATA_ROW_HEIGHT,
  TIMELINE_SESSION_ROW_HEIGHT,
  TIMELINE_TRACK_HEIGHT,
  TIMELINE_WORLD_ROW_HEIGHT,
} from './domain'

export interface TimelineDataRow extends TimelineLayoutRowBase {
  dataTrack: TimelineDataTrack
  role: 'data'
}

export interface TimelineLayout {
  rows: TimelineLayoutRow[]
  totalHeight: number
}

export type TimelineLayoutRow = TimelineDataRow | TimelinePlayerRow | TimelineSessionRow | TimelineWorldRow

/** Player lane. `groupId` collapses its data tracks. */
export interface TimelinePlayerRow extends TimelineLayoutRowBase {
  collapsed: boolean
  dataTrackCount: number
  groupId: string
  playCount: number
  role: 'primary'
  track: EpisodePrimaryTrack
}

/** Session header. `groupId` collapses the whole session. */
export interface TimelineSessionRow extends TimelineLayoutRowBase {
  collapsed: boolean
  groupId: string
  playerCount: number
  role: 'session'
  track: EpisodeSessionTrack
}

export interface TimelineWorldRow extends TimelineLayoutRowBase {
  role: 'world'
  track: EpisodeWorldTrack
}

interface TimelineLayoutRowBase {
  height: number
  /** Row id; equal to the engine track id. */
  id: string
  kind: TimelineTrackKind
  label: string
  sessionKey: string
  /** Content offset from the top of the first row (the ruler is not included). */
  top: number
}

/**
 * Visible rows in display order: each session header, its world slot and session data tracks,
 * then each player lane followed by that player's data tracks. Collapsed groups hide children.
 */
export function buildTimelineLayout(
  episode: EpisodeDraft,
  dataTracks: readonly TimelineDataTrack[],
  collapsedGroupIds: ReadonlySet<string>,
): TimelineLayout {
  const dataByTarget = new Map<string, TimelineDataTrack[]>()
  for (const track of dataTracks) {
    const list = dataByTarget.get(track.target.key) ?? []
    list.push(track)
    dataByTarget.set(track.target.key, list)
  }
  dataByTarget.forEach(list => list.sort((left, right) => left.order - right.order || left.label.localeCompare(right.label)))

  const rows: TimelineLayoutRow[] = []
  let top = 0
  const push = (row: TimelineLayoutRow) => {
    rows.push(row)
    top += row.height
  }
  const pushData = (sessionKey: string, targetKey: string) => {
    for (const dataTrack of dataByTarget.get(targetKey) ?? []) {
      push({ dataTrack, height: TIMELINE_DATA_ROW_HEIGHT, id: dataTrack.id, kind: 'markers', label: dataTrack.label, role: 'data', sessionKey, top })
    }
  }
  const playCounts = new Map<string, number>()
  for (const placement of uniqueByConnection(episode))
    playCounts.set(placement.laneId, (playCounts.get(placement.laneId) ?? 0) + 1)

  let currentSessionCollapsed = false
  for (const track of episode.tracks as EpisodeTrack[]) {
    if (track.role === 'session') {
      currentSessionCollapsed = collapsedGroupIds.has(track.sessionKey)
      const playerCount = episode.tracks.filter(candidate => candidate.role === 'primary' && candidate.sessionKey === track.sessionKey).length
      push({ collapsed: currentSessionCollapsed, groupId: track.sessionKey, height: TIMELINE_SESSION_ROW_HEIGHT, id: track.id, kind: track.kind, label: track.label, playerCount, role: 'session', sessionKey: track.sessionKey, top, track })
      continue
    }
    if (currentSessionCollapsed)
      continue
    if (track.role === 'world') {
      push({ height: TIMELINE_WORLD_ROW_HEIGHT, id: track.id, kind: track.kind, label: track.label, role: 'world', sessionKey: track.sessionKey, top, track })
      pushData(track.sessionKey, track.sessionKey)
      continue
    }
    const collapsed = collapsedGroupIds.has(track.id)
    push({
      collapsed,
      dataTrackCount: dataByTarget.get(track.id)?.length ?? 0,
      groupId: track.id,
      height: TIMELINE_TRACK_HEIGHT,
      id: track.id,
      kind: track.kind,
      label: track.label,
      playCount: playCounts.get(track.id) ?? 0,
      role: 'primary',
      sessionKey: track.sessionKey,
      top,
      track,
    })
    if (!collapsed)
      pushData(track.sessionKey, track.id)
  }
  return { rows, totalHeight: top }
}

/** Row at a content offset (ruler excluded), by binary search. */
export function rowAtOffset(layout: TimelineLayout, offset: number): null | TimelineLayoutRow {
  let low = 0
  let high = layout.rows.length - 1
  while (low <= high) {
    const middle = (low + high) >> 1
    const row = layout.rows[middle]!
    if (offset < row.top)
      high = middle - 1
    else if (offset >= row.top + row.height)
      low = middle + 1
    else
      return row
  }
  return null
}

function uniqueByConnection(episode: EpisodeDraft) {
  const seen = new Set<string>()
  return episode.placements.filter((placement) => {
    if (seen.has(placement.connectionId))
      return false
    seen.add(placement.connectionId)
    return true
  })
}
