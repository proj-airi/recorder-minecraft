import type { Ref } from 'vue'

import type { EpisodeDraft, EpisodePrimaryTrack, PlayPlacement } from '../timeline/domain'

import { shallowRef, watch } from 'vue'

import { SERVER_TICK_RATE } from '../timeline/domain'
import { placementContainsTick } from '../timeline/ticks'

/** Server tick of every rendered frame, ordered by frame ordinal (frame 1 first). */
export interface FramesIndex {
  serverTicks: Float64Array
}

export interface MonitorLaneView {
  lane: EpisodePrimaryTrack
  placement: PlayPlacement
  state: 'active' | 'ended' | 'waiting'
}

/** One tile per player lane with video: the clip under the playhead, or the next or last clip. */
export function monitorLaneViews(episode: EpisodeDraft, episodeTick: number): MonitorLaneView[] {
  return episode.tracks.flatMap((track): MonitorLaneView[] => {
    if (track.role !== 'primary')
      return []
    const placements = episode.placements
      .filter(placement => placement.laneId === track.id && placement.source.videoUrl)
      .sort((left, right) => left.startTick - right.startTick)
    if (placements.length === 0)
      return []
    const active = placements.filter(placement => placementContainsTick(placement, episodeTick)).at(-1)
    if (active)
      return [{ lane: track, placement: active, state: 'active' }]
    const next = placements.find(placement => placement.startTick > episodeTick)
    if (next)
      return [{ lane: track, placement: next, state: 'waiting' }]
    return [{ lane: track, placement: placements.at(-1)!, state: 'ended' }]
  })
}

/** Parses `renders/fpv_frames/frames.jsonl` (`{"ordinal":"1","serverTick":"1422",...}` per line). */
export function parseFramesIndex(text: string): FramesIndex {
  const frames: { ordinal: number, serverTick: number }[] = []
  for (const line of text.split('\n')) {
    if (!line.trim())
      continue
    try {
      const record = JSON.parse(line) as { ordinal?: unknown, serverTick?: unknown }
      const ordinal = Number(record.ordinal)
      const serverTick = Number(record.serverTick)
      if (Number.isFinite(ordinal) && Number.isFinite(serverTick))
        frames.push({ ordinal, serverTick })
    }
    catch {
      // Skip a malformed line; the remaining frames still map time.
    }
  }
  frames.sort((left, right) => left.ordinal - right.ordinal)
  return { serverTicks: Float64Array.from(frames, frame => frame.serverTick) }
}

/**
 * Video time that shows a Server tick.
 *
 * With a frames index, the last frame captured at or before the tick is selected and the time
 * points to the middle of that frame. Without it, the video is assumed to start at the Play's
 * first Server tick and run at the Server tick rate.
 */
export function videoTimeForServerTick(
  placement: PlayPlacement,
  serverTick: number,
  framesIndex: FramesIndex | null,
  framesPerSecond = SERVER_TICK_RATE,
): number {
  if (!framesIndex || framesIndex.serverTicks.length === 0)
    return Math.max(0, (serverTick - placement.playStartServerTick) / SERVER_TICK_RATE)

  const ticks = framesIndex.serverTicks
  let low = 0
  let high = ticks.length - 1
  let found = -1
  while (low <= high) {
    const middle = (low + high) >> 1
    if (ticks[middle]! <= serverTick) {
      found = middle
      low = middle + 1
    }
    else {
      high = middle - 1
    }
  }
  const frame = Math.max(0, found)
  return (frame + 0.5) / framesPerSecond
}

const framesIndexCache = new Map<string, Promise<FramesIndex>>()

/** Loads and caches a frames index; the value stays null while loading, on error, or without a URL. */
export function useFramesIndex(url: Readonly<Ref<string | undefined>>): Readonly<Ref<FramesIndex | null>> {
  const index = shallowRef<FramesIndex | null>(null)
  watch(url, (current, _previous, onCleanup) => {
    index.value = null
    if (!current)
      return
    let cancelled = false
    onCleanup(() => {
      cancelled = true
    })
    let pending = framesIndexCache.get(current)
    if (!pending) {
      pending = fetch(new URL(current, window.location.href)).then(async (response) => {
        if (!response.ok)
          throw new Error(`Frames index request failed with HTTP ${response.status}.`)
        return parseFramesIndex(await response.text())
      })
      pending.catch(() => framesIndexCache.delete(current))
      framesIndexCache.set(current, pending)
    }
    pending.then((value) => {
      if (!cancelled)
        index.value = value
    }, () => undefined)
  }, { immediate: true })
  return index
}
