import type { RecorderMinecraftApiV1Replay, RecorderMinecraftApiV1WorldSession } from '@proj-airi/recorder-minecraft-api'

import type { EvidenceSourcesOptions } from '../sources'

import run1Perception from './run1-alice-perception.jsonl?raw'
import run1Alignment from './run1-session-alignment.jsonl?raw'
import run1World from './run1-world-events.jsonl?raw'
import run2Perception from './run2-alice-perception.jsonl?raw'
import run2Alignment from './run2-session-alignment.jsonl?raw'
import run2World from './run2-world-events.jsonl?raw'

/**
 * Test fixtures derived from the Sally-Anne demo (run1: Bob moves the diamond unseen; run2: seen).
 * World events and alignments are complete; `componentsDebug` strings and input digests are
 * stripped. Perception files hold the header and Alice's samples around each divergence start.
 */
export const SERVER_INSTANCE = 'a9ed874e-ae36-4489-a3aa-db77e2a83705'
export const ALICE_UUID = '40f5db53-a47a-33ee-b1f6-db0e20deded4'
export const BOB_UUID = '8e289159-2034-3a16-96b9-9fa637848b3b'

export const RUNS = {
  run1: {
    alice: '4c7c85a8-f8cc-4bc3-830f-5754c51b1ddc',
    aliceTicks: [232, 916],
    bob: 'adfab141-a8be-4724-aef8-eb5223b11cee',
    bobTicks: [242, 896],
    sessionId: 'd6dda5df-652f-4727-a081-a1853a4fb0be',
    startedAt: '2026-10-10T08:00:03.300Z',
    worldSessionId: '20261010T080003.300Z--d6dda5df-652f-4727-a081-a1853a4fb0be',
  },
  run2: {
    alice: '790d63fc-74cc-4935-ad31-2ffd58a5454a',
    aliceTicks: [117, 792],
    bob: '5f33e770-0d8c-4dd4-8f2b-f5bdc5cc82a0',
    bobTicks: [124, 772],
    sessionId: '024245d6-b07a-4ca5-8811-247c0f7bf2b4',
    startedAt: '2026-10-10T08:01:22.198Z',
    worldSessionId: '20261010T080122.198Z--024245d6-b07a-4ca5-8811-247c0f7bf2b4',
  },
} as const

export type RunName = keyof typeof RUNS

const FILES: Record<string, string> = {
  '/demo/run1/alice/perception.jsonl': run1Perception,
  '/demo/run1/alignments/session-alignment.jsonl': run1Alignment,
  '/demo/run1/world-events.jsonl': run1World,
  '/demo/run2/alice/perception.jsonl': run2Perception,
  '/demo/run2/alignments/session-alignment.jsonl': run2Alignment,
  '/demo/run2/world-events.jsonl': run2World,
}

export interface DemoOptions {
  /** Leave the alignment out of the world session, like a session that was never aligned. */
  withoutAlignment?: boolean
}

/**
 * Catalog Plays of one run. Bob's perception URL points at a file that is not in the fixtures,
 * which exercises the missing-file path.
 */
export function demoReplays(run: RunName, options: { bobPerception?: boolean } = {}): RecorderMinecraftApiV1Replay[] {
  const spec = RUNS[run]
  const common = { serverInstanceId: SERVER_INSTANCE, serverName: 'Nekos-MacBook-Pro.local', sessionId: spec.sessionId, startedAt: spec.startedAt, worldSessionId: spec.worldSessionId }
  return [
    {
      ...common,
      connectionId: spec.alice,
      endServerTick: String(spec.aliceTicks[1]),
      eventsUrl: `/demo/${run}/alice/capture/events.jsonl`,
      perceptionUrl: `/demo/${run}/alice/perception.jsonl`,
      playerName: 'alice',
      playerUuid: ALICE_UUID,
      startServerTick: String(spec.aliceTicks[0]),
    },
    {
      ...common,
      connectionId: spec.bob,
      endServerTick: String(spec.bobTicks[1]),
      eventsUrl: `/demo/${run}/bob/capture/events.jsonl`,
      ...(options.bobPerception === false ? {} : { perceptionUrl: `/demo/${run}/bob/perception.jsonl` }),
      playerName: 'bob',
      playerUuid: BOB_UUID,
      startServerTick: String(spec.bobTicks[0]),
    },
  ]
}

/** Fetch over the fixture files; anything else is a 404. Records every requested URL. */
export function demoSourcesOptions(options: DemoOptions = {}): EvidenceSourcesOptions & { requests: string[] } {
  const requests: string[] = []
  return {
    async fetch(url) {
      requests.push(url)
      const body = FILES[url]
      return body === undefined ? new Response('not found', { status: 404 }) : new Response(body, { headers: { 'content-type': 'application/x-ndjson' } })
    },
    async getWorldSession(ref) {
      const run = (Object.keys(RUNS) as RunName[]).find(name => RUNS[name].worldSessionId === ref.worldSessionId)
      return run ? demoWorldSession(run, options) : null
    },
    requests,
  }
}

export function demoWorldSession(run: RunName, options: DemoOptions = {}): RecorderMinecraftApiV1WorldSession {
  return {
    alignments: options.withoutAlignment ? [] : [{ name: 'session-alignment', url: `/demo/${run}/alignments/session-alignment.jsonl` }],
    eventsUrl: `/demo/${run}/world-events.jsonl`,
    id: RUNS[run].worldSessionId,
    serverInstanceId: SERVER_INSTANCE,
    sessionId: RUNS[run].sessionId,
    startServerTick: '0',
  }
}
