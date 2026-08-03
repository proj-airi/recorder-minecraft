import type { RecorderMinecraftApiV1Replay, RecorderMinecraftApiV1ServerInstance } from '@proj-airi/recorder-minecraft-api'
import type { ComputedRef, ShallowRef } from 'vue'

import { artifactsList } from '@proj-airi/recorder-minecraft-api'
import { computed, shallowReadonly, shallowRef } from 'vue'

export interface ArtifactCatalog {
  error: Readonly<ShallowRef<null | string>>
  isLoading: Readonly<ShallowRef<boolean>>
  load: () => Promise<void>
  replays: ComputedRef<RecorderMinecraftApiV1Replay[]>
  selectedReplay: Readonly<ShallowRef<null | RecorderMinecraftApiV1Replay>>
  selectReplay: (replay: RecorderMinecraftApiV1Replay) => void
  servers: Readonly<ShallowRef<RecorderMinecraftApiV1ServerInstance[]>>
}

export function useArtifactCatalog(): ArtifactCatalog {
  const servers = shallowRef<RecorderMinecraftApiV1ServerInstance[]>([])
  const selectedReplay = shallowRef<null | RecorderMinecraftApiV1Replay>(null)
  const isLoading = shallowRef(false)
  const error = shallowRef<null | string>(null)
  const replays = computed(() => servers.value.flatMap(server =>
    (server.players ?? []).flatMap(player => player.replays ?? []),
  ))

  async function load(): Promise<void> {
    isLoading.value = true
    error.value = null
    try {
      const response = await artifactsList()
      servers.value = response.data.serverInstances ?? []
    }
    catch (caught) {
      error.value = formatError(caught)
    }
    finally {
      isLoading.value = false
    }
  }

  function selectReplay(replay: RecorderMinecraftApiV1Replay): void {
    selectedReplay.value = replay
  }

  return {
    error: shallowReadonly(error),
    isLoading: shallowReadonly(isLoading),
    load,
    replays,
    selectedReplay: shallowReadonly(selectedReplay),
    selectReplay,
    servers: shallowReadonly(servers),
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
