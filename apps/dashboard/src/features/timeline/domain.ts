export const SERVER_TICK_RATE = 20
export const TIMELINE_TRACK_HEIGHT = 64

export interface CommitSegmentEdit {
  endTick: number
  segmentId: string
  startTick: number
  trackId: string
}

export interface EpisodeDraft {
  durationTicks: number
  id: string
  originTimeMs?: number
  placements: PlayPlacement[]
  revision: number
  segments: EpisodeSegment[]
  title: string
  tracks: EpisodeTrack[]
}

export interface EpisodeExtensionTrack extends EpisodeTrackBase {
  extension: EpisodeExtensionTrackSource
  replay?: never
  role: 'extension'
}

export interface EpisodeExtensionTrackSource {
  descriptor: import('@proj-airi/recorder-minecraft-api').RecorderMinecraftApiV1PlayExtension
  items: NormalizedTimelineItem[]
}

export interface EpisodePrimaryTrack extends EpisodeTrackBase {
  extension?: never
  replay?: EpisodeReplaySource
  role: 'primary'
}

export interface EpisodeReplaySource {
  connectionId: string
  eventsUrl?: string
  playerName: string
  serverName: string
  startedAt?: string
  startServerTick?: string
  videoUrl?: string
}

export interface EpisodeSegment {
  color: string
  editable: boolean
  endTick: number
  id: string
  label: string
  placementId: string
  sourceItemId?: string
  startTick: number
  trackId: string
}

export type EpisodeTrack = EpisodeExtensionTrack | EpisodePrimaryTrack

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
  playEndServerTick: number
  playStartServerTick: number
  sourceEndServerTick: number
  sourceStartServerTick: number
  startTick: number
}

export type TimelineTrackKind = 'audio' | 'data' | 'video'

interface EpisodeTrackBase {
  id: string
  kind: TimelineTrackKind
  label: string
  placementId: string
}

interface NormalizedTimelineItemBase {
  color: string
  data: unknown
  id: string
  label: string
}
