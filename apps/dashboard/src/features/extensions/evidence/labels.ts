import type {
  AlignedEvent,
  BlockPos,
  ContainerSnapshotReason,
  DivergenceEnd,
  ItemStack,
  RecordRef,
  RecordStream,
  TargetVisibility,
  WorldContainerEvent,
} from './formats'

/** Colors shared by the tracks and the details view. */
export const EVIDENCE_COLORS = {
  click: '#f472b6',
  divergence: '#f43f5e',
  divergenceOpen: '#fb923c',
  lootUngenerated: '#a855f7',
  removedChunk: '#71717a',
  removedDestroyed: '#ef4444',
  snapshotChanged: '#facc15',
  snapshotLoaded: '#38bdf8',
  snapshotSessionStart: '#94a3b8',
  undetermined: '#78716c',
  viewClosed: '#a3a3a3',
  viewContents: '#22d3ee',
  viewOpened: '#4ade80',
  visibleBlock: '#34d399',
  visibleEntity: '#60a5fa',
} as const

/** `chest (4, -60, 0)`; the dimension is added outside the overworld. */
export function containerLabel(typeId: string, dimension: string, pos: BlockPos): string {
  const type = typeId ? shortId(typeId).replace(/_/g, ' ') : 'container'
  const where = dimension && dimension !== 'minecraft:overworld' ? ` in ${dimensionName(dimension)}` : ''
  return `${type} (${formatPos(pos)})${where}`
}

export function dimensionName(dimension: string): string {
  return shortId(dimension).replace(/_/g, ' ')
}

export function formatPos(pos: BlockPos): string {
  return `${pos.x}, ${pos.y}, ${pos.z}`
}

/** `minecraft:oak_log` -> `Oak Log`. */
export function itemName(itemId: string): string {
  if (!itemId)
    return 'Unknown item'
  return shortId(itemId).split(/[_:]/).filter(Boolean).map(word => word[0]!.toUpperCase() + word.slice(1)).join(' ')
}

export function itemsSummary(slots: readonly ItemStack[]): string {
  if (slots.length === 0)
    return 'empty'
  const totals = new Map<string, number>()
  for (const slot of slots)
    totals.set(slot.itemId, (totals.get(slot.itemId) ?? 0) + slot.count)
  return [...totals.entries()].map(([itemId, count]) => `${itemName(itemId)} ×${count}`).join(', ')
}

export function shortId(id: string): string {
  return id.replace(/^minecraft:/, '')
}

export const SNAPSHOT_REASON_LABELS: Record<ContainerSnapshotReason, string> = {
  CHANGED: 'contents changed',
  LOADED: 'loaded',
  SESSION_START: 'present at session start',
  UNSPECIFIED: 'snapshot',
}

export function worldEventColor(event: WorldContainerEvent): string {
  if (event.kind === 'removed')
    return event.cause === 'DESTROYED' ? EVIDENCE_COLORS.removedDestroyed : EVIDENCE_COLORS.removedChunk
  if (event.contentsState === 'LOOT_UNGENERATED')
    return EVIDENCE_COLORS.lootUngenerated
  if (event.reason === 'CHANGED')
    return EVIDENCE_COLORS.snapshotChanged
  if (event.reason === 'SESSION_START')
    return EVIDENCE_COLORS.snapshotSessionStart
  return EVIDENCE_COLORS.snapshotLoaded
}

export function worldEventLabel(event: WorldContainerEvent): string {
  if (event.kind === 'removed')
    return event.cause === 'DESTROYED' ? 'destroyed' : event.cause === 'CHUNK_UNLOADED' ? 'chunk unloaded' : 'removed'
  if (event.contentsState === 'LOOT_UNGENERATED')
    return 'loot not generated'
  return SNAPSHOT_REASON_LABELS[event.reason]
}

export const DIVERGENCE_END_LABELS: Record<DivergenceEnd, string> = {
  ACTOR_COVERAGE_END: 'the actor\'s Play ended (state after it is unknown)',
  CONTAINER_REMOVED: 'the container was removed',
  REOBSERVED: 'the actor observed the container again',
  TRUTH_MATCHES: 'world contents changed back to the observed contents',
  TRUTH_UNDETERMINED: 'world contents became undetermined',
  UNSPECIFIED: 'unspecified',
  WORLD_COVERAGE_END: 'the world stream ended (state after it is unknown)',
}

export const DIVERGENCE_END_SHORT: Record<DivergenceEnd, string> = {
  ACTOR_COVERAGE_END: 'actor coverage end',
  CONTAINER_REMOVED: 'removed',
  REOBSERVED: 're-observed',
  TRUTH_MATCHES: 'truth matches',
  TRUTH_UNDETERMINED: 'truth undetermined',
  UNSPECIFIED: 'unspecified',
  WORLD_COVERAGE_END: 'world coverage end',
}

export function isCoverageEnd(end: DivergenceEnd): boolean {
  return end === 'ACTOR_COVERAGE_END' || end === 'WORLD_COVERAGE_END'
}

export const VISIBILITY_LABELS: Record<TargetVisibility, string> = {
  NOT_VISIBLE: 'not visible',
  UNDETERMINED: 'undetermined',
  UNSPECIFIED: 'unknown',
  VISIBLE: 'visible',
}

export const STREAM_LABELS: Record<RecordStream, string> = {
  CAPTURE_EVENTS: 'capture/events.jsonl',
  CAPTURE_METADATA: 'metadata.json',
  PERCEPTION: 'perception.jsonl',
  UNSPECIFIED: 'record',
  WORLD_EVENTS: 'world-events.jsonl',
}

export function interactionColor(event: AlignedEvent): string {
  if (event.kind === 'ACTOR_CONTAINER_CLICK')
    return EVIDENCE_COLORS.click
  switch (event.containerViewKind) {
    case 'CLOSED': return EVIDENCE_COLORS.viewClosed
    case 'OPENED': return EVIDENCE_COLORS.viewOpened
    default: return EVIDENCE_COLORS.viewContents
  }
}

export function interactionLabel(event: AlignedEvent): string {
  if (event.kind === 'ACTOR_CONTAINER_CLICK')
    return 'click'
  switch (event.containerViewKind) {
    case 'CARRIED': return 'carried'
    case 'CLOSED': return 'closed'
    case 'CONTENTS': return 'contents sent'
    case 'OPENED': return 'opened'
    case 'SLOT': return 'slot update'
    default: return 'view'
  }
}

export function recordRefLabel(ref: RecordRef): string {
  const sequence = ref.sequence > 0 ? ` #${ref.sequence}` : ''
  return `${STREAM_LABELS[ref.stream]} @ ${ref.serverTick}${sequence}`
}
