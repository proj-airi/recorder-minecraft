import type {
  EpisodeReplaySource,
  EpisodeSession,
  PlayPlacement,
  TimelineWorldSessionSource,
} from '../domain'

/** A data item resolved against the current episode, exposed as `selectedDataItem`. */
export interface SelectedTimelineDataItem {
  /** Episode tick where the item starts on the timeline, or null when trims hide it. */
  episodeTick: null | number
  item: TimelineDataItem
  /** Player scope: the clip that shows the item. Null for session scope or hidden items. */
  placement: null | PlayPlacement
  /** Server tick where the item starts. */
  serverTick: number
  session: EpisodeSession | null
  track: TimelineDataTrack
}

export interface TimelineDataInterval extends TimelineDataItemBase {
  endServerTick: number
  kind: 'interval'
  startServerTick: number
}

export type TimelineDataItem = TimelineDataInterval | TimelineDataPoint

export interface TimelineDataItemRef {
  itemId: string
  trackId: string
}

export interface TimelineDataPoint extends TimelineDataItemBase {
  kind: 'point'
  serverTick: number
}

/** Runtime row of one described data track. */
export interface TimelineDataTrack {
  color?: string
  descriptorKey: string
  error?: string
  /** `data:<providerId>:<targetKey>:<descriptorKey>` */
  id: string
  items: readonly TimelineDataItem[]
  label: string
  order: number
  providerId: string
  status: TimelineDataTrackStatus
  target: TimelineDataTrackTarget
  viewId?: string
}

export interface TimelineDataTrackDescriptor {
  /** Default item color. */
  color?: string
  /** Unique per provider and target. */
  key: string
  label: string
  /** Lazily called the first time the row is visible (its group is expanded). */
  load: (context: TimelineDataTrackLoadContext) => Promise<readonly TimelineDataItem[]>
  /** Lower first; ties sort by label. */
  order?: number
  /** Editor view to activate when an item of this track is selected, e.g. `extension:airicraft.planner`. */
  viewId?: string
}

export interface TimelineDataTrackLoadContext {
  /** Aborted when the track is removed from the timeline or reloaded. */
  signal: AbortSignal
}

export type TimelineDataTrackProvider = TimelinePlayerDataTrackProvider | TimelineSessionDataTrackProvider

/**
 * Generic data track contract.
 *
 * A provider attaches data tracks to a lane group. A player-scoped track is listed under one
 * player lane and its items follow that player's clips (trim, cut and move). A session-scoped
 * track is listed under the session's world slot and its items follow the session's Server tick
 * anchor, independent of clip edits.
 *
 * Items are always expressed in Server ticks of the target's session.
 */
export type TimelineDataTrackScope = 'player' | 'session'

export type TimelineDataTrackStatus = 'empty' | 'error' | 'idle' | 'loading' | 'ready'

export type TimelineDataTrackTarget = TimelinePlayerTarget | TimelineSessionTarget

export interface TimelinePlayerDataTrackProvider {
  /** Must be cheap and synchronous: it runs whenever the episode changes. Return [] when not applicable. */
  describe: (target: TimelinePlayerTarget) => readonly TimelineDataTrackDescriptor[]
  /** Globally unique, e.g. `evidence.perception`. */
  id: string
  scope: 'player'
}

/** A player lane group: all Plays of one player in one session. */
export interface TimelinePlayerTarget {
  /** Equal to `laneId`. */
  key: string
  laneId: string
  playerKey: string
  playerName: string
  playerUuid?: string
  /** Plays on this lane, ordered by start Server tick. */
  plays: readonly EpisodeReplaySource[]
  scope: 'player'
  sessionId?: string
  sessionKey: string
}

export interface TimelineSessionDataTrackProvider {
  describe: (target: TimelineSessionTarget) => readonly TimelineDataTrackDescriptor[]
  id: string
  scope: 'session'
}

/** A session group: its world-session source (when known) and all of its Plays. */
export interface TimelineSessionTarget {
  /** Equal to `sessionKey`. */
  key: string
  label: string
  plays: readonly EpisodeReplaySource[]
  scope: 'session'
  sessionId?: string
  sessionKey: string
  world?: TimelineWorldSessionSource
}

interface TimelineDataItemBase {
  /** Free-form grouping key, e.g. `container_open`. Shown in tooltips. */
  category?: string
  /** Stroke and fill color (any CSS color). Defaults to the descriptor color. */
  color?: string
  /** For player-scoped items: show the item only through clips of this Play. */
  connectionId?: string
  /** Unique inside one track. */
  id: string
  /** Short text drawn on wide intervals and used as the default tooltip. */
  label?: string
  /** Opaque value returned on selection, e.g. a record index or the parsed record. */
  payload?: unknown
  /** Hover text. Defaults to `label`. */
  tooltip?: string
}
