import type { RecorderMinecraftApiV1PlayExtension } from '@proj-airi/recorder-minecraft-api'

import type { NormalizedTimelineItem } from '../../timeline/domain'
import type { ExtensionAssetAccess, ExtensionTrackProjection, PlayExtensionModule } from '../domain'

import PlannerView from './PlannerView.vue'

import { AIRICRAFT_PLANNER_SCHEMA, AIRICRAFT_PLANNER_TYPE } from './constants'

export { AIRICRAFT_PLANNER_SCHEMA, AIRICRAFT_PLANNER_TYPE } from './constants'

export interface PlannerCallRecord {
  callId: string
  model?: {
    name?: string
    provider?: string
  }
  outcome?: {
    assistantContent?: unknown
    failure?: {
      message?: string
      type?: string
    }
    status?: string
    toolCalls?: Record<string, unknown>[]
    usage?: Record<string, unknown>
  }
  plannerAttempt?: {
    attempt?: number
    generation?: string
    phase?: string
  }
  request?: {
    messages?: Record<string, unknown>[]
    tools?: Record<string, unknown>[]
  }
  schemaVersion?: number
  sequence: string
  timeline: {
    applied?: PlannerAnchor
    completed?: PlannerAnchor
    submitted: PlannerAnchor
  }
  timing?: {
    completedAtUnixMs?: string
    latencyMs?: string
    requestedAtUnixMs?: string
  }
  turnId?: string
}

interface PlannerAnchor {
  basis?: string
  eventSequence?: string
  serverTick: string
}

export const airicraftPlannerModule: PlayExtensionModule = {
  extensionType: AIRICRAFT_PLANNER_TYPE,
  loadTrack: loadPlannerTrack,
  view: {
    component: PlannerView,
    icon: 'i-mingcute-ai-line',
    label: 'Planner',
  },
}

export function parsePlannerCalls(source: string): PlannerCallRecord[] {
  return source
    .split(/\r?\n/)
    .filter(line => line.trim().length > 0)
    .map((line, index) => parsePlannerCall(line, index + 1))
}

function isAnchor(value: unknown): value is PlannerAnchor {
  return isRecord(value) && typeof value.serverTick === 'string'
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

async function loadPlannerTrack(
  descriptor: RecorderMinecraftApiV1PlayExtension,
  access: ExtensionAssetAccess,
): Promise<ExtensionTrackProjection> {
  const asset = descriptor.assets?.find(candidate =>
    candidate.role === 'planner_calls'
    && candidate.mediaType === 'application/x-ndjson'
    && candidate.schema === AIRICRAFT_PLANNER_SCHEMA,
  )
  if (!asset)
    throw new Error(`The ${AIRICRAFT_PLANNER_TYPE} planner_calls asset is missing or unsupported`)

  const records = parsePlannerCalls(await access.text(asset))
  return {
    descriptor,
    items: records.flatMap(plannerItems),
    label: 'Airicraft planner',
  }
}

function parsePlannerCall(line: string, lineNumber: number): PlannerCallRecord {
  const value: unknown = JSON.parse(line)
  if (!isRecord(value)
    || typeof value.callId !== 'string'
    || typeof value.sequence !== 'string'
    || !isRecord(value.timeline)
    || !isAnchor(value.timeline.submitted)
    || (value.timeline.completed !== undefined && !isAnchor(value.timeline.completed))
    || (value.timeline.applied !== undefined && !isAnchor(value.timeline.applied))) {
    throw new Error(`Planner call line ${lineNumber} does not match ${AIRICRAFT_PLANNER_SCHEMA}`)
  }
  return value as unknown as PlannerCallRecord
}

function plannerItems(record: PlannerCallRecord): NormalizedTimelineItem[] {
  const submittedTick = serverTick(record.timeline.submitted)
  const completedTick = record.timeline.completed ? serverTick(record.timeline.completed) : undefined
  const status = record.outcome?.status
  const label = [`Call ${record.sequence}`, status].filter(Boolean).join(' · ')
  const call: NormalizedTimelineItem = completedTick === undefined
    ? { color: '#a78bfa', data: record, id: record.callId, kind: 'point', label, serverTick: submittedTick }
    : {
        color: '#8b5cf6',
        data: record,
        endServerTick: Math.max(submittedTick, completedTick),
        id: record.callId,
        kind: 'interval',
        label,
        startServerTick: submittedTick,
      }
  const applied = record.timeline.applied
  if (!applied)
    return [call]

  return [
    call,
    {
      color: '#f59e0b',
      data: record,
      id: `${record.callId}:applied`,
      kind: 'point',
      label: `Applied ${record.sequence}`,
      serverTick: serverTick(applied),
    },
  ]
}

function serverTick(anchor: PlannerAnchor): number {
  const value = Number(anchor.serverTick)
  if (!Number.isSafeInteger(value))
    throw new Error(`Planner Server tick is not a safe decimal integer: ${anchor.serverTick}`)
  return value
}
