import type { RecorderMinecraftApiV1Replay } from '@proj-airi/recorder-minecraft-api'

import type { ExtensionTrackProjection } from '../extensions/domain'
import type { EpisodeDraft, EpisodeSegment, EpisodeTrack, PlayPlacement } from './domain'

import { SERVER_TICK_RATE } from './domain'

const replayColors = ['#31b899', '#3d8bd9', '#7d6ee7', '#df6b63', '#d89b45', '#bd718a']

/** Adds one Play placement and all supported extension tracks. */
export function addReplayToEpisode(
  episode: EpisodeDraft,
  replay: RecorderMinecraftApiV1Replay,
  extensions: ExtensionTrackProjection[] = [],
): EpisodeDraft | null {
  const connectionId = replay.connectionId
  const videoUrl = replay.video?.url
  const eventsUrl = replay.eventsUrl
  if (!connectionId || (!videoUrl && !eventsUrl) || replay.validationError || episode.placements.some(placement => placement.connectionId === connectionId))
    return null

  const sourceStartServerTick = Number(replay.startServerTick)
  const sourceEndServerTick = Number(replay.endServerTick)
  const durationTicks = replayDurationTicks(replay)
  if (!Number.isSafeInteger(sourceStartServerTick) || !Number.isSafeInteger(sourceEndServerTick) || durationTicks <= 0)
    return null

  const parsedStart = replay.startedAt ? Date.parse(replay.startedAt) : Number.NaN
  const startTimeMs = Number.isFinite(parsedStart) ? parsedStart : undefined
  const previousOrigin = episode.originTimeMs
  const originTimeMs = previousOrigin === undefined ? startTimeMs : startTimeMs === undefined ? previousOrigin : Math.min(previousOrigin, startTimeMs)
  const originShiftTicks = previousOrigin !== undefined && originTimeMs !== undefined && originTimeMs < previousOrigin
    ? Math.round((previousOrigin - originTimeMs) * SERVER_TICK_RATE / 1_000)
    : 0
  const shiftedPlacements = originShiftTicks === 0
    ? episode.placements
    : episode.placements.map(placement => ({
        ...placement,
        endTick: placement.endTick + originShiftTicks,
        startTick: placement.startTick + originShiftTicks,
      }))
  const startTick = startTimeMs !== undefined && originTimeMs !== undefined
    ? Math.max(0, Math.round((startTimeMs - originTimeMs) * SERVER_TICK_RATE / 1_000))
    : shiftedPlacements.reduce((latest, placement) => Math.max(latest, placement.endTick), 0)
  const placementId = `play:${connectionId}`
  const label = [replay.playerName, replay.serverName].filter(Boolean).join(' · ') || 'Play'
  const placement: PlayPlacement = {
    connectionId,
    endTick: startTick + durationTicks,
    id: placementId,
    playEndServerTick: sourceEndServerTick,
    playStartServerTick: sourceStartServerTick,
    sourceEndServerTick,
    sourceStartServerTick,
    startTick,
  }
  const primaryTrack: EpisodeTrack = {
    id: `${placementId}:primary`,
    kind: videoUrl ? 'video' : 'data',
    label,
    placementId,
    replay: {
      connectionId,
      eventsUrl,
      playerName: replay.playerName ?? 'Unknown player',
      serverName: replay.serverName ?? 'Unknown server',
      startedAt: replay.startedAt,
      startServerTick: replay.startServerTick,
      videoUrl,
    },
    role: 'primary',
  }
  const extensionTracks: EpisodeTrack[] = extensions.map(extension => ({
    extension: {
      descriptor: extension.descriptor,
      items: extension.items,
    },
    id: `${placementId}:extension:${extension.descriptor.extensionType}`,
    kind: 'data',
    label: extension.label,
    placementId,
    role: 'extension',
  }))
  const placements = [...shiftedPlacements, placement]
  const tracks = [...episode.tracks, primaryTrack, ...extensionTracks]

  return finishEpisode(episode, placements, tracks, originTimeMs)
}

export function commitPlacementEdit(episode: EpisodeDraft, segmentId: string, startTick: number, endTick: number, trackId: string): EpisodeDraft | null {
  const segment = episode.segments.find(candidate => candidate.id === segmentId)
  const track = episode.tracks.find(candidate => candidate.id === trackId)
  const placement = episode.placements.find(candidate => candidate.id === segment?.placementId)
  if (!segment?.editable || !track || track.role !== 'primary' || track.placementId !== segment.placementId || !placement)
    return null

  const nextStartTick = Math.max(0, Math.round(startTick))
  const nextEndTick = Math.max(nextStartTick + 1, Math.round(endTick))
  if (placement.startTick === nextStartTick && placement.endTick === nextEndTick)
    return null

  const startDelta = nextStartTick - placement.startTick
  const endDelta = nextEndTick - placement.endTick
  const moved = startDelta === endDelta
  const sourceStartServerTick = moved
    ? placement.sourceStartServerTick
    : Math.min(placement.sourceEndServerTick - 1, Math.max(placement.playStartServerTick, placement.sourceStartServerTick + startDelta))
  const sourceEndServerTick = moved
    ? placement.sourceEndServerTick
    : Math.max(sourceStartServerTick + 1, Math.min(placement.playEndServerTick, placement.sourceEndServerTick + endDelta))
  const adjustedEndTick = moved ? nextEndTick : nextStartTick + sourceEndServerTick - sourceStartServerTick
  const placements = episode.placements.map(candidate => candidate.id === placement.id
    ? { ...candidate, endTick: adjustedEndTick, sourceEndServerTick, sourceStartServerTick, startTick: nextStartTick }
    : candidate)
  return finishEpisode(episode, placements, episode.tracks, episode.originTimeMs)
}

export function createEmptyEpisode(): EpisodeDraft {
  return {
    durationTicks: 0,
    id: 'dataset-timeline',
    placements: [],
    revision: 1,
    segments: [],
    title: 'Dataset timeline',
    tracks: [],
  }
}

export function cutPlacement(episode: EpisodeDraft, segmentId: string, atTick: number): EpisodeDraft | null {
  const segment = episode.segments.find(candidate => candidate.id === segmentId)
  const placement = episode.placements.find(candidate => candidate.id === segment?.placementId)
  const cutTick = Math.round(atTick)
  if (!segment?.editable || !placement || cutTick <= placement.startTick || cutTick >= placement.endTick)
    return null

  const sourceCutTick = placement.sourceStartServerTick + cutTick - placement.startTick
  const rightId = `${placement.id}:cut:${episode.revision + 1}`
  const left = { ...placement, endTick: cutTick, sourceEndServerTick: sourceCutTick }
  const right = { ...placement, id: rightId, sourceStartServerTick: sourceCutTick, startTick: cutTick }
  const placements = episode.placements.flatMap(candidate => candidate.id === placement.id ? [left, right] : [candidate])
  const placementTracks = episode.tracks.filter(track => track.placementId === placement.id)
  const clonedTracks = placementTracks
    .map(track => ({ ...track, id: track.id.replace(placement.id, rightId), placementId: rightId }))
  const placementTrackIndex = episode.tracks.findIndex(track => track.placementId === placement.id)
  const tracks = episode.tracks.filter(track => track.placementId !== placement.id)
  tracks.splice(Math.max(0, placementTrackIndex), 0, ...placementTracks, ...clonedTracks)
  return finishEpisode(episode, placements, tracks, episode.originTimeMs)
}

export function deletePlacement(episode: EpisodeDraft, segmentId: string): EpisodeDraft | null {
  const segment = episode.segments.find(candidate => candidate.id === segmentId)
  if (!segment?.editable)
    return null

  const placements = episode.placements.filter(placement => placement.id !== segment.placementId)
  const tracks = episode.tracks.filter(track => track.placementId !== segment.placementId)
  return finishEpisode(episode, placements, tracks, episode.originTimeMs)
}

export function playServerTickAt(placement: PlayPlacement, episodeTick: number): number {
  return placement.sourceStartServerTick + episodeTick - placement.startTick
}

export function reorderPlacementTracks(episode: EpisodeDraft, sourceIndex: number, targetIndex: number): EpisodeDraft | null {
  const source = episode.tracks[sourceIndex]
  const target = episode.tracks[targetIndex]
  if (!source || !target || source.placementId === target.placementId)
    return null

  const placementOrder = [...new Set(episode.tracks.map(track => track.placementId))]
  const sourceGroup = placementOrder.indexOf(source.placementId)
  const targetGroup = placementOrder.indexOf(target.placementId)
  const [moved] = placementOrder.splice(sourceGroup, 1)
  if (!moved)
    return null
  placementOrder.splice(targetGroup, 0, moved)
  const tracksByPlacement = new Map(placementOrder.map(placementId => [
    placementId,
    episode.tracks.filter(track => track.placementId === placementId),
  ]))
  const tracks = placementOrder.flatMap(placementId => tracksByPlacement.get(placementId) ?? [])
  return finishEpisode(episode, episode.placements, tracks, episode.originTimeMs)
}

export function replayDurationTicks(replay: null | RecorderMinecraftApiV1Replay): number {
  if (!replay)
    return 0

  const startTick = Number(replay.startServerTick)
  const endTick = Number(replay.endServerTick)
  if (Number.isSafeInteger(startTick) && Number.isSafeInteger(endTick) && endTick > startTick)
    return endTick - startTick

  const startedAt = replay.startedAt ? Date.parse(replay.startedAt) : Number.NaN
  const endedAt = replay.endedAt ? Date.parse(replay.endedAt) : Number.NaN
  if (Number.isFinite(startedAt) && Number.isFinite(endedAt) && endedAt > startedAt)
    return Math.round((endedAt - startedAt) * SERVER_TICK_RATE / 1_000)

  return 0
}

function finishEpisode(
  episode: EpisodeDraft,
  placements: PlayPlacement[],
  tracks: EpisodeTrack[],
  originTimeMs: number | undefined,
): EpisodeDraft {
  const segments = projectSegments(placements, tracks)
  return {
    ...episode,
    durationTicks: placements.reduce((latest, placement) => Math.max(latest, placement.endTick), 0),
    originTimeMs,
    placements,
    revision: episode.revision + 1,
    segments,
    tracks,
  }
}

function projectSegments(placements: PlayPlacement[], tracks: EpisodeTrack[]): EpisodeSegment[] {
  const placementsById = new Map(placements.map(placement => [placement.id, placement]))
  return tracks.flatMap((track): EpisodeSegment[] => {
    const placement = placementsById.get(track.placementId)
    if (!placement)
      return []
    if (track.role === 'primary') {
      return [{
        color: replayColors[stableColorIndex(placement.connectionId)]!,
        editable: true,
        endTick: placement.endTick,
        id: `${placement.id}:primary`,
        label: track.label,
        placementId: placement.id,
        startTick: placement.startTick,
        trackId: track.id,
      }]
    }

    return track.extension.items.flatMap((item): EpisodeSegment[] => {
      const itemStart = item.kind === 'point' ? item.serverTick : item.startServerTick
      const itemEnd = item.kind === 'point' ? item.serverTick : item.endServerTick
      if (itemEnd < placement.sourceStartServerTick || itemStart > placement.sourceEndServerTick)
        return []

      const sourceStart = Math.max(placement.sourceStartServerTick, Math.min(placement.sourceEndServerTick, itemStart))
      const sourceEnd = Math.max(sourceStart, Math.min(placement.sourceEndServerTick, itemEnd))
      const startTick = Math.min(placement.endTick - 1, placement.startTick + sourceStart - placement.sourceStartServerTick)
      const endTick = Math.min(placement.endTick, Math.max(startTick + 1, placement.startTick + sourceEnd - placement.sourceStartServerTick))
      return [{
        color: item.color,
        editable: false,
        endTick,
        id: `${placement.id}:extension:${track.extension.descriptor.extensionType}:${item.id}`,
        label: item.label,
        placementId: placement.id,
        sourceItemId: item.id,
        startTick,
        trackId: track.id,
      }]
    })
  })
}

function stableColorIndex(value: string): number {
  let hash = 0
  for (const character of value)
    hash = (hash * 31 + character.charCodeAt(0)) >>> 0
  return hash % replayColors.length
}
