import type { AlignedEvent, ContainerDivergence, PerceptionHeader, WorldContainerEvent } from './formats'
import type { VisibilityRun, VisibilityTarget } from './perception'
import type { AlignmentIndex } from './sources'

export const EVIDENCE_VIEW_ID = 'extension:evidence'
export const EVIDENCE_SESSION_PROVIDER_ID = 'evidence.session'
export const EVIDENCE_PLAYER_PROVIDER_ID = 'evidence.player'

export interface DivergencePayload {
  actorName: string
  alignment: AlignmentIndex
  containerLabel: string
  divergence: ContainerDivergence
  evidence: 'divergence'
  /** perception.jsonl of the actor's Play, when the catalog lists one. */
  perceptionUrl?: string
}

export type EvidencePayload = DivergencePayload | InteractionPayload | VisibilityPayload | WorldEventPayload

export interface InteractionPayload {
  actorName: string
  alignment: AlignmentIndex
  containerLabel: string
  event: AlignedEvent
  evidence: 'interaction'
}

export interface VisibilityPayload {
  actorName: string
  connectionId: string
  evidence: 'visibility'
  header: null | PerceptionHeader
  intervalTicks: number
  perceptionUrl: string
  run: VisibilityRun
  target: Omit<VisibilityTarget, 'runs'>
  targetLabel: string
}

export interface WorldEventPayload {
  containerLabel: string
  event: WorldContainerEvent
  evidence: 'world-event'
  worldEventsUrl: string
}

export function isEvidencePayload(value: unknown): value is EvidencePayload {
  return typeof value === 'object' && value !== null && 'evidence' in value
    && ['divergence', 'interaction', 'visibility', 'world-event'].includes((value as { evidence: unknown }).evidence as string)
}

export function isEvidenceProvider(providerId: string): boolean {
  return providerId === EVIDENCE_SESSION_PROVIDER_ID || providerId === EVIDENCE_PLAYER_PROVIDER_ID
}
