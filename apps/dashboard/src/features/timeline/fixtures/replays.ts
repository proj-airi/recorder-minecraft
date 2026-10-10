import type { RecorderMinecraftApiV1Replay } from '@proj-airi/recorder-minecraft-api'

import type { PlayPlacement } from '../domain'

/** Minimal placement for component tests that build drafts by hand. */
export function testPlacement(overrides: Partial<PlayPlacement> & Pick<PlayPlacement, 'connectionId'>): PlayPlacement {
  const connectionId = overrides.connectionId
  return {
    endTick: 100,
    id: `play:${connectionId}`,
    laneId: `lane:session:test:uuid:${connectionId}`,
    playEndServerTick: 100,
    playerKey: `uuid:${connectionId}`,
    playerName: connectionId,
    playStartServerTick: 0,
    sessionKey: 'session:test',
    source: { connectionId, playerName: connectionId, serverName: 'test-server' },
    sourceEndServerTick: 100,
    sourceStartServerTick: 0,
    startTick: 0,
    ...overrides,
  }
}

/** Catalog Play for tests. Ticks are Server ticks; `startedAt` is wall clock. */
export function testReplay(
  connectionId: string,
  options: { end?: number, player?: string, sessionId?: string, start?: number, startedAt?: string, video?: boolean } = {},
): RecorderMinecraftApiV1Replay {
  const player = options.player ?? connectionId
  return {
    connectionId,
    endServerTick: String(options.end ?? 300),
    eventsUrl: `/events/${connectionId}.jsonl`,
    playerName: player,
    playerUuid: `uuid-${player}`,
    serverName: 'test-server',
    sessionId: options.sessionId,
    startedAt: options.startedAt,
    startServerTick: String(options.start ?? 100),
    video: options.video === false ? undefined : { url: `/video/${connectionId}.mp4` },
  }
}
