import type { RecorderMinecraftApiV1Replay } from '@proj-airi/recorder-minecraft-api'

export const SERVER_TICK_RATE = 20

/** Height of one player lane, the row that hosts a player's Play clips. */
export const TIMELINE_TRACK_HEIGHT = 64
/** Height of a session group header row. */
export const TIMELINE_SESSION_ROW_HEIGHT = 28
/** Height of the world-session lane slot in a session group. */
export const TIMELINE_WORLD_ROW_HEIGHT = 32
/** Height of one data track row (points and intervals). */
export const TIMELINE_DATA_ROW_HEIGHT = 32

export interface CommitSegmentEdit {
  endTick: number
  segmentId: string
  startTick: number
  trackId: string
}

/**
 * Draft of the dataset timeline.
 *
 * Episode ticks are the shared horizontal coordinate. Inside one session, episode ticks follow
 * Server ticks one to one. Different sessions are positioned by wall clock, because Server ticks
 * are only comparable inside one `session_id`.
 */
export interface EpisodeDraft {
  durationTicks: number
  id: string
  /** Wall-clock time (Unix ms) of episode tick 0, when any Play reported `startedAt`. */
  originTimeMs?: number
  placements: PlayPlacement[]
  revision: number
  /** Primary clips. Data track items are not segments; see `features/timeline/data-tracks`. */
  segments: EpisodeSegment[]
  sessions: EpisodeSession[]
  title: string
  /** Every row of the draft in display order: session header, world slot, then player lanes. */
  tracks: EpisodeTrack[]
}

/** Player lane. All Plays of one player in one session share this row. */
export interface EpisodePrimaryTrack extends EpisodeTrackBase {
  playerKey: string
  playerName: string
  playerUuid?: string
  role: 'primary'
}

export interface EpisodeReplaySource {
  connectionId: string
  endServerTick?: string
  eventsUrl?: string
  /** `renders/fpv_frames/frames.jsonl` URL; maps video frames to Server ticks when present. */
  framesIndexUrl?: string
  playerName: string
  playerUuid?: string
  /** The catalog record, kept for data track providers that need fields this type does not copy. */
  replay?: RecorderMinecraftApiV1Replay
  serverName: string
  sessionId?: string
  startedAt?: string
  startServerTick?: string
  videoFramesPerSecond?: number
  videoUrl?: string
}

export interface EpisodeSegment {
  color: string
  editable: boolean
  endTick: number
  id: string
  label: string
  /** The Play placement shown by this clip. World-session clips have no placement. */
  placementId?: string
  startTick: number
  trackId: string
}

export interface EpisodeSession {
  /** Server tick that maps to `anchorTick`. Inside a session, episode tick = anchorTick + (serverTick - anchorServerTick). */
  anchorServerTick: number
  anchorTick: number
  /** Stable group key: `session:<sessionId>`, or `connection:<connectionId>` for a Play without a session id. */
  key: string
  label: string
  placement: EpisodeSessionPlacement
  /** Player keys in display order. */
  playerOrder: string[]
  serverName?: string
  sessionId?: string
  world?: TimelineWorldSessionSource
}

/**
 * How a session group is positioned relative to the other session groups.
 *
 * - `origin`: the first session; it defines episode tick 0.
 * - `wall-clock`: placed by the wall-clock `startedAt` of its first Play.
 * - `sequential`: no wall clock was available, so it was appended after the episode end.
 */
export type EpisodeSessionPlacement = 'origin' | 'sequential' | 'wall-clock'

/** Session group header row. */
export interface EpisodeSessionTrack extends EpisodeTrackBase {
  role: 'session'
}

export type EpisodeTrack = EpisodePrimaryTrack | EpisodeSessionTrack | EpisodeWorldTrack

/** World-session lane slot. Session-scoped data tracks are listed right after it. */
export interface EpisodeWorldTrack extends EpisodeTrackBase {
  role: 'world'
}

/** Item shape produced by Play extension modules. */
export interface NormalizedTimelineInterval extends NormalizedTimelineItemBase {
  endServerTick: number
  kind: 'interval'
  startServerTick: number
}

export type NormalizedTimelineItem = NormalizedTimelineInterval | NormalizedTimelinePoint

export interface NormalizedTimelinePoint extends NormalizedTimelineItemBase {
  kind: 'point'
  serverTick: number
}

export interface PlayPlacement {
  connectionId: string
  endTick: number
  id: string
  /** Track id of the player lane that hosts this clip. Cuts keep it. */
  laneId: string
  playEndServerTick: number
  playerKey: string
  playerName: string
  playerUuid?: string
  playStartServerTick: number
  sessionId?: string
  sessionKey: string
  source: EpisodeReplaySource
  sourceEndServerTick: number
  sourceStartServerTick: number
  startTick: number
}

export type TimelineTrackKind = 'data' | 'header' | 'markers' | 'video' | 'world'

export interface TimelineWorldSessionAlignmentSource {
  name: string
  url: string
}

/**
 * Local view of a world session. Phase 2 maps the backend `WorldSession` type onto it.
 * Ticks are Server ticks of the same session.
 */
export interface TimelineWorldSessionSource {
  alignments?: TimelineWorldSessionAlignmentSource[]
  endedAt?: string
  endServerTick?: number
  eventsUrl?: string
  id?: string
  label?: string
  metadataUrl?: string
  sessionId: string
  startedAt?: string
  startServerTick?: number
}

interface EpisodeTrackBase {
  id: string
  kind: TimelineTrackKind
  label: string
  sessionKey: string
}

interface NormalizedTimelineItemBase {
  color: string
  data: unknown
  id: string
  label: string
}
