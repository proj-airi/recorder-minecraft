import type { VideoAnchors } from '../monitor/videoTime'
import type { ServerTickRange } from '../timeline/replay'

import { SERVER_TICK_RATE } from '../timeline/domain'

/**
 * Fallback without a timing file: the video starts at the Play's first tick and runs at the
 * Server tick rate. Callers must mark values derived from it as estimates.
 */
export function assumedServerTick(playStartServerTick: number, seconds: number): number {
  return playStartServerTick + Math.round(seconds * SERVER_TICK_RATE)
}

/**
 * Moves one edge of a range and keeps it valid: inside the Play and at least one tick long. The
 * other edge stays put unless the moved edge would cross it.
 */
export function moveRangeEdge(range: ServerTickRange, edge: 'end' | 'start', tick: number, play: ServerTickRange): ServerTickRange {
  const clamped = Math.min(play.endServerTick, Math.max(play.startServerTick, Math.round(tick)))
  if (edge === 'start')
    return { endServerTick: range.endServerTick, startServerTick: Math.min(clamped, range.endServerTick - 1) }
  return { endServerTick: Math.max(clamped, range.startServerTick + 1), startServerTick: range.startServerTick }
}

/**
 * Server tick shown at a video time: the last anchor at or before `seconds`. Inverse of
 * `videoPositionForServerTick`, used to place the preview's playhead while the video plays.
 */
export function serverTickForVideoTime(anchors: VideoAnchors, seconds: number): null | number {
  const times = anchors.videoSeconds
  if (times.length === 0)
    return null
  let low = 0
  let high = times.length - 1
  let found = 0
  while (low <= high) {
    const middle = (low + high) >> 1
    if (times[middle]! <= seconds) {
      found = middle
      low = middle + 1
    }
    else {
      high = middle - 1
    }
  }
  return anchors.serverTicks[found]!
}
