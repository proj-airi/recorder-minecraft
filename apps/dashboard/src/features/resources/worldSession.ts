import type {
  RecorderMinecraftApiV1Replay,
  RecorderMinecraftApiV1WorldSession,
  RecorderMinecraftApiV1WorldSessionLink,
} from '@proj-airi/recorder-minecraft-api'

import type { TimelineWorldSessionSource } from '../timeline/domain'

import { formatInstantShort, parseTick } from '../basic/format'

/** True when a Play can be placed on the timeline (the store rejects the others). */
export function isReplayAddable(replay: RecorderMinecraftApiV1Replay): boolean {
  return Boolean(replay.connectionId && (replay.video?.url || replay.eventsUrl) && !replay.validationError)
}

/** Plays of the catalog that belong to a world session. */
export function replaysOfWorldSession(
  session: RecorderMinecraftApiV1WorldSession,
  replays: readonly RecorderMinecraftApiV1Replay[],
): RecorderMinecraftApiV1Replay[] {
  return replays.filter(replay => replay.worldSessionId === session.id && replay.serverInstanceId === session.serverInstanceId)
}

/**
 * Maps a catalog world session onto the timeline's world-session source. `startServerTick` is
 * kept so the timeline anchors on the world start, and world events recorded before the first
 * Play joined stay visible. Returns undefined without a `sessionId`, because the timeline keys
 * sessions by it.
 */
export function toTimelineWorldSession(session: RecorderMinecraftApiV1WorldSession): TimelineWorldSessionSource | undefined {
  if (!session.sessionId)
    return undefined
  return {
    alignments: (session.alignments ?? []).flatMap(alignment => alignment.name && alignment.url && !alignment.validationError
      ? [{ name: alignment.name, url: alignment.url }]
      : []),
    endedAt: session.endedAt,
    endServerTick: parseTick(session.endServerTick),
    eventsUrl: session.eventsUrl,
    id: session.id,
    label: worldSessionLabel(session),
    metadataUrl: session.metadataUrl,
    sessionId: session.sessionId,
    startedAt: session.startedAt,
    startServerTick: parseTick(session.startServerTick),
  }
}

export function worldLinkDescription(link: RecorderMinecraftApiV1WorldSessionLink | undefined): string {
  switch (link) {
    case 'WORLD_SESSION_LINK_CONTAINER_TRUTH':
      return 'Linked by world container truth (metadata names the world session)'
    case 'WORLD_SESSION_LINK_SESSION_ID':
      return 'Linked by session id (the only world session with this session id)'
    default:
      return 'Not linked to a world session'
  }
}

export function worldLinkShort(link: RecorderMinecraftApiV1WorldSessionLink | undefined): string {
  switch (link) {
    case 'WORLD_SESSION_LINK_CONTAINER_TRUTH':
      return 'container truth'
    case 'WORLD_SESSION_LINK_SESSION_ID':
      return 'session id'
    default:
      return 'unlinked'
  }
}

/** The catalog world session a Play is linked to, if any. */
export function worldSessionForReplay(
  sessions: readonly RecorderMinecraftApiV1WorldSession[],
  replay: RecorderMinecraftApiV1Replay,
): RecorderMinecraftApiV1WorldSession | undefined {
  if (!replay.worldSessionId)
    return undefined
  return sessions.find(session => session.id === replay.worldSessionId && session.serverInstanceId === replay.serverInstanceId)
}

/** `Oct 10, 08:00:03 · d6dda5df`: start time and the short session id. */
export function worldSessionLabel(session: RecorderMinecraftApiV1WorldSession): string {
  const id = session.sessionId?.slice(0, 8)
  const time = session.startedAt ? formatInstantShort(session.startedAt) : null
  return [time, id].filter(Boolean).join(' · ') || session.id || 'World session'
}
