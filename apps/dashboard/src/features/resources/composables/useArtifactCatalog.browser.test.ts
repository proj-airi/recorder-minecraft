import type { RecorderMinecraftApiV1ListReplaysResponse, RecorderMinecraftApiV1Replay } from '@proj-airi/recorder-minecraft-api'

import { expect, it, vi } from 'vitest'

import { catalogFixture } from '../fixtures'
import { useArtifactCatalog } from './useArtifactCatalog'

it('publishes basic rows before it merges the asynchronous summaries', async () => {
  let resolveSummaries: ((value: { data: RecorderMinecraftApiV1ListReplaysResponse }) => void) | undefined
  const summaryResponse = new Promise<{ data: RecorderMinecraftApiV1ListReplaysResponse }>((resolve) => {
    resolveSummaries = resolve
  })
  const replaysList = vi.fn(() => summaryResponse)
  const catalog = useArtifactCatalog({
    artifactsList: async () => ({ data: { serverInstances: [{
      instanceId: 'server-id',
      players: [{ replays: [{ connectionId: 'connection-id', playerName: 'player' }] }],
    }] } }),
    replaysList,
  })

  const load = catalog.load()
  await expect.poll(() => catalog.replays.value.length).toBe(1)
  expect(catalog.replays.value[0]?.summary).toBeUndefined()
  expect(catalog.isLoading.value).toBe(false)
  expect(catalog.isSummaryLoading.value).toBe(true)

  resolveSummaries?.({ data: { replays: [{
    connectionId: 'connection-id',
    summary: { durationTicks: '21', idlePercentage: 50, observedPathDistanceBlocks: 3.5 },
  } satisfies RecorderMinecraftApiV1Replay] } })
  await load

  expect(replaysList).toHaveBeenCalledWith({ query: { includeSummary: true } })
  expect(catalog.replays.value[0]?.summary?.durationTicks).toBe('21')
  expect(catalog.isSummaryLoading.value).toBe(false)
  expect(catalog.summaryError.value).toBeNull()
})

it('keeps basic rows when summary loading fails', async () => {
  const catalog = useArtifactCatalog({
    artifactsList: async () => ({ data: { serverInstances: [{ players: [{ replays: [{ connectionId: 'connection-id' }] }] }] } }),
    replaysList: async () => {
      throw new Error('corrupt completed Play')
    },
  })

  await catalog.load()

  expect(catalog.replays.value).toHaveLength(1)
  expect(catalog.error.value).toBeNull()
  expect(catalog.summaryError.value).toBe('Error: corrupt completed Play')
})

it('rescans before reloading and maps a linked Play to its world source', async () => {
  const calls: string[] = []
  const catalog = useArtifactCatalog({
    artifactsList: async () => {
      calls.push('list')
      return { data: { serverInstances: catalogFixture() } }
    },
    catalogRefresh: async () => {
      calls.push('refresh')
    },
    replaysList: async () => ({ data: { replays: [] } }),
  })
  await catalog.refresh()

  expect(calls).toEqual(['refresh', 'list'])
  expect(catalog.worldSessions.value.map(session => session.sessionId)).toEqual(['run1-session', 'run2-session'])
  const bob = catalog.replays.value.find(replay => replay.connectionId === 'bob-1')!
  expect(catalog.worldSourceFor(bob)).toMatchObject({ id: '20261010T080003Z--run1', sessionId: 'run1-session', startServerTick: 0 })
  const eve = catalog.replays.value.find(replay => replay.connectionId === 'eve-1')!
  expect(catalog.worldSourceFor(eve)).toBeUndefined()
})
