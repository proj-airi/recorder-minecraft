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
  revision: number
  segments: EpisodeSegment[]
  title: string
  tracks: EpisodeTrack[]
}

export interface EpisodeReplaySource {
  connectionId: string
  eventsUrl?: string
  playerName: string
  serverName: string
  startedAt?: string
  startServerTick?: string
  videoUrl: string
}

export interface EpisodeSegment {
  color: string
  endTick: number
  id: string
  label: string
  startTick: number
  trackId: string
}

export interface EpisodeTrack {
  id: string
  kind: TimelineTrackKind
  label: string
  replay?: EpisodeReplaySource
}

export type TimelineTrackKind = 'audio' | 'video'
