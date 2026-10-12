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
 * Applies slider positions to a range. Only the edge whose step changed moves, so dragging one
 * thumb never rounds the other edge away from an exact tick. The last step lands on the Play end
 * even when the Play is not a whole number of steps long, so "the whole Play" stays reachable.
 */
export function rangeFromSteps(range: ServerTickRange, steps: [number, number], play: ServerTickRange, stepTicks: number): ServerTickRange {
  const current = rangeToSteps(range, play, stepTicks)
  const tickOf = (step: number) => Math.min(play.endServerTick, play.startServerTick + step * stepTicks)
  return {
    endServerTick: steps[1] === current[1] ? range.endServerTick : tickOf(steps[1]),
    startServerTick: steps[0] === current[0] ? range.startServerTick : tickOf(steps[0]),
  }
}

/**
 * Slider positions of a range, counted in `stepTicks` from the Play start. The start rounds down
 * and the end up, so a range set to exact ticks (I/O at the playhead) still shows inside its band.
 */
export function rangeToSteps(range: ServerTickRange, play: ServerTickRange, stepTicks: number): [number, number] {
  return [
    Math.floor((range.startServerTick - play.startServerTick) / stepTicks),
    Math.ceil((range.endServerTick - play.startServerTick) / stepTicks),
  ]
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
