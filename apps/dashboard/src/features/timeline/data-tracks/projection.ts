import type { TimelineDataLane } from '@proj-airi/canvas-timeline-renderer'

import type { EpisodeDraft, EpisodeSession, PlayPlacement } from '../domain'
import type { SelectedTimelineDataItem, TimelineDataItem, TimelineDataTrack } from './types'

import { DATA_LANE_INTERVAL, DATA_LANE_POINT, lowerBound } from '@proj-airi/canvas-timeline-renderer'

import { SERVER_TICK_RATE } from '../domain'
import { placementContainsServerTick, serverTickToEpisodeTick } from '../ticks'

const DEFAULT_ITEM_COLOR = '#38bdf8'

/** Renderer lane plus the source item index of every entry, kept on the main thread for hit tests. */
export interface ProjectedDataLane extends TimelineDataLane {
  /** `items[itemIndexes[i]]` is the source of entry `i`. */
  itemIndexes: Int32Array
  /** Placement that shows entry `i`, or null for session-scoped tracks. */
  placementIds: (null | string)[]
}

interface LaneEntry {
  end: number
  itemIndex: number
  placementId: null | string
  start: number
}

/**
 * Entry active at an episode tick: intervals cover `[start, end)`, points cover `[tick, tick + 1)`.
 * The latest start wins. Returns -1 when nothing is active.
 */
export function dataLaneEntryAt(lane: ProjectedDataLane, tick: number): number {
  const begin = lowerBound(lane.starts, tick - lane.maxSpan - 1)
  const end = lowerBound(lane.starts, tick + 1e-9)
  let found = -1
  for (let index = begin; index < end; index += 1) {
    const stop = lane.kinds[index] === DATA_LANE_POINT ? lane.starts[index]! + 1 : lane.ends[index]!
    if (tick >= lane.starts[index]! && tick < stop)
      found = index
  }
  return found
}

/**
 * Lane entry under a pointer at `tick`: the nearest point within `toleranceTicks`, otherwise the
 * interval with the latest start that contains the tick. Returns -1 when nothing is hit.
 */
export function hitTestDataLane(lane: ProjectedDataLane, tick: number, toleranceTicks: number): number {
  const begin = lowerBound(lane.starts, tick - lane.maxSpan - toleranceTicks)
  const end = lowerBound(lane.starts, tick + toleranceTicks + 1e-9)
  let bestPoint = -1
  let bestDistance = Number.POSITIVE_INFINITY
  let bestInterval = -1
  for (let index = begin; index < end; index += 1) {
    if (lane.kinds[index] === DATA_LANE_POINT) {
      const distance = Math.abs(lane.starts[index]! - tick)
      if (distance <= toleranceTicks && distance < bestDistance) {
        bestDistance = distance
        bestPoint = index
      }
    }
    else if (tick >= lane.starts[index]! && tick <= lane.ends[index]!) {
      bestInterval = index
    }
  }
  return bestPoint >= 0 ? bestPoint : bestInterval
}

export function itemServerRange(item: TimelineDataItem): [number, number] {
  return item.kind === 'point' ? [item.serverTick, item.serverTick] : [item.startServerTick, Math.max(item.startServerTick, item.endServerTick)]
}

/**
 * Maps a data track's Server-tick items to episode ticks.
 *
 * Player-scoped items go through the player's clips: points outside every trimmed source range
 * are hidden, and intervals are split at clip boundaries. Session-scoped items go through the
 * session anchor.
 */
export function projectDataTrack(episode: EpisodeDraft, track: TimelineDataTrack): ProjectedDataLane {
  const entries: LaneEntry[] = []
  const target = track.target
  if (target.scope === 'player') {
    const placements = episode.placements.filter(placement => placement.laneId === target.laneId)
    track.items.forEach((item, itemIndex) => {
      for (const placement of placements) {
        if (item.connectionId && item.connectionId !== placement.connectionId)
          continue
        const entry = projectThroughPlacement(item, placement)
        if (entry)
          entries.push({ ...entry, itemIndex, placementId: placement.id })
      }
    })
  }
  else {
    const session = episode.sessions.find(candidate => candidate.key === target.sessionKey)
    if (session) {
      track.items.forEach((item, itemIndex) => {
        const [start, end] = itemServerRange(item)
        entries.push({
          end: serverTickToEpisodeTick(session, end),
          itemIndex,
          placementId: null,
          start: serverTickToEpisodeTick(session, start),
        })
      })
    }
  }

  entries.sort((left, right) => left.start - right.start || left.end - right.end)
  const starts = new Float64Array(entries.length)
  const ends = new Float64Array(entries.length)
  const kinds = new Uint8Array(entries.length)
  const itemIndexes = new Int32Array(entries.length)
  const colors: string[] = []
  const labels: string[] = []
  const placementIds: (null | string)[] = []
  let maxSpan = 0
  entries.forEach((entry, index) => {
    const item = track.items[entry.itemIndex]!
    starts[index] = entry.start
    ends[index] = entry.end
    kinds[index] = item.kind === 'point' ? DATA_LANE_POINT : DATA_LANE_INTERVAL
    itemIndexes[index] = entry.itemIndex
    colors.push(item.color ?? track.color ?? DEFAULT_ITEM_COLOR)
    labels.push(item.label ?? '')
    placementIds.push(entry.placementId)
    maxSpan = Math.max(maxSpan, entry.end - entry.start)
  })

  return {
    colors,
    ends,
    itemIndexes,
    kinds,
    labels,
    maxSpan,
    message: laneMessage(track, entries.length),
    messageColor: track.status === 'error' ? 'rgba(248, 113, 113, 0.85)' : undefined,
    placementIds,
    starts,
    ticksPerSecond: SERVER_TICK_RATE,
    trackId: track.id,
  }
}

/** Resolves a lane entry (or the first entry of an item) to the public selection shape. */
export function resolveDataItem(
  episode: EpisodeDraft,
  track: TimelineDataTrack,
  lane: ProjectedDataLane | undefined,
  item: TimelineDataItem,
  entryIndex = -1,
): SelectedTimelineDataItem {
  const itemIndex = track.items.indexOf(item)
  let entry = entryIndex
  if (entry < 0 && lane) {
    for (let index = 0; index < lane.itemIndexes.length; index += 1) {
      if (lane.itemIndexes[index] === itemIndex) {
        entry = index
        break
      }
    }
  }
  const placementId = entry >= 0 && lane ? lane.placementIds[entry] : null
  const placement: null | PlayPlacement = placementId ? episode.placements.find(candidate => candidate.id === placementId) ?? null : null
  const session: EpisodeSession | null = episode.sessions.find(candidate => candidate.key === track.target.sessionKey) ?? null
  return {
    episodeTick: entry >= 0 && lane ? lane.starts[entry]! : null,
    item,
    placement,
    serverTick: itemServerRange(item)[0],
    session,
    track,
  }
}

function laneMessage(track: TimelineDataTrack, entryCount: number): string | undefined {
  if (entryCount > 0)
    return undefined
  switch (track.status) {
    case 'empty':
      return 'No items'
    case 'error':
      return `Failed to load: ${track.error ?? 'unknown error'}`
    case 'idle':
    case 'loading':
      return 'Loading…'
    case 'ready':
      return 'No items inside the visible clips'
  }
}

function projectThroughPlacement(item: TimelineDataItem, placement: PlayPlacement): null | { end: number, start: number } {
  if (item.kind === 'point') {
    // Half-open at a cut so a point on the boundary is drawn once; the Play's last tick stays visible.
    const atCutEnd = item.serverTick === placement.sourceEndServerTick && placement.sourceEndServerTick < placement.playEndServerTick
    if (!placementContainsServerTick(placement, item.serverTick) || atCutEnd)
      return null
    const tick = serverTickToEpisodeTick(placement, item.serverTick)
    return { end: tick, start: tick }
  }
  const [start, end] = itemServerRange(item)
  const clippedStart = Math.max(start, placement.sourceStartServerTick)
  const clippedEnd = Math.min(end, placement.sourceEndServerTick)
  if (clippedEnd < clippedStart)
    return null
  return {
    end: serverTickToEpisodeTick(placement, clippedEnd),
    start: serverTickToEpisodeTick(placement, clippedStart),
  }
}
