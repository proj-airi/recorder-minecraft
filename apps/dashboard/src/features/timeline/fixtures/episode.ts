import type { EpisodeDraft, EpisodeSession, PlayPlacement } from '../domain'

import { SERVER_TICK_RATE } from '../domain'
import { createEmptyEpisode, finishEpisode, laneIdFor } from '../replay'

export interface EpisodeOptions {
  clipsPerTrack: number
  durationTicks: number
  id: string
  title: string
  trackCount: number
}

export const stressOptions = {
  clipsPerTrack: 25,
  durationTicks: SERVER_TICK_RATE * 60 * 20,
  id: 'timeline-stress',
  title: 'Timeline stress test · 200 tracks',
  trackCount: 200,
} satisfies EpisodeOptions

/**
 * Creates deterministic timeline data without depending on Vue, Pinia, or browser globals: one
 * session with `trackCount` player lanes, each hosting `clipsPerTrack` Play clips.
 */
export function createEpisode(options: EpisodeOptions): EpisodeDraft {
  const sessionKey = 'session:fixture'
  const slotTicks = Math.floor(options.durationTicks / options.clipsPerTrack)
  const playerKeys = Array.from({ length: options.trackCount }, (_, index) => `name:Video ${index + 1}`)
  const placements = playerKeys.flatMap((playerKey, trackIndex) => Array.from(
    { length: options.clipsPerTrack },
    (_, clipIndex): PlayPlacement => {
      // Each clip stays inside its time slot, while deterministic offsets and durations prevent
      // large fixtures from degenerating into visually identical rows.
      const offsetTicks = (trackIndex * 37 + clipIndex * 53) % 180
      const durationTicks = 240 + (trackIndex * 29 + clipIndex * 71) % 480
      const startTick = clipIndex * slotTicks + offsetTicks
      const endTick = Math.min(options.durationTicks, startTick + durationTicks)
      const connectionId = `fixture-${trackIndex + 1}-${clipIndex + 1}`
      const playerName = `Video ${trackIndex + 1}`
      return {
        connectionId,
        endTick,
        id: `play:${connectionId}`,
        laneId: laneIdFor(sessionKey, playerKey),
        playEndServerTick: endTick,
        playerKey,
        playerName,
        playStartServerTick: startTick,
        sessionId: 'fixture',
        sessionKey,
        source: { connectionId, playerName, serverName: `Clip ${clipIndex + 1}`, sessionId: 'fixture' },
        sourceEndServerTick: endTick,
        sourceStartServerTick: startTick,
        startTick,
      }
    },
  ))
  const session: EpisodeSession = {
    anchorServerTick: 0,
    anchorTick: 0,
    key: sessionKey,
    label: options.title,
    placement: 'origin',
    playerOrder: playerKeys,
    sessionId: 'fixture',
  }
  const episode = finishEpisode({ ...createEmptyEpisode(), id: options.id, revision: 0, title: options.title }, placements, [session], undefined)
  return { ...episode, durationTicks: Math.max(episode.durationTicks, options.durationTicks) }
}
