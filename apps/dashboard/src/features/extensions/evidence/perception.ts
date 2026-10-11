import type { BlockPos, PerceivedBlockEntity, PerceivedEntity, PerceptionHeader, RaySupport } from './formats'

import { containerKey, parsePerceptionRecord } from './formats'

/** Compact interval form of a perception.jsonl; small enough to post from a worker. */
export interface PerceptionSummary {
  firstTick: number
  header: null | PerceptionHeader
  /** Sampling interval from the header; 0 when unknown. */
  intervalTicks: number
  lastTick: number
  sampleCount: number
  targets: VisibilityTarget[]
}

/** Consecutive samples in which one target had the same visibility state. */
export interface VisibilityRun {
  /** Last sampled tick of the run (inclusive). */
  lastTick: number
  maxDistance: number
  minDistance: number
  samples: number
  startTick: number
  state: VisibilityState
  /** Ray outcomes summed over the run's samples. */
  support: RaySupport
}

export type VisibilityState = 'undetermined' | 'visible'

export interface VisibilityTarget {
  blockPos?: BlockPos
  dimension?: string
  /** `entity:<uuid or instance id>` or `block:<dimension>|<x>,<y>,<z>`. */
  key: string
  kind: 'block-entity' | 'entity'
  runs: VisibilityRun[]
  typeId: string
  undeterminedSamples: number
  uuid?: string
  visibleSamples: number
}

interface OpenRun {
  run: VisibilityRun
  target: VisibilityTarget
}

/**
 * Folds perception samples into visibility runs per target. A run breaks when the state changes,
 * when the target is absent from a sample (determined not visible), or when samples are further
 * apart than the sampling interval.
 */
export class PerceptionSummarizer {
  private firstTick = 0
  private header: null | PerceptionHeader = null
  private lastTick = 0
  private readonly open = new Map<string, OpenRun>()
  private previousTick: null | number = null
  private sampleCount = 0
  private readonly targets = new Map<string, VisibilityTarget>()

  finish(): PerceptionSummary {
    this.open.clear()
    return {
      firstTick: this.firstTick,
      header: this.header,
      intervalTicks: this.header?.assumptions.samplingIntervalTicks ?? 0,
      lastTick: this.lastTick,
      sampleCount: this.sampleCount,
      targets: [...this.targets.values()],
    }
  }

  push(raw: unknown): void {
    const record = parsePerceptionRecord(raw)
    if (!record)
      return
    if (record.kind === 'header') {
      this.header = record.header
      return
    }

    const sample = record.sample
    const tick = sample.serverTick
    const interval = this.header?.assumptions.samplingIntervalTicks || 1
    const contiguous = this.previousTick !== null && tick - this.previousTick <= interval
    if (this.sampleCount === 0)
      this.firstTick = tick
    this.sampleCount += 1
    this.lastTick = tick

    const seen = new Set<string>()
    for (const entity of sample.visibleEntities)
      this.observe(seen, this.entityTarget(entity), 'visible', tick, contiguous, entity.support, entity.distance)
    for (const entity of sample.undeterminedEntities)
      this.observe(seen, this.entityTarget(entity), 'undetermined', tick, contiguous, entity.support, entity.distance)
    for (const block of sample.visibleBlockEntities)
      this.observe(seen, this.blockTarget(block), 'visible', tick, contiguous, block.support, block.distance)
    for (const block of sample.undeterminedBlockEntities)
      this.observe(seen, this.blockTarget(block), 'undetermined', tick, contiguous, block.support, block.distance)

    for (const key of this.open.keys()) {
      if (!seen.has(key))
        this.open.delete(key)
    }
    this.previousTick = tick
  }

  private blockTarget(block: PerceivedBlockEntity): VisibilityTarget {
    const key = blockTargetKey(block.dimension, block.blockPos)
    let target = this.targets.get(key)
    if (!target) {
      target = { blockPos: block.blockPos, dimension: block.dimension, key, kind: 'block-entity', runs: [], typeId: block.typeId, undeterminedSamples: 0, visibleSamples: 0 }
      this.targets.set(key, target)
    }
    return target
  }

  private entityTarget(entity: PerceivedEntity): VisibilityTarget {
    const key = entityTargetKey(entity)
    let target = this.targets.get(key)
    if (!target) {
      target = { key, kind: 'entity', runs: [], typeId: entity.typeId, undeterminedSamples: 0, visibleSamples: 0, ...(entity.uuid ? { uuid: entity.uuid } : {}) }
      this.targets.set(key, target)
    }
    return target
  }

  private observe(seen: Set<string>, target: VisibilityTarget, state: VisibilityState, tick: number, contiguous: boolean, support: RaySupport, distance: number): void {
    if (seen.has(target.key))
      return
    seen.add(target.key)
    if (state === 'visible')
      target.visibleSamples += 1
    else
      target.undeterminedSamples += 1

    const open = this.open.get(target.key)
    if (open && contiguous && open.run.state === state) {
      const run = open.run
      run.lastTick = tick
      run.samples += 1
      run.minDistance = Math.min(run.minDistance, distance)
      run.maxDistance = Math.max(run.maxDistance, distance)
      addSupport(run.support, support)
      return
    }

    const run: VisibilityRun = {
      lastTick: tick,
      maxDistance: distance,
      minDistance: distance,
      samples: 1,
      startTick: tick,
      state,
      support: { ...support },
    }
    target.runs.push(run)
    this.open.set(target.key, { run, target })
  }
}

export function blockTargetKey(dimension: string, pos: BlockPos): string {
  return `block:${containerKey(dimension, pos)}`
}

export function entityTargetKey(entity: { instanceId?: string, uuid?: string }): string {
  return `entity:${entity.uuid || entity.instanceId || 'unknown'}`
}

/** Summarizes already parsed lines; the worker uses the streaming form. */
export function summarizePerception(records: Iterable<unknown>): PerceptionSummary {
  const summarizer = new PerceptionSummarizer()
  for (const record of records)
    summarizer.push(record)
  return summarizer.finish()
}

function addSupport(total: RaySupport, next: RaySupport): void {
  total.points += next.points
  total.inView += next.inView
  total.clear += next.clear
  total.blocked += next.blocked
  total.unknown += next.unknown
}
