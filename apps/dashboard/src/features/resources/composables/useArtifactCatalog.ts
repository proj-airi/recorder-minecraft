import type {
  RecorderMinecraftApiV1ListArtifactsResponse,
  RecorderMinecraftApiV1ListReplaysResponse,
  RecorderMinecraftApiV1Replay,
  RecorderMinecraftApiV1ServerInstance,
  RecorderMinecraftApiV1WorldSession,
} from '@proj-airi/recorder-minecraft-api'
import type { ComputedRef, ShallowRef } from 'vue'

import type { TimelineWorldSessionSource } from '../../timeline/domain'

import { artifactsList, catalogRefresh, replaysList } from '@proj-airi/recorder-minecraft-api'
import { computed, shallowReadonly, shallowRef } from 'vue'

import { toTimelineWorldSession, worldSessionForReplay } from '../worldSession'

export interface ArtifactCatalog {
  error: Readonly<ShallowRef<null | string>>
  isLoading: Readonly<ShallowRef<boolean>>
  isRefreshing: Readonly<ShallowRef<boolean>>
  isSummaryLoading: Readonly<ShallowRef<boolean>>
  load: () => Promise<void>
  /** Asks the service to rescan the artifacts root, then reloads. */
  refresh: () => Promise<void>
  replays: ComputedRef<RecorderMinecraftApiV1Replay[]>
  selectedReplay: Readonly<ShallowRef<null | RecorderMinecraftApiV1Replay>>
  selectReplay: (replay: RecorderMinecraftApiV1Replay) => void
  servers: Readonly<ShallowRef<RecorderMinecraftApiV1ServerInstance[]>>
  summaryError: Readonly<ShallowRef<null | string>>
  /** Every world session of every server instance. */
  worldSessions: ComputedRef<RecorderMinecraftApiV1WorldSession[]>
  /**
   * Timeline world source of the world session a Play is linked to, or undefined when the Play
   * has none (or its session id differs, which the timeline cannot align).
   */
  worldSourceFor: (replay: RecorderMinecraftApiV1Replay) => TimelineWorldSessionSource | undefined
}

interface CatalogApi {
  artifactsList: () => Promise<{ data: RecorderMinecraftApiV1ListArtifactsResponse }>
  catalogRefresh?: () => Promise<unknown>
  replaysList: (options: { query: { includeSummary: true } }) => Promise<{ data: RecorderMinecraftApiV1ListReplaysResponse }>
}

export function useArtifactCatalog(api: CatalogApi = { artifactsList, catalogRefresh: () => catalogRefresh(), replaysList }): ArtifactCatalog {
  const servers = shallowRef<RecorderMinecraftApiV1ServerInstance[]>([])
  const selectedReplay = shallowRef<null | RecorderMinecraftApiV1Replay>(null)
  const isLoading = shallowRef(false)
  const isSummaryLoading = shallowRef(false)
  const isRefreshing = shallowRef(false)
  const error = shallowRef<null | string>(null)
  const summaryError = shallowRef<null | string>(null)
  const replays = computed(() => servers.value.flatMap(server =>
    (server.players ?? []).flatMap(player => player.replays ?? []),
  ))
  const worldSessions = computed(() => servers.value.flatMap(server => (server.worldSessions ?? []).map(session => ({
    ...session,
    serverInstanceId: session.serverInstanceId ?? server.instanceId,
  }))))

  async function refresh(): Promise<void> {
    isRefreshing.value = true
    try {
      // A failed rescan still reloads: the service keeps serving its last walk.
      await api.catalogRefresh?.().catch(() => undefined)
      await load()
    }
    finally {
      isRefreshing.value = false
    }
  }

  function worldSourceFor(replay: RecorderMinecraftApiV1Replay): TimelineWorldSessionSource | undefined {
    const session = worldSessionForReplay(worldSessions.value, replay)
    const source = session ? toTimelineWorldSession(session) : undefined
    return source && source.sessionId === replay.sessionId ? source : undefined
  }

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
    isRefreshing: shallowReadonly(isRefreshing),
    isSummaryLoading: shallowReadonly(isSummaryLoading),
    load,
    refresh,
    replays,
    selectedReplay: shallowReadonly(selectedReplay),
    selectReplay,
    servers: shallowReadonly(servers),
    summaryError: shallowReadonly(summaryError),
    worldSessions,
    worldSourceFor,
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
