import type { PerceptionSummary } from './perception'

export interface PerceptionWorkerRequest {
  id: number
  /** Absolute URL; the worker has no page base URL. */
  url: string
}

export type PerceptionWorkerResponse
  = | { id: number, message: string, missing: boolean, type: 'error' }
    | { id: number, summary: PerceptionSummary, type: 'done' }
