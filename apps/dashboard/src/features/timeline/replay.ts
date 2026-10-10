import type { RecorderMinecraftApiV1Replay } from '@proj-airi/recorder-minecraft-api'

import type {
  EpisodeDraft,
  EpisodeReplaySource,
  EpisodeSegment,
  EpisodeSession,
  EpisodeTrack,
  PlayPlacement,
  TimelineWorldSessionSource,
} from './domain'

import { SERVER_TICK_RATE } from './domain'
import { serverTickToEpisodeTick } from './ticks'

export { playServerTickAt } from './ticks'

const replayColors = ['#31b899', '#3d8bd9', '#7d6ee7', '#df6b63', '#d89b45', '#bd718a']
const WORLD_SEGMENT_COLOR = '#71717a'

/**
 * How the sessions of an episode share the horizontal axis.
 *
 * - `empty`: nothing is on the timeline.
 * - `server-tick`: one session; every clip is placed by Server tick.
 * - `wall-clock`: several sessions; clips inside each session are placed by Server tick, and the
 *   sessions are placed relative to each other by wall clock.
 * - `sequential`: several sessions, and at least one had no wall clock, so it was appended.
 */
export type EpisodeAlignmentMode = 'empty' | 'sequential' | 'server-tick' | 'wall-clock'

export interface EpisodeAlignmentSummary {
  label: string
  mode: EpisodeAlignmentMode
}

interface EpisodeState {
  originTimeMs: number | undefined
  placements: PlayPlacement[]
  sessions: EpisodeSession[]
}

/**
 * Adds several Plays in one edit. The earliest Play (by Server tick) anchors each new session, so
 * every Play of one session is aligned by Server tick no matter the input order.
 * Returns null when no Play could be added.
 */
export function addReplaysToEpisode(
  episode: EpisodeDraft,
  replays: readonly RecorderMinecraftApiV1Replay[],
  world?: TimelineWorldSessionSource,
): EpisodeDraft | null {
  const ordered = [...replays].sort((left, right) => compareReplayStart(left, right))
  let state: EpisodeState = { originTimeMs: episode.originTimeMs, placements: episode.placements, sessions: episode.sessions }
  let added = 0
  for (const replay of ordered) {
    const draft = { ...episode, ...state, durationTicks: episodeDuration(state.placements, state.sessions) }
    const next = insertReplay(draft, replay, world && replay.sessionId === world.sessionId ? world : undefined)
    if (!next)
      continue
    state = next
    added += 1
  }
  return added > 0 ? finishEpisode(episode, state.placements, state.sessions, state.originTimeMs) : null
}

/** Adds one Play to its session group and player lane, aligned by Server tick inside the session. */
export function addReplayToEpisode(
  episode: EpisodeDraft,
  replay: RecorderMinecraftApiV1Replay,
  world?: TimelineWorldSessionSource,
): EpisodeDraft | null {
  const next = insertReplay(episode, replay, world)
  return next ? finishEpisode(episode, next.placements, next.sessions, next.originTimeMs) : null
}

export function commitPlacementEdit(episode: EpisodeDraft, segmentId: string, startTick: number, endTick: number, trackId: string): EpisodeDraft | null {
  const segment = episode.segments.find(candidate => candidate.id === segmentId)
  const placement = episode.placements.find(candidate => candidate.id === segment?.placementId)
  const track = episode.tracks.find(candidate => candidate.id === trackId)
  if (!segment?.editable || !placement || track?.role !== 'primary' || track.id !== placement.laneId)
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
  return finishEpisode(episode, placements, episode.sessions, episode.originTimeMs)
}

export function createEmptyEpisode(): EpisodeDraft {
  return {
    durationTicks: 0,
    id: 'dataset-timeline',
    placements: [],
    revision: 1,
    segments: [],
    sessions: [],
    title: 'Dataset timeline',
    tracks: [],
  }
}

/** Splits a clip at an episode tick. Both halves stay on the same player lane. */
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
  return finishEpisode(episode, placements, episode.sessions, episode.originTimeMs)
}

export function deletePlacement(episode: EpisodeDraft, segmentId: string): EpisodeDraft | null {
  const segment = episode.segments.find(candidate => candidate.id === segmentId)
  if (!segment?.editable || !segment.placementId)
    return null

  const placements = episode.placements.filter(placement => placement.id !== segment.placementId)
  return finishEpisode(episode, placements, episode.sessions, episode.originTimeMs)
}

export function episodeAlignment(episode: EpisodeDraft): EpisodeAlignmentSummary {
  if (episode.sessions.length === 0)
    return { label: 'Empty timeline', mode: 'empty' }
  if (episode.sessions.length === 1)
    return { label: 'Aligned by server tick', mode: 'server-tick' }
  if (episode.sessions.some(session => session.placement === 'sequential'))
    return { label: 'Sessions placed in sequence (no wall clock)', mode: 'sequential' }
  return { label: 'Sessions aligned by wall clock', mode: 'wall-clock' }
}

/**
 * Rebuilds the derived rows and clips from placements and sessions. Exported for fixtures and for
 * callers that construct drafts directly; the revision always increments.
 */
export function finishEpisode(
  episode: EpisodeDraft,
  placements: PlayPlacement[],
  sessions: EpisodeSession[],
  originTimeMs: number | undefined,
): EpisodeDraft {
  const prunedSessions = pruneSessions(sessions, placements)
  const tracks = projectTracks(prunedSessions, placements)
  const segments = projectSegments(prunedSessions, placements)
  return {
    ...episode,
    durationTicks: episodeDuration(placements, prunedSessions),
    originTimeMs: prunedSessions.length === 0 ? undefined : originTimeMs,
    placements,
    revision: episode.revision + 1,
    segments,
    sessions: prunedSessions,
    tracks,
  }
}

/** Lane id for a player in a session. */
export function laneIdFor(sessionKey: string, playerKey: string): string {
  return `lane:${sessionKey}:${playerKey}`
}

export function playerKeyFor(replay: Pick<RecorderMinecraftApiV1Replay, 'connectionId' | 'playerName' | 'playerUuid'>): string {
  if (replay.playerUuid)
    return `uuid:${replay.playerUuid}`
  if (replay.playerName)
    return `name:${replay.playerName}`
  return `connection:${replay.connectionId ?? 'unknown'}`
}

/**
 * Moves a lane group. Two player lanes of one session reorder players inside that session; rows
 * of two different sessions reorder whole session groups.
 */
export function reorderLaneGroups(episode: EpisodeDraft, sourceTrackId: string, targetTrackId: string): EpisodeDraft | null {
  const source = episode.tracks.find(track => track.id === sourceTrackId)
  const target = episode.tracks.find(track => track.id === targetTrackId)
  if (!source || !target || source.id === target.id)
    return null

  if (source.sessionKey === target.sessionKey) {
    if (source.role !== 'primary' || target.role !== 'primary')
      return null
    const sessions = episode.sessions.map((session) => {
      if (session.key !== source.sessionKey)
        return session
      const order = session.playerOrder.filter(key => key !== source.playerKey)
      order.splice(session.playerOrder.indexOf(target.playerKey), 0, source.playerKey)
      return { ...session, playerOrder: order }
    })
    return finishEpisode(episode, episode.placements, sessions, episode.originTimeMs)
  }

  const moving = episode.sessions.find(session => session.key === source.sessionKey)
  const targetIndex = episode.sessions.findIndex(session => session.key === target.sessionKey)
  if (!moving || targetIndex < 0)
    return null
  const sessions = episode.sessions.filter(session => session.key !== moving.key)
  sessions.splice(targetIndex, 0, moving)
  return finishEpisode(episode, episode.placements, sessions, episode.originTimeMs)
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

/** Builds the episode source record for a catalog Play. */
export function replaySource(replay: RecorderMinecraftApiV1Replay): EpisodeReplaySource {
  return {
    actionsUrl: replay.actionsUrl || undefined,
    connectionId: replay.connectionId ?? '',
    endServerTick: replay.endServerTick,
    eventsUrl: replay.eventsUrl,
    framesIndexUrl: replay.framesIndexUrl || undefined,
    perceptionUrl: replay.perceptionUrl || undefined,
    playerName: replay.playerName ?? 'Unknown player',
    playerUuid: replay.playerUuid,
    replay,
    sceneUrl: replay.sceneUrl || undefined,
    serverName: replay.serverName ?? 'Unknown server',
    sessionId: replay.sessionId,
    startedAt: replay.startedAt,
    startServerTick: replay.startServerTick,
    videoFramesPerSecond: replay.video?.framesPerSecond,
    videoUrl: replay.video?.url,
    worldSessionId: replay.worldSessionId || undefined,
  }
}

export function sessionKeyFor(replay: Pick<RecorderMinecraftApiV1Replay, 'connectionId' | 'sessionId'>): string {
  return replay.sessionId ? `session:${replay.sessionId}` : `connection:${replay.connectionId ?? 'unknown'}`
}

function compareReplayStart(left: RecorderMinecraftApiV1Replay, right: RecorderMinecraftApiV1Replay): number {
  const leftTick = Number(left.startServerTick)
  const rightTick = Number(right.startServerTick)
  if (left.sessionId === right.sessionId && Number.isFinite(leftTick) && Number.isFinite(rightTick) && leftTick !== rightTick)
    return leftTick - rightTick
  const leftTime = left.startedAt ? Date.parse(left.startedAt) : Number.POSITIVE_INFINITY
  const rightTime = right.startedAt ? Date.parse(right.startedAt) : Number.POSITIVE_INFINITY
  return (Number.isFinite(leftTime) ? leftTime : Number.POSITIVE_INFINITY) - (Number.isFinite(rightTime) ? rightTime : Number.POSITIVE_INFINITY) || 0
}

function episodeDuration(placements: readonly PlayPlacement[], sessions: readonly EpisodeSession[]): number {
  let end = placements.reduce((latest, placement) => Math.max(latest, placement.endTick), 0)
  for (const session of sessions) {
    const worldEnd = session.world?.endServerTick
    if (worldEnd !== undefined && Number.isFinite(worldEnd))
      end = Math.max(end, serverTickToEpisodeTick(session, worldEnd))
  }
  return end
}

function insertReplay(
  episode: EpisodeDraft,
  replay: RecorderMinecraftApiV1Replay,
  world: TimelineWorldSessionSource | undefined,
): EpisodeState | null {
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
  const sessionKey = sessionKeyFor(replay)
  const playerKey = playerKeyFor(replay)
  const laneId = laneIdFor(sessionKey, playerKey)
  let originTimeMs = episode.originTimeMs
  let sessions = episode.sessions
  let session = sessions.find(candidate => candidate.key === sessionKey)

  if (!session) {
    // Anchor on the world-session start when it is known and earlier, so world data recorded
    // before the first Play joined stays on the timeline.
    const worldStart = world?.startServerTick
    const anchorServerTick = worldStart !== undefined && Number.isSafeInteger(worldStart) && worldStart < sourceStartServerTick
      ? worldStart
      : sourceStartServerTick
    const parsedWorldStart = world?.startedAt ? Date.parse(world.startedAt) : Number.NaN
    const anchorTimeMs = anchorServerTick === sourceStartServerTick
      ? startTimeMs
      : Number.isFinite(parsedWorldStart)
        ? parsedWorldStart
        : startTimeMs === undefined ? undefined : startTimeMs - (sourceStartServerTick - anchorServerTick) * 1_000 / SERVER_TICK_RATE
    let anchorTick = 0
    let placement: EpisodeSession['placement'] = 'origin'
    if (sessions.length > 0) {
      if (anchorTimeMs !== undefined && originTimeMs !== undefined) {
        anchorTick = Math.round((anchorTimeMs - originTimeMs) * SERVER_TICK_RATE / 1_000)
        placement = 'wall-clock'
      }
      else {
        anchorTick = episode.durationTicks
        placement = 'sequential'
      }
    }
    if (originTimeMs === undefined && anchorTimeMs !== undefined)
      originTimeMs = anchorTimeMs - anchorTick * 1_000 / SERVER_TICK_RATE

    session = {
      anchorServerTick,
      anchorTick,
      key: sessionKey,
      label: sessionLabel(replay, world),
      placement,
      playerOrder: [],
      serverName: replay.serverName,
      sessionId: replay.sessionId,
      world,
    }
    sessions = [...sessions, session]
  }
  else if (world && !session.world) {
    const withWorld: EpisodeSession = { ...session, label: sessionLabel(replay, world), world }
    sessions = sessions.map(candidate => candidate.key === sessionKey ? withWorld : candidate)
    session = withWorld
  }

  if (!session.playerOrder.includes(playerKey)) {
    const withPlayer: EpisodeSession = { ...session, playerOrder: [...session.playerOrder, playerKey] }
    sessions = sessions.map(candidate => candidate.key === sessionKey ? withPlayer : candidate)
    session = withPlayer
  }

  const startTick = serverTickToEpisodeTick(session, sourceStartServerTick)
  const source = replaySource(replay)
  const placement: PlayPlacement = {
    connectionId,
    endTick: startTick + durationTicks,
    id: `play:${connectionId}`,
    laneId,
    playEndServerTick: sourceEndServerTick,
    playerKey,
    playerName: source.playerName,
    playerUuid: replay.playerUuid,
    playStartServerTick: sourceStartServerTick,
    sessionId: replay.sessionId,
    sessionKey,
    source,
    sourceEndServerTick,
    sourceStartServerTick,
    startTick,
  }

  const state: EpisodeState = { originTimeMs, placements: [...episode.placements, placement], sessions }
  return startTick < 0 ? shiftEpisode(state, -startTick) : state
}

function projectSegments(sessions: readonly EpisodeSession[], placements: readonly PlayPlacement[]): EpisodeSegment[] {
  const segments: EpisodeSegment[] = []
  for (const session of sessions) {
    const sessionPlacements = placements.filter(placement => placement.sessionKey === session.key)
    const world = session.world
    const worldStart = world?.startServerTick
    const worldEnd = world?.endServerTick
    const startTick = worldStart !== undefined
      ? serverTickToEpisodeTick(session, worldStart)
      : Math.min(...sessionPlacements.map(placement => placement.startTick))
    const endTick = worldEnd !== undefined
      ? serverTickToEpisodeTick(session, worldEnd)
      : Math.max(...sessionPlacements.map(placement => placement.endTick))
    if (Number.isFinite(startTick) && Number.isFinite(endTick) && endTick > startTick) {
      segments.push({
        color: WORLD_SEGMENT_COLOR,
        editable: false,
        endTick,
        id: `${session.key}:world`,
        label: world ? `World session${session.sessionId ? ` ${session.sessionId.slice(0, 8)}` : ''}` : 'Session span (from Plays)',
        startTick: Math.max(0, startTick),
        trackId: `${session.key}:world`,
      })
    }

    for (const placement of sessionPlacements) {
      segments.push({
        color: replayColors[stableColorIndex(placement.playerKey)]!,
        editable: true,
        endTick: placement.endTick,
        id: `${placement.id}:primary`,
        label: [placement.playerName, placement.source.serverName].filter(Boolean).join(' · ') || 'Play',
        placementId: placement.id,
        startTick: placement.startTick,
        trackId: placement.laneId,
      })
    }
  }
  return segments
}

function projectTracks(sessions: readonly EpisodeSession[], placements: readonly PlayPlacement[]): EpisodeTrack[] {
  return sessions.flatMap((session): EpisodeTrack[] => {
    const rows: EpisodeTrack[] = [
      { id: `${session.key}:header`, kind: 'header', label: session.label, role: 'session', sessionKey: session.key },
      { id: `${session.key}:world`, kind: 'world', label: 'World', role: 'world', sessionKey: session.key },
    ]
    for (const playerKey of session.playerOrder) {
      const lanePlacements = placements.filter(placement => placement.sessionKey === session.key && placement.playerKey === playerKey)
      const first = lanePlacements[0]
      if (!first)
        continue
      rows.push({
        id: first.laneId,
        kind: lanePlacements.some(placement => placement.source.videoUrl) ? 'video' : 'data',
        label: first.playerName,
        playerKey,
        playerName: first.playerName,
        playerUuid: first.playerUuid,
        role: 'primary',
        sessionKey: session.key,
      })
    }
    return rows
  })
}

function pruneSessions(sessions: readonly EpisodeSession[], placements: readonly PlayPlacement[]): EpisodeSession[] {
  return sessions.flatMap((session) => {
    const players = new Set(placements.filter(placement => placement.sessionKey === session.key).map(placement => placement.playerKey))
    if (players.size === 0)
      return []
    const playerOrder = session.playerOrder.filter(key => players.has(key))
    return [playerOrder.length === session.playerOrder.length ? session : { ...session, playerOrder }]
  })
}

function sessionLabel(replay: RecorderMinecraftApiV1Replay, world: TimelineWorldSessionSource | undefined): string {
  if (world?.label)
    return world.label
  if (replay.sessionId)
    return `Session ${replay.sessionId.slice(0, 8)}`
  return `${replay.playerName ?? 'Play'} · no session id`
}

function shiftEpisode(state: EpisodeState, ticks: number): EpisodeState {
  return {
    originTimeMs: state.originTimeMs === undefined ? undefined : state.originTimeMs - ticks * 1_000 / SERVER_TICK_RATE,
    placements: state.placements.map(placement => ({
      ...placement,
      endTick: placement.endTick + ticks,
      startTick: placement.startTick + ticks,
    })),
    sessions: state.sessions.map(session => ({ ...session, anchorTick: session.anchorTick + ticks })),
  }
}

function stableColorIndex(value: string): number {
  let hash = 0
  for (const character of value)
    hash = (hash * 31 + character.charCodeAt(0)) >>> 0
  return hash % replayColors.length
}
