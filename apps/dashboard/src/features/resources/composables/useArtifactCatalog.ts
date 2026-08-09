import type {
  RecorderMinecraftApiV1ListArtifactsResponse,
  RecorderMinecraftApiV1ListReplaysResponse,
  RecorderMinecraftApiV1Replay,
  RecorderMinecraftApiV1ServerInstance,
} from '@proj-airi/recorder-minecraft-api'
import type { ComputedRef, ShallowRef } from 'vue'

import { artifactsList, replaysList } from '@proj-airi/recorder-minecraft-api'
import { computed, shallowReadonly, shallowRef } from 'vue'

export interface ArtifactCatalog {
  error: Readonly<ShallowRef<null | string>>
  isLoading: Readonly<ShallowRef<boolean>>
  isSummaryLoading: Readonly<ShallowRef<boolean>>
  load: () => Promise<void>
  replays: ComputedRef<RecorderMinecraftApiV1Replay[]>
  selectedReplay: Readonly<ShallowRef<null | RecorderMinecraftApiV1Replay>>
  selectReplay: (replay: RecorderMinecraftApiV1Replay) => void
  servers: Readonly<ShallowRef<RecorderMinecraftApiV1ServerInstance[]>>
  summaryError: Readonly<ShallowRef<null | string>>
}

interface CatalogApi {
  artifactsList: () => Promise<{ data: RecorderMinecraftApiV1ListArtifactsResponse }>
  replaysList: (options: { query: { includeSummary: true } }) => Promise<{ data: RecorderMinecraftApiV1ListReplaysResponse }>
}

export function useArtifactCatalog(api: CatalogApi = { artifactsList, replaysList }): ArtifactCatalog {
  const servers = shallowRef<RecorderMinecraftApiV1ServerInstance[]>([])
  const selectedReplay = shallowRef<null | RecorderMinecraftApiV1Replay>(null)
  const isLoading = shallowRef(false)
  const isSummaryLoading = shallowRef(false)
  const error = shallowRef<null | string>(null)
  const summaryError = shallowRef<null | string>(null)
  const replays = computed(() => servers.value.flatMap(server =>
    (server.players ?? []).flatMap(player => player.replays ?? []),
  ))

  async function load(): Promise<void> {
    isLoading.value = true
    error.value = null
    summaryError.value = null
    let basicLoaded = false
    try {
      const response = await api.artifactsList()
      servers.value = response.data.serverInstances ?? []
      basicLoaded = true
    }
    catch (caught) {
      error.value = formatError(caught)
    }
    finally {
      isLoading.value = false
    }
    if (!basicLoaded)
      return

    isSummaryLoading.value = true
    try {
      const response = await api.replaysList({ query: { includeSummary: true } })
      mergeSummaries(response.data.replays ?? [])
    }
    catch (caught) {
      summaryError.value = formatError(caught)
    }
    finally {
      isSummaryLoading.value = false
    }
  }

  function mergeSummaries(enrichedReplays: RecorderMinecraftApiV1Replay[]): void {
    const summaries = new Map(enrichedReplays.map(replay => [replay.connectionId, replay.summary]))
    servers.value = servers.value.map(server => ({
      ...server,
      players: server.players?.map(player => ({
        ...player,
        replays: player.replays?.map(replay => ({ ...replay, summary: summaries.get(replay.connectionId) })),
      })),
    }))
    if (selectedReplay.value?.connectionId) {
      selectedReplay.value = servers.value
        .flatMap(server => server.players ?? [])
        .flatMap(player => player.replays ?? [])
        .find(replay => replay.connectionId === selectedReplay.value?.connectionId) ?? selectedReplay.value
    }
  }

  function selectReplay(replay: RecorderMinecraftApiV1Replay): void {
    selectedReplay.value = replay
  }

  return {
    error: shallowReadonly(error),
    isLoading: shallowReadonly(isLoading),
    isSummaryLoading: shallowReadonly(isSummaryLoading),
    load,
    replays,
    selectedReplay: shallowReadonly(selectedReplay),
    selectReplay,
    servers: shallowReadonly(servers),
    summaryError: shallowReadonly(summaryError),
  }
}

function formatError(error: unknown): string {
  if (error instanceof Error)
    return `${error.name}: ${error.message}`
  if (typeof error === 'string')
    return error
  if (error && typeof error === 'object') {
    const status = error as { code?: unknown, message?: unknown }
    if (typeof status.message === 'string')
      return typeof status.code === 'number' ? `${status.message} (code ${status.code})` : status.message
    try {
      return JSON.stringify(error, null, 2)
    }
    catch {
      return 'The artifact service returned an unreadable error response.'
    }
  }
  return String(error)
}
