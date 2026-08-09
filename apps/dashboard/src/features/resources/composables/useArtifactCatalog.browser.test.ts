import type { RecorderMinecraftApiV1ListReplaysResponse, RecorderMinecraftApiV1Replay } from '@proj-airi/recorder-minecraft-api'

import { expect, it, vi } from 'vitest'

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
