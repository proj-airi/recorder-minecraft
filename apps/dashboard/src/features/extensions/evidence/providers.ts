import type {
  TimelineDataItem,
  TimelineDataTrackDescriptor,
  TimelinePlayerDataTrackProvider,
  TimelinePlayerTarget,
  TimelineSessionDataTrackProvider,
  TimelineSessionTarget,
} from '../../timeline/data-tracks/types'
import type { EpisodeReplaySource } from '../../timeline/domain'
import type { ContainerDivergence } from './formats'
import type { DivergencePayload, InteractionPayload, VisibilityPayload, WorldEventPayload } from './payload'
import type { PerceptionSummary, VisibilityTarget } from './perception'
import type { AlignmentIndex, EvidenceSources, EvidenceWorldSession, ResourceState, WorldSessionRef } from './sources'

import { shallowRef } from 'vue'

import { containerKey } from './formats'
import {
  containerLabel,
  DIVERGENCE_END_SHORT,
  EVIDENCE_COLORS,
  interactionColor,
  interactionLabel,
  isCoverageEnd,
  itemsSummary,
  worldEventColor,
  worldEventLabel,
} from './labels'
import { EVIDENCE_PLAYER_PROVIDER_ID, EVIDENCE_SESSION_PROVIDER_ID, EVIDENCE_VIEW_ID } from './payload'
import { blockTargetKey, entityTargetKey } from './perception'

/** Which visibility rows a player lane lists. */
export type VisibilityRowMode = 'aligned' | 'all'

/** Upper bound of visibility rows per player lane in "all" mode. */
export const MAX_VISIBILITY_ROWS = 24

/** Page-wide evidence display settings, changed from the evidence view. */
export const evidenceSettings = shallowRef<{ visibilityRows: VisibilityRowMode }>({ visibilityRows: 'aligned' })

const ORDER = { divergence: 20, interactions: 30, visibility: 40, world: 10 }

interface VisibilityRow {
  key: string
  label: string
}

export function createEvidencePlayerProvider(sources: EvidenceSources, settings = evidenceSettings): TimelinePlayerDataTrackProvider {
  return {
    describe(target) {
      const ref = worldSessionRefOf(target.plays)
      const perceptionPlays = target.plays.filter(play => play.replay?.perceptionUrl)
      if (!ref && perceptionPlays.length === 0)
        return []

      const world = worldSessionState(sources, target.plays)
      const alignmentRef = world?.status === 'ready' ? world.value.alignments[0] : undefined
      const alignment = alignmentRef ? sources.alignmentState(alignmentRef.url, alignmentRef.name) : null
      const alignmentUnavailable = !ref || world?.status === 'missing' || (world?.status === 'ready' && !alignmentRef) || alignment?.status === 'missing'

      const descriptors: TimelineDataTrackDescriptor[] = []
      descriptors.push(...divergenceDescriptors(sources, target, alignmentUnavailable, alignment))
      if (!alignmentUnavailable)
        descriptors.push(interactionDescriptor(sources, target))
      const rowsSource = alignmentUnavailable ? null : alignment ?? { status: 'loading' as const }
      descriptors.push(...visibilityDescriptors(sources, target, perceptionPlays, rowsSource, settings.value.visibilityRows))
      return descriptors
    },
    id: EVIDENCE_PLAYER_PROVIDER_ID,
    scope: 'player',
  }
}

export function createEvidenceSessionProvider(sources: EvidenceSources): TimelineSessionDataTrackProvider {
  return {
    describe(target) {
      if (!target.world && !worldSessionRefOf(target.plays))
        return []
      return [{
        color: EVIDENCE_COLORS.snapshotChanged,
        key: 'world-containers',
        label: 'World container events',
        async load() {
          const world = await resolveWorldSession(sources, target.world, target.plays)
          if (!world?.eventsUrl)
            return []
          return worldEventItems(world.eventsUrl, await sources.worldEvents(world.eventsUrl) ?? [])
        },
        order: ORDER.world,
        viewId: EVIDENCE_VIEW_ID,
      }]
    },
    id: EVIDENCE_SESSION_PROVIDER_ID,
    scope: 'session',
  }
}

export function visibilityTargetLabel(candidate: Omit<VisibilityTarget, 'runs'>): string {
  if (candidate.kind === 'block-entity' && candidate.blockPos)
    return containerLabel(candidate.typeId, candidate.dimension ?? '', candidate.blockPos)
  const type = candidate.typeId.replace(/^minecraft:/, '')
  return candidate.uuid ? `${type} ${candidate.uuid.slice(0, 8)}` : type
}

/** Catalog key of the world session of a Play, from the Replay fields of workstream A. */
export function worldSessionRefOf(plays: readonly EpisodeReplaySource[]): null | WorldSessionRef {
  for (const play of plays) {
    const replay = play.replay
    if (replay?.worldSessionId && replay.serverInstanceId)
      return { serverInstanceId: replay.serverInstanceId, worldSessionId: replay.worldSessionId }
  }
  return null
}

function actorConnections(target: TimelinePlayerTarget): Set<string> {
  return new Set(target.plays.map(play => play.connectionId))
}

/** Other participants and every container the alignment names. */
function alignedVisibilityRows(alignment: AlignmentIndex, target: TimelinePlayerTarget): VisibilityRow[] {
  const own = actorConnections(target)
  const rows: VisibilityRow[] = []
  const seen = new Set<string>()
  for (const participant of alignment.participantsByConnection.values()) {
    if (own.has(participant.connectionId) || participant.playerUuid === target.playerUuid || !participant.playerUuid)
      continue
    const key = entityTargetKey({ uuid: participant.playerUuid })
    if (seen.has(key))
      continue
    seen.add(key)
    rows.push({ key, label: participant.playerName || participant.playerUuid.slice(0, 8) })
  }
  for (const container of [...alignment.containers.values()].sort((left, right) => left.key.localeCompare(right.key)))
    rows.push({ key: blockTargetKey(container.dimension, container.blockPos), label: containerLabel(container.typeId, container.dimension, container.blockPos) })
  return rows
}

async function alignmentFor(sources: EvidenceSources, target: TimelinePlayerTarget): Promise<AlignmentIndex | null> {
  const world = await resolveWorldSession(sources, undefined, target.plays)
  const entry = world?.alignments[0]
  return entry ? sources.alignment(entry.url, entry.name) : null
}

/** Every perceived target, players first, then by visible samples; capped. */
function allVisibilityRows(summaries: readonly PerceptionSummary[], target: TimelinePlayerTarget): VisibilityRow[] {
  const merged = new Map<string, { samples: number, target: VisibilityTarget }>()
  for (const summary of summaries) {
    for (const candidate of summary.targets) {
      const entry = merged.get(candidate.key)
      const samples = candidate.visibleSamples + candidate.undeterminedSamples
      merged.set(candidate.key, { samples: (entry?.samples ?? 0) + samples, target: entry?.target ?? candidate })
    }
  }
  const isPlayer = (candidate: VisibilityTarget): number => candidate.typeId === 'minecraft:player' ? 0 : 1
  return [...merged.values()]
    .filter(entry => entry.target.uuid !== target.playerUuid)
    .sort((left, right) => isPlayer(left.target) - isPlayer(right.target) || right.samples - left.samples || left.target.key.localeCompare(right.target.key))
    .slice(0, MAX_VISIBILITY_ROWS)
    .map(({ target: candidate }) => ({ key: candidate.key, label: visibilityTargetLabel(candidate) }))
}

function containerName(alignment: AlignmentIndex, dimension: string, pos: ContainerDivergence['blockPos']): string {
  return containerLabel(alignment.containers.get(containerKey(dimension, pos))?.typeId ?? '', dimension, pos)
}

function divergenceDescriptors(
  sources: EvidenceSources,
  target: TimelinePlayerTarget,
  unavailable: boolean,
  alignment: null | ResourceState<AlignmentIndex>,
): TimelineDataTrackDescriptor[] {
  if (unavailable) {
    return [{ color: EVIDENCE_COLORS.divergence, key: 'divergences', label: 'Observation divergences · no session alignment', load: async () => [], order: ORDER.divergence, viewId: EVIDENCE_VIEW_ID }]
  }

  const loadAll = async (): Promise<readonly TimelineDataItem[]> => {
    const index = await alignmentFor(sources, target)
    return index ? divergencesOf(index, target).map(divergence => divergenceItem(index, target, divergence)) : []
  }

  if (alignment?.status !== 'ready') {
    return [{ color: EVIDENCE_COLORS.divergence, key: 'divergences', label: 'Observation divergences', load: loadAll, order: ORDER.divergence, viewId: EVIDENCE_VIEW_ID }]
  }

  // One row per container keeps overlapping intervals of different containers readable.
  const index = alignment.value
  const byContainer = new Map<string, ContainerDivergence[]>()
  for (const divergence of divergencesOf(index, target)) {
    const key = containerKey(divergence.dimension, divergence.blockPos)
    byContainer.set(key, [...byContainer.get(key) ?? [], divergence])
  }
  if (byContainer.size === 0)
    return [{ color: EVIDENCE_COLORS.divergence, key: 'divergences', label: 'Observation divergences', load: loadAll, order: ORDER.divergence, viewId: EVIDENCE_VIEW_ID }]

  return [...byContainer.entries()]
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([key, divergences], position) => ({
      color: EVIDENCE_COLORS.divergence,
      key: `divergences:${key}`,
      label: `Divergence · ${containerName(index, divergences[0]!.dimension, divergences[0]!.blockPos)}`,
      async load() {
        return divergences.map(divergence => divergenceItem(index, target, divergence))
      },
      order: ORDER.divergence + position / 100,
      viewId: EVIDENCE_VIEW_ID,
    }))
}

function divergenceItem(index: AlignmentIndex, target: TimelinePlayerTarget, divergence: ContainerDivergence): TimelineDataItem {
  const where = containerName(index, divergence.dimension, divergence.blockPos)
  const open = divergence.endTick === undefined
  const endTick = divergence.endTick ?? divergence.lastTick + 1
  const actorName = index.participantsByConnection.get(divergence.connectionId)?.playerName || target.playerName
  const reason = DIVERGENCE_END_SHORT[divergence.end]
  const play = target.plays.find(candidate => candidate.connectionId === divergence.connectionId)
  const payload: DivergencePayload = {
    actorName,
    alignment: index,
    containerLabel: where,
    divergence,
    evidence: 'divergence',
    ...(play?.replay?.perceptionUrl ? { perceptionUrl: play.replay.perceptionUrl } : {}),
  }
  return {
    category: 'observation_divergence',
    color: open || isCoverageEnd(divergence.end) ? EVIDENCE_COLORS.divergenceOpen : EVIDENCE_COLORS.divergence,
    connectionId: divergence.connectionId,
    endServerTick: endTick,
    id: `divergence:${divergence.connectionId}:${containerKey(divergence.dimension, divergence.blockPos)}:${divergence.startTick}`,
    kind: 'interval',
    label: `observed: ${itemsSummary(divergence.observed.slots)} · world: ${itemsSummary(divergence.truth.slots)}`,
    payload,
    startServerTick: divergence.startTick,
    tooltip: `${actorName} · ${where}: world contents differ from the last observed contents, ticks ${divergence.startTick}–${divergence.lastTick}. ${
      open ? `Open at coverage end (${reason}).` : `Ended at tick ${endTick}: ${reason}.`}`,
  }
}

function divergencesOf(alignment: AlignmentIndex, target: TimelinePlayerTarget): ContainerDivergence[] {
  const connections = actorConnections(target)
  return alignment.divergences.filter(divergence => connections.has(divergence.connectionId))
}

function interactionDescriptor(sources: EvidenceSources, target: TimelinePlayerTarget): TimelineDataTrackDescriptor {
  return {
    color: EVIDENCE_COLORS.viewContents,
    key: 'container-interactions',
    label: 'Container views & clicks',
    async load() {
      const index = await alignmentFor(sources, target)
      if (!index)
        return []
      const connections = actorConnections(target)
      return index.events
        .filter(event => (event.kind === 'ACTOR_CONTAINER_VIEW' || event.kind === 'ACTOR_CONTAINER_CLICK') && connections.has(event.source.connectionId))
        .map((event): TimelineDataItem => {
          const where = event.blockPos ? containerName(index, event.dimension, event.blockPos) : 'container'
          const what = interactionLabel(event)
          const payload: InteractionPayload = {
            actorName: index.participantsByConnection.get(event.source.connectionId)?.playerName || target.playerName,
            alignment: index,
            containerLabel: where,
            event,
            evidence: 'interaction',
          }
          return {
            category: event.kind === 'ACTOR_CONTAINER_CLICK' ? 'container_click' : 'container_view',
            color: interactionColor(event),
            connectionId: event.source.connectionId,
            id: `interaction:${event.source.connectionId}:${event.source.serverTick}:${event.source.sequence}`,
            kind: 'point',
            label: `${what} · ${where}`,
            payload,
            serverTick: event.source.serverTick,
            tooltip: `${what}: ${where} (tick ${event.source.serverTick}, seq ${event.source.sequence})`,
          }
        })
    },
    order: ORDER.interactions,
    viewId: EVIDENCE_VIEW_ID,
  }
}

/**
 * NOTICE: `target.world` (from the session glue) is preferred. When it lacks the alignment list,
 * the world session is read from the catalog through the Plays' `worldSessionId`. Player targets
 * have no `world`, so they always use the catalog. Switch to the episode session's `world` once
 * player targets carry it.
 */
async function resolveWorldSession(
  sources: EvidenceSources,
  world: TimelineSessionTarget['world'] | undefined,
  plays: readonly EpisodeReplaySource[],
): Promise<EvidenceWorldSession | null> {
  if (world?.alignments && world.eventsUrl)
    return { alignments: world.alignments, eventsUrl: world.eventsUrl, id: world.id }
  const ref = worldSessionRefOf(plays)
  const fromCatalog = ref ? await sources.worldSession(ref) : null
  if (!world)
    return fromCatalog
  return {
    alignments: world.alignments ?? fromCatalog?.alignments ?? [],
    eventsUrl: world.eventsUrl ?? fromCatalog?.eventsUrl,
    id: world.id ?? fromCatalog?.id,
  }
}

function visibilityDescriptors(
  sources: EvidenceSources,
  target: TimelinePlayerTarget,
  perceptionPlays: readonly EpisodeReplaySource[],
  alignment: null | ResourceState<AlignmentIndex>,
  mode: VisibilityRowMode,
): TimelineDataTrackDescriptor[] {
  if (perceptionPlays.length === 0)
    return [{ color: EVIDENCE_COLORS.visibleEntity, key: 'visibility', label: 'Visibility · no perception.jsonl', load: async () => [], order: ORDER.visibility, viewId: EVIDENCE_VIEW_ID }]

  const urls = perceptionPlays.map(play => play.replay!.perceptionUrl!)
  const loadSummaries = async (): Promise<{ play: EpisodeReplaySource, summary: PerceptionSummary }[]> => {
    const loaded = await Promise.all(perceptionPlays.map(async play => ({ play, summary: await sources.perception(play.replay!.perceptionUrl!) })))
    return loaded.filter((entry): entry is { play: EpisodeReplaySource, summary: PerceptionSummary } => entry.summary !== null)
  }

  let rows: null | VisibilityRow[] = null
  if (mode === 'aligned' && alignment?.status === 'ready') {
    rows = alignedVisibilityRows(alignment.value, target)
  }
  else if (mode === 'all' || alignment === null || alignment.status === 'error') {
    const summaries = urls.map(url => sources.peekPerception(url))
    if (summaries.every(state => state && state.status !== 'loading'))
      rows = allVisibilityRows(summaries.flatMap(state => state?.status === 'ready' ? [state.value] : []), target)
  }

  if (rows === null) {
    // Rows depend on data that is still loading; this placeholder starts the load and is
    // replaced by one row per target when it settles.
    return [{
      color: EVIDENCE_COLORS.visibleEntity,
      key: 'visibility',
      label: 'Visibility',
      async load() {
        await loadSummaries()
        return []
      },
      order: ORDER.visibility,
      viewId: EVIDENCE_VIEW_ID,
    }]
  }
  if (rows.length === 0)
    return [{ color: EVIDENCE_COLORS.visibleEntity, key: 'visibility', label: 'Visibility', load: async () => [], order: ORDER.visibility, viewId: EVIDENCE_VIEW_ID }]

  return rows.map((row, position) => ({
    color: row.key.startsWith('entity:') ? EVIDENCE_COLORS.visibleEntity : EVIDENCE_COLORS.visibleBlock,
    key: `visibility:${row.key}`,
    label: `Visible · ${row.label}`,
    async load() {
      const items: TimelineDataItem[] = []
      for (const { play, summary } of await loadSummaries()) {
        const found = summary.targets.find(candidate => candidate.key === row.key)
        if (found)
          items.push(...visibilityItems(play, summary, found, row.label, target.playerName))
      }
      return items
    },
    order: ORDER.visibility + position / 100,
    viewId: EVIDENCE_VIEW_ID,
  }))
}

function visibilityItems(play: EpisodeReplaySource, summary: PerceptionSummary, found: VisibilityTarget, targetLabel: string, actorName: string): TimelineDataItem[] {
  const step = Math.max(1, summary.intervalTicks)
  const { runs, ...target } = found
  return runs.map((run): TimelineDataItem => {
    const payload: VisibilityPayload = {
      actorName,
      connectionId: play.connectionId,
      evidence: 'visibility',
      header: summary.header,
      intervalTicks: summary.intervalTicks,
      perceptionUrl: play.replay!.perceptionUrl!,
      run,
      target,
      targetLabel,
    }
    const state = run.state === 'visible' ? 'visible' : 'undetermined'
    return {
      category: `visibility_${run.state}`,
      color: run.state === 'undetermined' ? EVIDENCE_COLORS.undetermined : undefined,
      connectionId: play.connectionId,
      endServerTick: run.lastTick + step,
      id: `visibility:${play.connectionId}:${found.key}:${run.startTick}`,
      kind: 'interval',
      label: state,
      payload,
      startServerTick: run.startTick,
      tooltip: `${targetLabel} ${state} to ${actorName}, ticks ${run.startTick}–${run.lastTick} (${run.samples} samples, ${run.support.clear}/${run.support.inView} rays clear)`,
    }
  })
}

function worldEventItems(url: string, events: Awaited<ReturnType<EvidenceSources['worldEvents']>> & object): TimelineDataItem[] {
  return events.map((event) => {
    const where = containerLabel(event.blockEntityType, event.dimension, event.blockPos)
    const what = worldEventLabel(event)
    const contents = event.kind === 'snapshot' && event.contentsState === 'KNOWN' ? ` · ${itemsSummary(event.slots)}` : ''
    const payload: WorldEventPayload = { containerLabel: where, event, evidence: 'world-event', worldEventsUrl: url }
    return {
      category: event.kind === 'snapshot' ? 'container_snapshot' : 'container_removed',
      color: worldEventColor(event),
      id: `world:${event.sequence}`,
      kind: 'point',
      label: `${where} · ${what}`,
      payload,
      serverTick: event.serverTick,
      tooltip: `${where}: ${what}${contents} (tick ${event.serverTick}, seq ${event.sequence})`,
    }
  })
}

function worldSessionState(sources: EvidenceSources, plays: readonly EpisodeReplaySource[]): null | ResourceState<EvidenceWorldSession> {
  const ref = worldSessionRefOf(plays)
  return ref ? sources.worldSessionState(ref) : null
}
