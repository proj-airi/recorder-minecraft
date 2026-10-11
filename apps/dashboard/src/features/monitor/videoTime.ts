import type { Ref } from 'vue'

import type { EpisodeDraft, EpisodePrimaryTrack, EpisodeReplaySource, EpisodeVideoTiming, PlayPlacement } from '../timeline/domain'

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

/**
 * Parallel arrays of `{serverTick, videoSeconds}` anchors, ordered by video time with
 * non-decreasing Server ticks. Until the next anchor, the video shows the image captured at the
 * anchor's tick.
 */
export interface VideoAnchors {
  /** Encoded frame rate of the video, when the timing file states it. */
  framesPerSecond?: number
  serverTicks: Float64Array
  videoSeconds: Float64Array
}

/**
 * Where the video should be for one Server tick.
 *
 * `time` is the seek target. Any video time in `[windowStart, windowEnd]` shows the same image, so
 * a playing video inside that window needs no seek. `hold` is set when the tick is outside the
 * mapped range and the first or last image is held.
 */
export interface VideoPosition {
  hold?: 'after' | 'before'
  time: number
  windowEnd: number
  windowStart: number
}

export type VideoTimingState
  = | { anchors: VideoAnchors, status: 'ready', timing: EpisodeVideoTiming }
    | { message: string, status: 'error', timing: EpisodeVideoTiming }
    | { status: 'loading', timing: EpisodeVideoTiming }
    | { status: 'unknown' }

type TimingFile = FramesIndex | VideoAnchors

/**
 * Fallback position without timing: assume the video starts at the Play's first Server tick and
 * runs at the Server tick rate. Only approximate; a capture that holds or skips images drifts.
 */
export function assumedVideoPosition(placement: PlayPlacement, serverTick: number): VideoPosition {
  const time = Math.max(0, (serverTick - placement.playStartServerTick) / SERVER_TICK_RATE)
  return { time, windowEnd: time, windowStart: time }
}

/** A frames index as anchors: frame N (from 1) starts at (N - 1) / framesPerSecond. */
export function frameIndexAnchors(index: FramesIndex, framesPerSecond: number): VideoAnchors {
  return {
    framesPerSecond,
    serverTicks: index.serverTicks,
    videoSeconds: Float64Array.from(index.serverTicks, (_tick, frame) => frame / framesPerSecond),
  }
}

/** One tile per player lane: the clip under the playhead, or the next or last clip. */
export function monitorLaneViews(episode: EpisodeDraft, episodeTick: number): MonitorLaneView[] {
  return episode.tracks.flatMap((track): MonitorLaneView[] => {
    if (track.role !== 'primary')
      return []
    // Plays without video still get a tile, so the lane says why nothing plays.
    const placements = episode.placements
      .filter(placement => placement.laneId === track.id)
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

/**
 * Parses `renders/fpv.json` (`FpvVideoManifest` ProtoJSON).
 *
 * ProtoJSON omits zero values and writes int64 as strings, so `{"videoSeconds":0,"serverTick":"22"}`
 * and `{"serverTick":"22"}` are the same anchor, and a missing `serverTick` is tick 0.
 */
export function parseFpvManifest(text: string): VideoAnchors {
  const manifest = JSON.parse(text) as { frames?: unknown, framesPerSecond?: unknown }
  const frames = Array.isArray(manifest.frames) ? manifest.frames as { serverTick?: unknown, videoSeconds?: unknown }[] : []
  const anchors: { serverTick: number, videoSeconds: number }[] = []
  for (const frame of frames) {
    const serverTick = Number(frame?.serverTick ?? 0)
    const videoSeconds = Number(frame?.videoSeconds ?? 0)
    if (Number.isFinite(serverTick) && Number.isFinite(videoSeconds))
      anchors.push({ serverTick, videoSeconds })
  }
  // The contract orders anchors already; a stable sort keeps a misordered file usable.
  anchors.sort((left, right) => left.videoSeconds - right.videoSeconds)
  const framesPerSecond = Number(manifest.framesPerSecond ?? 0)
  return {
    framesPerSecond: framesPerSecond > 0 ? framesPerSecond : undefined,
    serverTicks: Float64Array.from(anchors, anchor => anchor.serverTick),
    videoSeconds: Float64Array.from(anchors, anchor => anchor.videoSeconds),
  }
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
 * Video position that shows a Server tick: the last anchor with `serverTick <= tick`.
 *
 * The seek target is half a frame past the anchor, so the decoder lands inside that frame instead
 * of on the boundary with the previous one. Before the first anchor the first image is held; after
 * the last anchor the last image is held.
 */
export function videoPositionForServerTick(anchors: VideoAnchors, serverTick: number, fallbackFramesPerSecond = SERVER_TICK_RATE): null | VideoPosition {
  const ticks = anchors.serverTicks
  const seconds = anchors.videoSeconds
  if (ticks.length === 0)
    return null
  const framesPerSecond = anchors.framesPerSecond ?? fallbackFramesPerSecond
  const frameSeconds = 1 / framesPerSecond

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

  const anchor = Math.max(0, found)
  const windowStart = seconds[anchor]!
  const time = windowStart + 0.5 * frameSeconds
  if (found < 0)
    return { hold: 'before', time, windowEnd: windowStart + frameSeconds, windowStart }
  if (anchor === ticks.length - 1) {
    const hold = serverTick > ticks[anchor]! ? 'after' : undefined
    return { hold, time, windowEnd: windowStart + frameSeconds, windowStart }
  }
  // Every video time up to the next anchor shows this anchor's image, including a long hold such
  // as the Airicraft empty-host pause, so playback inside it advances without seeking.
  return { time, windowEnd: Math.max(windowStart + frameSeconds, seconds[anchor + 1]!), windowStart }
}

const timingCache = new Map<string, Promise<TimingFile>>()

/** Loads and caches the video timing of a Play; the state follows the source's timing URL. */
export function useVideoTiming(source: Readonly<Ref<EpisodeReplaySource>>): Readonly<Ref<VideoTimingState>> {
  const state = shallowRef<VideoTimingState>({ status: 'unknown' })
  watch(
    [() => source.value.videoTiming, () => source.value.videoFramesPerSecond],
    ([timing, videoFramesPerSecond], _previous, onCleanup) => {
      if (!timing) {
        state.value = { status: 'unknown' }
        return
      }
      state.value = { status: 'loading', timing }
      let cancelled = false
      onCleanup(() => {
        cancelled = true
      })
      loadTimingFile(timing).then((file) => {
        if (cancelled)
          return
        const anchors = 'videoSeconds' in file
          ? file
          : frameIndexAnchors(file, videoFramesPerSecond ?? SERVER_TICK_RATE)
        state.value = { anchors, status: 'ready', timing }
      }, (error: unknown) => {
        if (!cancelled)
          state.value = { message: String(error), status: 'error', timing }
      })
    },
    { immediate: true },
  )
  return state
}

function loadTimingFile(timing: EpisodeVideoTiming): Promise<TimingFile> {
  const key = `${timing.format}:${timing.url}`
  let pending = timingCache.get(key)
  if (!pending) {
    pending = fetch(new URL(timing.url, window.location.href)).then(async (response) => {
      if (!response.ok)
        throw new Error(`Video timing request failed with HTTP ${response.status}.`)
      const text = await response.text()
      return timing.format === 'fpv-manifest' ? parseFpvManifest(text) : parseFramesIndex(text)
    })
    // A failed request is retried by the next tile that asks for it.
    pending.catch(() => timingCache.delete(key))
    timingCache.set(key, pending)
  }
  return pending
}
