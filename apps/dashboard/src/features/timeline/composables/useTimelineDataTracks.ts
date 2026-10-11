import type { ComputedRef, Ref } from 'vue'

import type { ProjectedDataLane } from '../data-tracks/projection'
import type {
  TimelineDataItem,
  TimelineDataTrack,
  TimelineDataTrackDescriptor,
  TimelineDataTrackProvider,
  TimelineDataTrackStatus,
  TimelineDataTrackTarget,
  TimelinePlayerTarget,
  TimelineSessionTarget,
} from '../data-tracks/types'
import type { EpisodeDraft, EpisodeReplaySource, PlayPlacement } from '../domain'

import { computed, onScopeDispose, shallowRef, watch } from 'vue'

import { projectDataTrack } from '../data-tracks/projection'

export interface TimelineDataTracksRuntime {
  /** Starts loading idle tracks; already loading or loaded tracks are left alone. */
  ensureLoaded: (trackIds: Iterable<string>) => void
  /** Projected lanes keyed by track id. */
  lanes: ComputedRef<ReadonlyMap<string, ProjectedDataLane>>
  reload: (trackId: string) => void
  /** Every described data track with its load state, in provider order. */
  tracks: ComputedRef<readonly TimelineDataTrack[]>
}

interface DescribedTrack {
  descriptor: TimelineDataTrackDescriptor
  id: string
  providerId: string
  signature: string
  target: TimelineDataTrackTarget
}

interface TrackState {
  controller?: AbortController
  error?: string
  items: readonly TimelineDataItem[]
  signature: string
  status: TimelineDataTrackStatus
}

export function dataTrackId(providerId: string, targetKey: string, descriptorKey: string): string {
  return `data:${providerId}:${targetKey}:${descriptorKey}`
}

/** Lane-group targets of an episode: one per session group and one per player lane. */
export function describeTargets(episode: EpisodeDraft): TimelineDataTrackTarget[] {
  const targets: TimelineDataTrackTarget[] = []
  for (const session of episode.sessions) {
    const sessionPlacements = episode.placements.filter(placement => placement.sessionKey === session.key)
    const sessionTarget: TimelineSessionTarget = {
      key: session.key,
      label: session.label,
      plays: uniquePlays(sessionPlacements),
      scope: 'session',
      sessionId: session.sessionId,
      sessionKey: session.key,
      world: session.world,
    }
    targets.push(sessionTarget)
    for (const track of episode.tracks) {
      if (track.role !== 'primary' || track.sessionKey !== session.key)
        continue
      const playerTarget: TimelinePlayerTarget = {
        key: track.id,
        laneId: track.id,
        playerKey: track.playerKey,
        playerName: track.playerName,
        playerUuid: track.playerUuid,
        plays: uniquePlays(sessionPlacements.filter(placement => placement.laneId === track.id)),
        scope: 'player',
        sessionId: session.sessionId,
        sessionKey: session.key,
      }
      targets.push(playerTarget)
    }
  }
  return targets
}

export function useTimelineDataTracks(
  episode: Readonly<Ref<EpisodeDraft>>,
  providers: () => readonly TimelineDataTrackProvider[],
): TimelineDataTracksRuntime {
  const states = shallowRef<ReadonlyMap<string, TrackState>>(new Map())

  const described = computed(() => {
    const result: DescribedTrack[] = []
    const targets = describeTargets(episode.value)
    for (const provider of providers()) {
      for (const target of targets) {
        if (target.scope !== provider.scope)
          continue
        let descriptors: readonly TimelineDataTrackDescriptor[]
        try {
          descriptors = provider.scope === 'player'
            ? provider.describe(target as TimelinePlayerTarget)
            : provider.describe(target as TimelineSessionTarget)
        }
        catch {
          continue
        }
        for (const descriptor of descriptors) {
          result.push({
            descriptor,
            id: dataTrackId(provider.id, target.key, descriptor.key),
            providerId: provider.id,
            signature: targetSignature(target),
            target,
          })
        }
      }
    }
    return result
  })

  const describedById = computed(() => new Map(described.value.map(entry => [entry.id, entry])))

  const tracks = computed<readonly TimelineDataTrack[]>(() => described.value.map((entry) => {
    const state = states.value.get(entry.id)
    const current = state?.signature === entry.signature ? state : undefined
    return {
      color: entry.descriptor.color,
      descriptorKey: entry.descriptor.key,
      error: current?.error,
      id: entry.id,
      items: current?.items ?? [],
      label: entry.descriptor.label,
      order: entry.descriptor.order ?? 0,
      providerId: entry.providerId,
      status: current?.status ?? 'idle',
      target: entry.target,
      viewId: entry.descriptor.viewId,
    }
  }))

  const lanes = computed<ReadonlyMap<string, ProjectedDataLane>>(() => {
    const result = new Map<string, ProjectedDataLane>()
    for (const track of tracks.value)
      result.set(track.id, projectDataTrack(episode.value, track))
    return result
  })

  function setState(trackId: string, state: TrackState | undefined): void {
    const next = new Map(states.value)
    if (state)
      next.set(trackId, state)
    else
      next.delete(trackId)
    states.value = next
  }

  function load(entry: DescribedTrack): void {
    states.value.get(entry.id)?.controller?.abort()
    const controller = new AbortController()
    setState(entry.id, { controller, items: [], signature: entry.signature, status: 'loading' })
    let pending: Promise<readonly TimelineDataItem[]>
    try {
      pending = Promise.resolve(entry.descriptor.load({ signal: controller.signal }))
    }
    catch (caught) {
      pending = Promise.reject(caught)
    }
    pending.then((items) => {
      if (controller.signal.aborted)
        return
      setState(entry.id, { items, signature: entry.signature, status: items.length > 0 ? 'ready' : 'empty' })
    }, (caught: unknown) => {
      if (controller.signal.aborted)
        return
      setState(entry.id, { error: errorMessage(caught), items: [], signature: entry.signature, status: 'error' })
    })
  }

  function ensureLoaded(trackIds: Iterable<string>): void {
    for (const trackId of trackIds) {
      const entry = describedById.value.get(trackId)
      if (!entry)
        continue
      const state = states.value.get(trackId)
      if (state && state.signature === entry.signature)
        continue
      load(entry)
    }
  }

  function reload(trackId: string): void {
    const entry = describedById.value.get(trackId)
    if (entry)
      load(entry)
  }

  // Abort and forget tracks that are no longer described, e.g. after their Plays were removed.
  watch(describedById, (current) => {
    let changed = false
    const next = new Map(states.value)
    for (const [trackId, state] of states.value) {
      if (current.get(trackId)?.signature === state.signature)
        continue
      state.controller?.abort()
      next.delete(trackId)
      changed = true
    }
    if (changed)
      states.value = next
  })

  onScopeDispose(() => states.value.forEach(state => state.controller?.abort()))

  return { ensureLoaded, lanes, reload, tracks }
}

function errorMessage(value: unknown): string {
  if (value && typeof value === 'object' && 'message' in value && typeof value.message === 'string')
    return value.message
  return String(value)
}

function targetSignature(target: TimelineDataTrackTarget): string {
  const plays = target.plays.map(play => play.connectionId).join(',')
  if (target.scope === 'player')
    return `player|${plays}`
  const world = target.world
  return `session|${plays}|${world?.id ?? ''}|${world?.eventsUrl ?? ''}|${world?.alignments?.map(alignment => alignment.url).join(',') ?? ''}`
}

function uniquePlays(placements: readonly PlayPlacement[]): EpisodeReplaySource[] {
  const byConnection = new Map<string, PlayPlacement>()
  for (const placement of placements) {
    if (!byConnection.has(placement.connectionId))
      byConnection.set(placement.connectionId, placement)
  }
  return [...byConnection.values()]
    .sort((left, right) => left.playStartServerTick - right.playStartServerTick)
    .map(placement => placement.source)
}
