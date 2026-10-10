/**
 * Normalized views of three ProtoJSON artifacts:
 * - `world-events.jsonl` (`WorldEvent`, `artifacts/v1/world.proto`);
 * - `perception.jsonl` (`PerceptionRecord`, `artifacts/v1/perception.proto`);
 * - `alignments/<name>.jsonl` (`SessionAlignmentRecord`, `artifacts/v1/session_alignment.proto`).
 *
 * ProtoJSON omits zero values (`z: 0`, `serverTick: "0"`, empty lists, unspecified enums) and
 * writes int64/uint64 as strings. Every reader below restores the zero value and converts int64
 * to a JavaScript number. Ticks and sequences stay far below 2^53.
 */

export interface AlignedEvent {
  blockPos?: BlockPos
  containerViewKind: ContainerViewKind
  dimension: string
  entityUuid: string
  kind: AlignedEventKind
  source: RecordRef
  typeId: string
  visibility: TargetVisibility
}

export type AlignedEventKind
  = | 'ACTOR_BLOCK_ENTITY_VISIBILITY'
    | 'ACTOR_CONTAINER_CLICK'
    | 'ACTOR_CONTAINER_VIEW'
    | 'ACTOR_ENTITY_VISIBILITY'
    | 'ACTOR_JOINED'
    | 'ACTOR_LEFT'
    | 'UNSPECIFIED'
    | 'WORLD_CONTAINER_REMOVED'
    | 'WORLD_CONTAINER_SNAPSHOT'

export interface AlignmentHeader {
  assumptions: string[]
  divergenceCount: number
  eventCount: number
  futureContext: string
  knownLimitations: string[]
  participants: AlignmentParticipant[]
  processor: { name: string, version: string }
  provenance: string
  scope: string
  sessionId: string
  usesFutureContext: boolean
  worldCoverage: TickRange
}

export interface AlignmentParticipant {
  connectionId: string
  coverage: TickRange
  observedContainerCount: number
  perceptionCoverage?: TickRange
  perceptionIntervalTicks: number
  playerName: string
  playerUuid: string
  terminalReason: string
  unobservedContainerCount: number
}

export type AlignmentRecord
  = | { divergence: ContainerDivergence, kind: 'divergence' }
    | { event: AlignedEvent, kind: 'event' }
    | { header: AlignmentHeader, kind: 'header' }

export interface BlockPos {
  x: number
  y: number
  z: number
}

export type ContainerContentsState = 'KNOWN' | 'LOOT_UNGENERATED' | 'UNSPECIFIED'
export interface ContainerDivergence {
  blockPos: BlockPos
  connectionId: string
  coPresence: CoPresence
  dimension: string
  end: DivergenceEnd
  endSource?: RecordRef
  /** Absent when coverage ended first. */
  endTick?: number
  lastTick: number
  observed: ContainerSlots
  startTick: number
  truth: ContainerSlots
  truthChanges: number
}
export type ContainerRemovedCause = 'CHUNK_UNLOADED' | 'DESTROYED' | 'UNSPECIFIED'

export interface ContainerSlots {
  slots: ItemStack[]
  sources: RecordRef[]
}

export type ContainerSnapshotReason = 'CHANGED' | 'LOADED' | 'SESSION_START' | 'UNSPECIFIED'

export type ContainerViewKind = 'CARRIED' | 'CLOSED' | 'CONTENTS' | 'OPENED' | 'SLOT' | 'UNSPECIFIED'

export interface CoPresence {
  container: TargetVisibility
  entities: CoPresentEntity[]
  sample?: RecordRef
  status: CoPresenceStatus
}

export type CoPresenceStatus = 'NO_SAMPLE' | 'PERCEPTION_NOT_PROVIDED' | 'SAMPLED' | 'UNSPECIFIED'
export interface CoPresentEntity {
  participantConnectionId: string
  /** Absent when the entity is not a participant whose Play covers the start tick. */
  participantContainerOpen?: boolean
  participantMenu?: RecordRef
  typeId: string
  uuid?: string
  visibility: TargetVisibility
}
export type DivergenceEnd
  = | 'ACTOR_COVERAGE_END'
    | 'CONTAINER_REMOVED'
    | 'REOBSERVED'
    | 'TRUTH_MATCHES'
    | 'TRUTH_UNDETERMINED'
    | 'UNSPECIFIED'
    | 'WORLD_COVERAGE_END'

export interface ItemStack {
  count: number
  damage: number
  itemId: string
  maxDamage: number
  slot: number
}

export interface PerceivedBlockEntity {
  blockPos: BlockPos
  dimension: string
  distance: number
  support: RaySupport
  typeId: string
}

export interface PerceivedEntity {
  distance: number
  instanceId: string
  support: RaySupport
  typeId: string
  uuid?: string
}

export interface PerceptionHeader {
  assumptions: {
    camera: { aspectRatio: number, horizontalFovDegrees: number, nearPlaneBlocks: number, verticalFovDegrees: number }
    defaultEyeHeight: number
    maxDistanceBlocks: number
    maxDistanceCappedByViewDistance: boolean
    observerSource: string
    occluderModel: { description: string, name: string }
    samplingIntervalTicks: number
    targetSampling: { blockEntityPoints: string, entityPoints: string, visibilityRule: string }
    unknownCellPolicy: string
  }
  connectionId: string
  knownLimitations: string[]
  playerUuid: string
  processor: { name: string, version: string }
  provenance: string
  sampleCount: number
  scope: string
  sessionId: string
  ticks: TickRange
  usesFutureContext: boolean
}

export type PerceptionRecord
  = | { header: PerceptionHeader, kind: 'header' }
    | { kind: 'sample', sample: PerceptionSample }

export interface PerceptionSample {
  dimension: string
  serverTick: number
  undeterminedBlockEntities: PerceivedBlockEntity[]
  undeterminedEntities: PerceivedEntity[]
  visibleBlockEntities: PerceivedBlockEntity[]
  visibleEntities: PerceivedEntity[]
}

export interface RaySupport {
  blocked: number
  clear: number
  inView: number
  points: number
  unknown: number
}

/** `RecordRef`: one source record by stream, actor, tick and sequence. */
export interface RecordRef {
  connectionId: string
  sequence: number
  serverTick: number
  stream: RecordStream
}

export type RecordStream = 'CAPTURE_EVENTS' | 'CAPTURE_METADATA' | 'PERCEPTION' | 'UNSPECIFIED' | 'WORLD_EVENTS'

export type TargetVisibility = 'NOT_VISIBLE' | 'UNDETERMINED' | 'UNSPECIFIED' | 'VISIBLE'

export interface TickRange {
  firstTick: number
  lastTick: number
}

export type WorldContainerEvent = WorldContainerRemoved | WorldContainerSnapshot

export interface WorldContainerRemoved {
  blockEntityType: string
  blockPos: BlockPos
  cause: ContainerRemovedCause
  dimension: string
  kind: 'removed'
  sequence: number
  serverTick: number
}

export interface WorldContainerSnapshot {
  blockEntityType: string
  blockPos: BlockPos
  containerSize: number
  contentsState: ContainerContentsState
  dimension: string
  kind: 'snapshot'
  lootTable?: string
  reason: ContainerSnapshotReason
  sequence: number
  serverTick: number
  slots: ItemStack[]
}

type Json = Record<string, unknown>

export function blockPos(value: unknown): BlockPos {
  const pos = object(value)
  return { x: num(pos.x), y: num(pos.y), z: num(pos.z) }
}

export function isRecord(value: unknown): value is Json {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

/** int32, double, or ProtoJSON int64 string. Absent means zero. */
export function num(value: unknown): number {
  if (typeof value === 'number')
    return Number.isFinite(value) ? value : 0
  if (typeof value === 'string' && value.trim() !== '') {
    const parsed = Number(value)
    return Number.isFinite(parsed) ? parsed : 0
  }
  return 0
}

/** Strips the enum prefix; accepts names and numbers. Absent means UNSPECIFIED. */
function enumName<T extends string>(value: unknown, prefix: string, byNumber: readonly T[]): T {
  if (typeof value === 'number')
    return byNumber[value] ?? ('UNSPECIFIED' as T)
  if (typeof value !== 'string' || value === '')
    return 'UNSPECIFIED' as T
  return (value.startsWith(prefix) ? value.slice(prefix.length) : value) as T
}

function itemStack(value: unknown): ItemStack {
  const slot = object(value)
  return { count: num(slot.count), damage: num(slot.damage), itemId: str(slot.itemId), maxDamage: num(slot.maxDamage), slot: num(slot.slot) }
}

function list(value: unknown): unknown[] {
  return Array.isArray(value) ? value : []
}

function object(value: unknown): Json {
  return isRecord(value) ? value : {}
}

function raySupport(value: unknown): RaySupport {
  const support = object(value)
  return { blocked: num(support.blocked), clear: num(support.clear), inView: num(support.inView), points: num(support.points), unknown: num(support.unknown) }
}

function str(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

function strings(value: unknown): string[] {
  return list(value).filter((entry): entry is string => typeof entry === 'string')
}

function tickRange(value: unknown): TickRange {
  const range = object(value)
  return { firstTick: num(range.firstTick), lastTick: num(range.lastTick) }
}

const RECORD_STREAMS = ['UNSPECIFIED', 'WORLD_EVENTS', 'CAPTURE_METADATA', 'CAPTURE_EVENTS', 'PERCEPTION'] as const

export function recordRef(value: unknown): RecordRef {
  const ref = object(value)
  return {
    connectionId: str(ref.connectionId),
    sequence: num(ref.sequence),
    serverTick: num(ref.serverTick),
    stream: enumName(ref.stream, 'RECORD_STREAM_', RECORD_STREAMS),
  }
}

function optionalRecordRef(value: unknown): RecordRef | undefined {
  return isRecord(value) ? recordRef(value) : undefined
}

const SNAPSHOT_REASONS = ['UNSPECIFIED', 'SESSION_START', 'LOADED', 'CHANGED'] as const
const CONTENTS_STATES = ['UNSPECIFIED', 'KNOWN', 'LOOT_UNGENERATED'] as const
const REMOVED_CAUSES = ['UNSPECIFIED', 'CHUNK_UNLOADED', 'DESTROYED'] as const

/** One `WorldEvent` line. Returns null for records this dashboard does not know. */
export function parseWorldEvent(value: unknown): null | WorldContainerEvent {
  const record = object(value)
  const identity = object(record.identity)
  const serverTick = num(identity.serverTick)
  const sequence = num(identity.sequence)
  if (isRecord(record.containerSnapshot)) {
    const snapshot = record.containerSnapshot
    const lootTable = str(snapshot.lootTable)
    return {
      blockEntityType: str(snapshot.blockEntityType),
      blockPos: blockPos(snapshot.blockPos),
      containerSize: num(snapshot.containerSize),
      contentsState: enumName(snapshot.contentsState, 'CONTENTS_STATE_', CONTENTS_STATES),
      dimension: str(snapshot.dimension),
      kind: 'snapshot',
      ...(lootTable ? { lootTable } : {}),
      reason: enumName(snapshot.reason, 'REASON_', SNAPSHOT_REASONS),
      sequence,
      serverTick,
      slots: list(snapshot.slots).map(itemStack),
    }
  }
  if (isRecord(record.containerRemoved)) {
    const removed = record.containerRemoved
    return {
      blockEntityType: str(removed.blockEntityType),
      blockPos: blockPos(removed.blockPos),
      cause: enumName(removed.cause, 'CAUSE_', REMOVED_CAUSES),
      dimension: str(removed.dimension),
      kind: 'removed',
      sequence,
      serverTick,
    }
  }
  return null
}

const ALIGNED_EVENT_KINDS = [
  'UNSPECIFIED',
  'WORLD_CONTAINER_SNAPSHOT',
  'WORLD_CONTAINER_REMOVED',
  'ACTOR_JOINED',
  'ACTOR_LEFT',
  'ACTOR_CONTAINER_VIEW',
  'ACTOR_CONTAINER_CLICK',
  'ACTOR_ENTITY_VISIBILITY',
  'ACTOR_BLOCK_ENTITY_VISIBILITY',
] as const
const CONTAINER_VIEW_KINDS = ['UNSPECIFIED', 'OPENED', 'CONTENTS', 'SLOT', 'CARRIED', 'CLOSED'] as const
const TARGET_VISIBILITIES = ['UNSPECIFIED', 'VISIBLE', 'NOT_VISIBLE', 'UNDETERMINED'] as const
const DIVERGENCE_ENDS = ['UNSPECIFIED', 'REOBSERVED', 'TRUTH_MATCHES', 'CONTAINER_REMOVED', 'TRUTH_UNDETERMINED', 'ACTOR_COVERAGE_END', 'WORLD_COVERAGE_END'] as const
const CO_PRESENCE_STATUSES = ['UNSPECIFIED', 'PERCEPTION_NOT_PROVIDED', 'NO_SAMPLE', 'SAMPLED'] as const

/** Join key of a container or block entity: dimension plus block position. */
export function containerKey(dimension: string, pos: BlockPos): string {
  return `${dimension}|${pos.x},${pos.y},${pos.z}`
}

/** One `SessionAlignmentRecord` line. Returns null for unknown record kinds. */
export function parseAlignmentRecord(value: unknown): AlignmentRecord | null {
  const record = object(value)
  if (isRecord(record.header)) {
    const header = record.header
    return {
      header: {
        assumptions: strings(header.assumptions),
        divergenceCount: num(header.divergenceCount),
        eventCount: num(header.eventCount),
        futureContext: str(header.futureContext),
        knownLimitations: strings(header.knownLimitations),
        participants: list(header.participants).map(participant),
        processor: processor(header.processor),
        provenance: str(header.provenance),
        scope: str(header.scope),
        sessionId: str(header.sessionId),
        usesFutureContext: header.usesFutureContext === true,
        worldCoverage: tickRange(header.worldCoverage),
      },
      kind: 'header',
    }
  }
  if (isRecord(record.event)) {
    const event = record.event
    return {
      event: {
        ...(isRecord(event.blockPos) || typeof event.dimension === 'string' ? { blockPos: blockPos(event.blockPos) } : {}),
        containerViewKind: enumName(event.containerViewKind, 'CONTAINER_VIEW_KIND_', CONTAINER_VIEW_KINDS),
        dimension: str(event.dimension),
        entityUuid: str(event.entityUuid),
        kind: enumName(event.kind, 'ALIGNED_EVENT_KIND_', ALIGNED_EVENT_KINDS),
        source: recordRef(event.source),
        typeId: str(event.typeId),
        visibility: enumName(event.visibility, 'TARGET_VISIBILITY_', TARGET_VISIBILITIES),
      },
      kind: 'event',
    }
  }
  if (isRecord(record.divergence)) {
    const divergence = record.divergence
    return {
      divergence: {
        blockPos: blockPos(divergence.blockPos),
        connectionId: str(divergence.connectionId),
        coPresence: coPresence(divergence.coPresence),
        dimension: str(divergence.dimension),
        end: enumName(divergence.end, 'DIVERGENCE_END_', DIVERGENCE_ENDS),
        ...(optionalRecordRef(divergence.endSource) ? { endSource: recordRef(divergence.endSource) } : {}),
        ...(divergence.endTick !== undefined ? { endTick: num(divergence.endTick) } : {}),
        lastTick: num(divergence.lastTick),
        observed: containerSlots(divergence.observed),
        startTick: num(divergence.startTick),
        truth: containerSlots(divergence.truth),
        truthChanges: num(divergence.truthChanges),
      },
      kind: 'divergence',
    }
  }
  return null
}

export function parsePerceptionHeader(value: unknown): PerceptionHeader {
  const header = object(value)
  const assumptions = object(header.assumptions)
  const camera = object(assumptions.camera)
  const occluder = object(assumptions.occluderModel)
  const sampling = object(assumptions.targetSampling)
  return {
    assumptions: {
      camera: {
        aspectRatio: num(camera.aspectRatio),
        horizontalFovDegrees: num(camera.horizontalFovDegrees),
        nearPlaneBlocks: num(camera.nearPlaneBlocks),
        verticalFovDegrees: num(camera.verticalFovDegrees),
      },
      defaultEyeHeight: num(assumptions.defaultEyeHeight),
      maxDistanceBlocks: num(assumptions.maxDistanceBlocks),
      maxDistanceCappedByViewDistance: assumptions.maxDistanceCappedByViewDistance === true,
      observerSource: str(assumptions.observerSource),
      occluderModel: { description: str(occluder.description), name: str(occluder.name) },
      samplingIntervalTicks: num(assumptions.samplingIntervalTicks),
      targetSampling: {
        blockEntityPoints: str(sampling.blockEntityPoints),
        entityPoints: str(sampling.entityPoints),
        visibilityRule: str(sampling.visibilityRule),
      },
      unknownCellPolicy: str(assumptions.unknownCellPolicy),
    },
    connectionId: str(header.connectionId),
    knownLimitations: strings(header.knownLimitations),
    playerUuid: str(header.playerUuid),
    processor: processor(header.processor),
    provenance: str(header.provenance),
    sampleCount: num(header.sampleCount),
    scope: str(header.scope),
    sessionId: str(header.sessionId),
    ticks: tickRange(header.ticks),
    usesFutureContext: header.usesFutureContext === true,
  }
}

/** One `PerceptionRecord` line. Returns null for unknown record kinds. */
export function parsePerceptionRecord(value: unknown): null | PerceptionRecord {
  const record = object(value)
  if (isRecord(record.header))
    return { header: parsePerceptionHeader(record.header), kind: 'header' }
  if (isRecord(record.sample)) {
    const sample = record.sample
    return {
      kind: 'sample',
      sample: {
        dimension: str(sample.dimension),
        serverTick: num(sample.serverTick),
        undeterminedBlockEntities: list(sample.undeterminedBlockEntities).map(perceivedBlockEntity),
        undeterminedEntities: list(sample.undeterminedEntities).map(perceivedEntity),
        visibleBlockEntities: list(sample.visibleBlockEntities).map(perceivedBlockEntity),
        visibleEntities: list(sample.visibleEntities).map(perceivedEntity),
      },
    }
  }
  return null
}

export function recordRefKey(ref: RecordRef): string {
  return `${ref.stream}|${ref.connectionId}|${ref.serverTick}|${ref.sequence}`
}

function containerSlots(value: unknown): ContainerSlots {
  const entry = object(value)
  return { slots: list(entry.slots).map(itemStack), sources: list(entry.sources).map(recordRef) }
}

function coPresence(value: unknown): CoPresence {
  const entry = object(value)
  return {
    container: enumName(entry.container, 'TARGET_VISIBILITY_', TARGET_VISIBILITIES),
    entities: list(entry.entities).map((raw) => {
      const entity = object(raw)
      const uuid = str(entity.uuid)
      return {
        participantConnectionId: str(entity.participantConnectionId),
        ...(typeof entity.participantContainerOpen === 'boolean' ? { participantContainerOpen: entity.participantContainerOpen } : {}),
        ...(isRecord(entity.participantMenu) ? { participantMenu: recordRef(entity.participantMenu) } : {}),
        typeId: str(entity.typeId),
        ...(uuid ? { uuid } : {}),
        visibility: enumName(entity.visibility, 'TARGET_VISIBILITY_', TARGET_VISIBILITIES),
      }
    }),
    ...(isRecord(entry.sample) ? { sample: recordRef(entry.sample) } : {}),
    status: enumName(entry.status, 'CO_PRESENCE_STATUS_', CO_PRESENCE_STATUSES),
  }
}

function participant(value: unknown): AlignmentParticipant {
  const entry = object(value)
  return {
    connectionId: str(entry.connectionId),
    coverage: tickRange(entry.coverage),
    observedContainerCount: num(entry.observedContainerCount),
    ...(isRecord(entry.perceptionCoverage) ? { perceptionCoverage: tickRange(entry.perceptionCoverage) } : {}),
    perceptionIntervalTicks: num(entry.perceptionIntervalTicks),
    playerName: str(entry.playerName),
    playerUuid: str(entry.playerUuid),
    terminalReason: str(entry.terminalReason),
    unobservedContainerCount: num(entry.unobservedContainerCount),
  }
}

function perceivedBlockEntity(value: unknown): PerceivedBlockEntity {
  const entity = object(value)
  return {
    blockPos: blockPos(entity.blockPos),
    dimension: str(entity.dimension),
    distance: num(entity.distance),
    support: raySupport(entity.support),
    typeId: str(entity.typeId),
  }
}

function perceivedEntity(value: unknown): PerceivedEntity {
  const entity = object(value)
  const uuid = str(entity.uuid)
  return {
    distance: num(entity.distance),
    instanceId: str(entity.instanceId),
    support: raySupport(entity.support),
    typeId: str(entity.typeId),
    ...(uuid ? { uuid } : {}),
  }
}

function processor(value: unknown): { name: string, version: string } {
  const entry = object(value)
  return { name: str(entry.name), version: str(entry.version) }
}
