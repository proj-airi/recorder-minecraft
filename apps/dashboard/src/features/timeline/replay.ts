import type { RecorderMinecraftApiV1Replay } from '@proj-airi/recorder-minecraft-api'

import type { EpisodeDraft, EpisodeSegment, EpisodeTrack } from './domain'

import { SERVER_TICK_RATE } from './domain'

const replayColors = ['#31b899', '#3d8bd9', '#7d6ee7', '#df6b63', '#d89b45', '#bd718a']

/** Adds a replay as one immutable, time-aligned camera track. */
export function addReplayToEpisode(episode: EpisodeDraft, replay: RecorderMinecraftApiV1Replay): EpisodeDraft | null {
  const connectionId = replay.connectionId
  const videoUrl = replay.video?.url
  const eventsUrl = replay.eventsUrl
  if (!connectionId || (!videoUrl && !eventsUrl) || replay.validationError || episode.tracks.some(track => track.replay?.connectionId === connectionId))
    return null

  const durationTicks = replayDurationTicks(replay)
  if (durationTicks <= 0)
    return null

  const parsedStart = replay.startedAt ? Date.parse(replay.startedAt) : Number.NaN
  const startTimeMs = Number.isFinite(parsedStart) ? parsedStart : undefined
  const previousOrigin = episode.originTimeMs
  const originTimeMs = previousOrigin === undefined ? startTimeMs : startTimeMs === undefined ? previousOrigin : Math.min(previousOrigin, startTimeMs)
  const originShiftTicks = previousOrigin !== undefined && originTimeMs !== undefined && originTimeMs < previousOrigin
    ? Math.round((previousOrigin - originTimeMs) * SERVER_TICK_RATE / 1_000)
    : 0
  const shiftedSegments = originShiftTicks === 0
    ? episode.segments
    : episode.segments.map(segment => ({
        ...segment,
        endTick: segment.endTick + originShiftTicks,
        startTick: segment.startTick + originShiftTicks,
      }))
  const startTick = startTimeMs !== undefined && originTimeMs !== undefined
    ? Math.max(0, Math.round((startTimeMs - originTimeMs) * SERVER_TICK_RATE / 1_000))
    : shiftedSegments.reduce((latest, segment) => Math.max(latest, segment.endTick), 0)
  const endTick = startTick + durationTicks
  const trackId = `replay:${connectionId}`
  const label = [replay.playerName, replay.serverName].filter(Boolean).join(' · ') || 'Replay'
  const track: EpisodeTrack = {
    id: trackId,
    kind: videoUrl ? 'video' : 'data',
    label,
    replay: {
      connectionId,
      eventsUrl,
      playerName: replay.playerName ?? 'Unknown player',
      serverName: replay.serverName ?? 'Unknown server',
      startedAt: replay.startedAt,
      startServerTick: replay.startServerTick,
      videoUrl,
    },
  }
  const segment: EpisodeSegment = {
    color: replayColors[stableColorIndex(connectionId)]!,
    endTick,
    id: `clip:${connectionId}`,
    label,
    startTick,
    trackId,
  }
  const segments = [...shiftedSegments, segment]

  return {
    ...episode,
    durationTicks: segments.reduce((latest, candidate) => Math.max(latest, candidate.endTick), 0),
    originTimeMs,
    revision: episode.revision + 1,
    segments,
    tracks: [...episode.tracks, track],
  }
}

export function createEmptyEpisode(): EpisodeDraft {
  return {
    durationTicks: 0,
    id: 'dataset-timeline',
    revision: 1,
    segments: [],
    title: 'Dataset timeline',
    tracks: [],
  }
}

export function replayDurationTicks(replay: null | RecorderMinecraftApiV1Replay): number {
  if (!replay)
    return 0

  const startTick = Number(replay.startServerTick)
  const endTick = Number(replay.endServerTick)
  if (Number.isFinite(startTick) && Number.isFinite(endTick) && endTick >= startTick)
    return Math.round(endTick - startTick)

  const startedAt = replay.startedAt ? Date.parse(replay.startedAt) : Number.NaN
  const endedAt = replay.endedAt ? Date.parse(replay.endedAt) : Number.NaN
  if (Number.isFinite(startedAt) && Number.isFinite(endedAt) && endedAt >= startedAt)
    return Math.round((endedAt - startedAt) * SERVER_TICK_RATE / 1_000)

  return 0
}

function stableColorIndex(value: string): number {
  let hash = 0
  for (const character of value)
    hash = (hash * 31 + character.charCodeAt(0)) >>> 0
  return hash % replayColors.length
}
