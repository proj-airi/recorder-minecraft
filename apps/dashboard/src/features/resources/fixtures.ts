import type { RecorderMinecraftApiV1Replay, RecorderMinecraftApiV1ServerInstance, RecorderMinecraftApiV1WorldSession } from '@proj-airi/recorder-minecraft-api'

/**
 * Server A: world session run 1 (alice + bob, two observation divergences), world session run 2
 * (alice), and older Plays without a world session: carol and dave share capture session `cap1`,
 * eve stands alone and has a rendered video. Server B has one unlinked Play.
 */
export function catalogFixture(): RecorderMinecraftApiV1ServerInstance[] {
  const run1 = catalogWorldSession('20261010T080003Z--run1', 'run1-session', {
    alignments: [{
      divergenceCount: '2',
      eventCount: '57',
      name: 'session-alignment',
      participants: [
        { connectionId: 'alice-1', endServerTick: '916', perceptionProvided: true, playerName: 'alice', playerUuid: 'uuid-alice', startServerTick: '232' },
        { connectionId: 'bob-1', endServerTick: '896', perceptionProvided: true, playerName: 'bob', playerUuid: 'uuid-bob', startServerTick: '242' },
      ],
      url: '/assets/world/run1/alignments/session-alignment.jsonl',
    }],
    endedAt: '2026-10-10T08:00:58Z',
    knownGaps: ['world_entities_not_recorded'],
    plays: [
      { connectionId: 'alice-1', link: 'WORLD_SESSION_LINK_CONTAINER_TRUTH', playerName: 'alice', playerUuid: 'uuid-alice' },
      { connectionId: 'bob-1', link: 'WORLD_SESSION_LINK_CONTAINER_TRUTH', playerName: 'bob', playerUuid: 'uuid-bob' },
    ],
    startedAt: '2026-10-10T08:00:03Z',
    terminalReason: 'server_shutdown',
  })
  const run2 = catalogWorldSession('20261010T080122Z--run2', 'run2-session', {
    endedAt: '2026-10-10T08:02:10Z',
    plays: [{ connectionId: 'alice-2', link: 'WORLD_SESSION_LINK_SESSION_ID', playerName: 'alice', playerUuid: 'uuid-alice' }],
    startedAt: '2026-10-10T08:01:22Z',
    streamFailure: 'world stream closed early',
  })
  const linked = (id: string, player: string, session: RecorderMinecraftApiV1WorldSession, extra: Partial<RecorderMinecraftApiV1Replay> = {}) => catalogReplay(id, {
    perceptionUrl: `/assets/${id}/perception.jsonl`,
    player,
    sceneUrl: `/assets/${id}/scene.sqlite3`,
    sessionId: session.sessionId,
    startedAt: session.startedAt,
    worldSessionId: session.id,
    worldSessionLink: 'WORLD_SESSION_LINK_CONTAINER_TRUTH',
    ...extra,
  })
  const alice1 = linked('alice-1', 'alice', run1, { startServerTick: '232', summary: { durationTicks: '684', idlePercentage: 12, observedPathDistanceBlocks: 24.2 }, terminalReason: 'disconnect' })
  const bob1 = linked('bob-1', 'bob', run1, { startServerTick: '242' })
  const alice2 = linked('alice-2', 'alice', run2, { extensions: [{ extensionType: 'airicraft.planner' }], worldSessionLink: 'WORLD_SESSION_LINK_SESSION_ID' })
  const carol = catalogReplay('carol-1', { player: 'carol', sessionId: 'cap1', startedAt: '2026-08-02T21:00:00Z' })
  const dave = catalogReplay('dave-1', { player: 'dave', sessionId: 'cap1', startedAt: '2026-08-02T21:00:05Z' })
  const eve = catalogReplay('eve-1', {
    framesIndexUrl: '/assets/eve-1/renders/fpv_frames/frames.jsonl',
    player: 'eve',
    sessionId: 'cap2',
    startedAt: '2026-08-03T08:09:13Z',
    video: { framesPerSecond: 20, url: '/assets/eve-1/renders/fpv.mp4' },
  })
  const frank = catalogReplay('frank-1', { player: 'frank', serverInstanceId: 'server-b', serverName: 'Other server', sessionId: 'cap3', startedAt: '2026-07-01T00:00:00Z' })
  return [
    {
      instanceId: 'server-a',
      name: 'Test server',
      players: [
        { name: 'alice', replays: [alice1, alice2], uuid: 'uuid-alice' },
        { name: 'bob', replays: [bob1], uuid: 'uuid-bob' },
        { name: 'carol', replays: [carol], uuid: 'uuid-carol' },
        { name: 'dave', replays: [dave], uuid: 'uuid-dave' },
        { name: 'eve', replays: [eve], uuid: 'uuid-eve' },
      ],
      worldSessions: [run1, run2],
    },
    {
      instanceId: 'server-b',
      name: 'Other server',
      players: [{ name: 'frank', replays: [frank], uuid: 'uuid-frank' }],
      worldSessions: [],
    },
  ]
}

/** Catalog fixtures for resource-browser tests. */
export function catalogReplay(
  connectionId: string,
  options: Partial<RecorderMinecraftApiV1Replay> & { player: string },
): RecorderMinecraftApiV1Replay {
  const { player, ...rest } = options
  return {
    connectionId,
    endServerTick: '900',
    eventsUrl: `/assets/${connectionId}/capture/events.jsonl`,
    playerName: player,
    playerUuid: `uuid-${player}`,
    serverInstanceId: 'server-a',
    serverName: 'Test server',
    startServerTick: '200',
    ...rest,
  }
}

export function catalogWorldSession(id: string, sessionId: string, options: Partial<RecorderMinecraftApiV1WorldSession> = {}): RecorderMinecraftApiV1WorldSession {
  return {
    endServerTick: '1106',
    eventsUrl: `/assets/world/${id}/world-events.jsonl`,
    id,
    metadataUrl: `/assets/world/${id}/metadata.json`,
    serverInstanceId: 'server-a',
    serverName: 'Test server',
    sessionId,
    startServerTick: '0',
    ...options,
  }
}
