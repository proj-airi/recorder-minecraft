import type { RecorderMinecraftApiV1WorldSession } from '@proj-airi/recorder-minecraft-api'
import type { InjectionKey, ShallowRef } from 'vue'

import type {
  AlignedEvent,
  AlignmentHeader,
  AlignmentParticipant,
  BlockPos,
  ContainerDivergence,
  WorldContainerEvent,
} from './formats'
import type { EvidenceFetch } from './jsonl'
import type { PerceptionSummary } from './perception'
import type { PerceptionWorkerRequest, PerceptionWorkerResponse } from './perceptionWorkerProtocol'

import { worldSessionsGet } from '@proj-airi/recorder-minecraft-api'
import { shallowRef } from 'vue'

import { containerKey, num, parseAlignmentRecord, parseWorldEvent } from './formats'
import { errorText, EvidenceFileMissingError, isMissingFile, readJsonl } from './jsonl'
import { PerceptionSummarizer } from './perception'

/** A container or block entity named anywhere in an alignment file. */
export interface AlignmentContainer {
  blockPos: BlockPos
  dimension: string
  key: string
  /** Block entity type when some record names it, else empty. */
  typeId: string
}

/** Parsed session-alignment.jsonl with lookup tables. */
export interface AlignmentIndex {
  containers: ReadonlyMap<string, AlignmentContainer>
  divergences: readonly ContainerDivergence[]
  events: readonly AlignedEvent[]
  header: AlignmentHeader | null
  name: string
  participantsByConnection: ReadonlyMap<string, AlignmentParticipant>
  participantsByUuid: ReadonlyMap<string, AlignmentParticipant>
  url: string
}

export interface EvidenceSourcesOptions {
  fetch?: EvidenceFetch
  /** Loads a world session from the catalog; null when it does not exist. */
  getWorldSession?: (ref: WorldSessionRef) => Promise<null | RecorderMinecraftApiV1WorldSession>
  /** Parses one perception.jsonl. Defaults to a Web Worker, or the main thread without one. */
  loadPerception?: (url: string) => Promise<PerceptionSummary>
}

/** The parts of a world session the evidence tracks read. */
export interface EvidenceWorldSession {
  alignments: readonly { name: string, url: string }[]
  endServerTick?: number
  eventsUrl?: string
  id?: string
  startServerTick?: number
}

export type ResourceState<T>
  = | { error: string, status: 'error' }
    | { status: 'loading' }
    | { status: 'missing' }
    | { status: 'ready', value: T }

export interface WorldSessionRef {
  serverInstanceId: string
  worldSessionId: string
}

/**
 * Per-URL cache of every evidence file. Each file is fetched and parsed once per page; later
 * tracks and the details view share the parsed value. `version` changes whenever a load
 * settles, so synchronous `describe` calls that peek at a state re-run when it arrives.
 */
export class EvidenceSources {
  readonly version: Readonly<ShallowRef<number>>
  private readonly counter = shallowRef(0)
  private readonly fetcher: EvidenceFetch
  private readonly getWorldSessionFn: (ref: WorldSessionRef) => Promise<null | RecorderMinecraftApiV1WorldSession>
  private readonly perceptionFn: (url: string) => Promise<PerceptionSummary>
  private readonly promises = new Map<string, Promise<unknown>>()
  private readonly states = new Map<string, ResourceState<unknown>>()

  constructor(options: EvidenceSourcesOptions = {}) {
    this.version = this.counter
    this.fetcher = options.fetch ?? ((input, init) => fetch(input, init))
    this.getWorldSessionFn = options.getWorldSession ?? defaultGetWorldSession
    // A worker cannot call a custom fetch, so a custom fetch (tests) parses on the main thread.
    this.perceptionFn = options.loadPerception
      ?? (options.fetch ? url => loadPerceptionOnMainThread(url, this.fetcher) : loadPerceptionInWorker)
  }

  /** Parsed alignment file; null when the file does not exist. */
  alignment(url: string, name = 'session-alignment'): Promise<AlignmentIndex | null> {
    return this.resource(`alignment:${url}`, () => loadAlignment(url, name, this.fetcher))
  }

  alignmentState(url: string, name?: string): ResourceState<AlignmentIndex> {
    return this.state(`alignment:${url}`, () => this.alignment(url, name))
  }

  /** Forgets every cached file, e.g. after a catalog refresh. */
  clear(): void {
    this.promises.clear()
    this.states.clear()
    this.counter.value += 1
  }

  /** Reactive peek without starting a load. */
  peekPerception(url: string): ResourceState<PerceptionSummary> | undefined {
    void this.counter.value
    return this.states.get(`perception:${url}`) as ResourceState<PerceptionSummary> | undefined
  }

  perception(url: string): Promise<null | PerceptionSummary> {
    return this.resource(`perception:${url}`, () => this.perceptionFn(url))
  }

  perceptionState(url: string): ResourceState<PerceptionSummary> {
    return this.state(`perception:${url}`, () => this.perception(url))
  }

  worldEvents(url: string): Promise<null | readonly WorldContainerEvent[]> {
    return this.resource(`world-events:${url}`, () => loadWorldEvents(url, this.fetcher))
  }

  worldSession(ref: WorldSessionRef): Promise<EvidenceWorldSession | null> {
    return this.resource(`world-session:${ref.serverInstanceId}/${ref.worldSessionId}`, async () => {
      const session = await this.getWorldSessionFn(ref)
      if (!session)
        throw new EvidenceFileMissingError(ref.worldSessionId)
      return toEvidenceWorldSession(session)
    })
  }

  worldSessionState(ref: WorldSessionRef): ResourceState<EvidenceWorldSession> {
    return this.state(`world-session:${ref.serverInstanceId}/${ref.worldSessionId}`, () => this.worldSession(ref))
  }

  private resource<T>(key: string, load: () => Promise<T>): Promise<null | T> {
    const existing = this.promises.get(key)
    if (existing)
      return existing as Promise<null | T>

    this.states.set(key, { status: 'loading' })
    const promise = load().then((value) => {
      this.settle(key, { status: 'ready', value })
      return value
    }, (caught: unknown) => {
      if (isMissingFile(caught)) {
        this.settle(key, { status: 'missing' })
        return null
      }
      this.settle(key, { error: errorText(caught), status: 'error' })
      // A failed load may succeed later (server restart); retry on the next request.
      this.promises.delete(key)
      throw caught
    })
    this.promises.set(key, promise)
    return promise
  }

  private settle(key: string, state: ResourceState<unknown>): void {
    this.states.set(key, state)
    this.counter.value += 1
  }

  /** Reactive state of a resource. The first call starts the load. */
  private state<T>(key: string, start: () => Promise<unknown>): ResourceState<T> {
    void this.counter.value
    const current = this.states.get(key)
    if (current && (current.status !== 'error' || this.promises.has(key)))
      return current as ResourceState<T>
    if (!current)
      start().catch(() => {})
    return (this.states.get(key) ?? { status: 'loading' }) as ResourceState<T>
  }
}

export async function loadAlignment(url: string, name: string, fetcher?: EvidenceFetch): Promise<AlignmentIndex> {
  let header: AlignmentHeader | null = null
  const events: AlignedEvent[] = []
  const divergences: ContainerDivergence[] = []
  const containers = new Map<string, AlignmentContainer>()

  function noteContainer(dimension: string, pos: BlockPos | undefined, typeId = ''): void {
    if (!pos || !dimension)
      return
    const key = containerKey(dimension, pos)
    const existing = containers.get(key)
    if (!existing)
      containers.set(key, { blockPos: pos, dimension, key, typeId })
    else if (!existing.typeId && typeId)
      containers.set(key, { ...existing, typeId })
  }

  await readJsonl(url, (raw) => {
    const record = parseAlignmentRecord(raw)
    if (!record)
      return
    if (record.kind === 'header') {
      header = record.header
    }
    else if (record.kind === 'event') {
      events.push(record.event)
      if (record.event.kind !== 'ACTOR_ENTITY_VISIBILITY')
        noteContainer(record.event.dimension, record.event.blockPos, record.event.kind === 'ACTOR_BLOCK_ENTITY_VISIBILITY' ? record.event.typeId : '')
    }
    else {
      divergences.push(record.divergence)
      noteContainer(record.divergence.dimension, record.divergence.blockPos)
    }
  }, { fetch: fetcher, yieldEvery: 5000 })

  const participants = (header as AlignmentHeader | null)?.participants ?? []
  return {
    containers,
    divergences,
    events,
    header,
    name,
    participantsByConnection: new Map(participants.map(participant => [participant.connectionId, participant])),
    participantsByUuid: new Map(participants.map(participant => [participant.playerUuid, participant])),
    url,
  }
}

/** Main-thread fallback: streams the file and yields to the event loop every 2000 lines. */
export async function loadPerceptionOnMainThread(url: string, fetcher?: EvidenceFetch): Promise<PerceptionSummary> {
  const summarizer = new PerceptionSummarizer()
  await readJsonl(url, record => summarizer.push(record), { fetch: fetcher, yieldEvery: 2000 })
  return summarizer.finish()
}

export async function loadWorldEvents(url: string, fetcher?: EvidenceFetch): Promise<WorldContainerEvent[]> {
  const events: WorldContainerEvent[] = []
  await readJsonl(url, (raw) => {
    const event = parseWorldEvent(raw)
    if (event)
      events.push(event)
  }, { fetch: fetcher, yieldEvery: 5000 })
  return events
}

export function toEvidenceWorldSession(session: RecorderMinecraftApiV1WorldSession): EvidenceWorldSession {
  return {
    alignments: (session.alignments ?? [])
      .filter(alignment => alignment.url && !alignment.validationError)
      .map(alignment => ({ name: alignment.name ?? 'alignment', url: alignment.url! })),
    ...(session.endServerTick !== undefined ? { endServerTick: num(session.endServerTick) } : {}),
    ...(session.eventsUrl ? { eventsUrl: session.eventsUrl } : {}),
    ...(session.id ? { id: session.id } : {}),
    ...(session.startServerTick !== undefined ? { startServerTick: num(session.startServerTick) } : {}),
  }
}

async function defaultGetWorldSession(ref: WorldSessionRef): Promise<null | RecorderMinecraftApiV1WorldSession> {
  const { data, error, response } = await worldSessionsGet({ path: ref, throwOnError: false })
  if (response?.status === 404)
    return null
  if (error || !data)
    throw new Error(`World session ${ref.worldSessionId} could not be loaded (status ${response?.status ?? 'unknown'})`)
  return data.worldSession ?? null
}

let perceptionWorker: null | Worker = null
let workerUnavailable = false
let nextRequestId = 1
const pendingWorkerRequests = new Map<number, { reject: (error: Error) => void, resolve: (summary: PerceptionSummary) => void }>()

/** Parses a perception.jsonl in a Web Worker, or on the main thread when workers are unavailable. */
export async function loadPerceptionInWorker(url: string): Promise<PerceptionSummary> {
  const worker = sharedPerceptionWorker()
  if (!worker)
    return loadPerceptionOnMainThread(url)

  const id = nextRequestId++
  const absolute = new URL(url, globalThis.location?.href).href
  try {
    return await new Promise<PerceptionSummary>((resolve, reject) => {
      pendingWorkerRequests.set(id, { reject, resolve })
      const request: PerceptionWorkerRequest = { id, url: absolute }
      worker.postMessage(request)
    })
  }
  catch (caught) {
    if (caught instanceof Error && caught.message === 'worker-failed')
      return loadPerceptionOnMainThread(url)
    throw caught
  }
}

function sharedPerceptionWorker(): null | Worker {
  if (workerUnavailable || typeof Worker === 'undefined')
    return null
  if (perceptionWorker)
    return perceptionWorker
  try {
    const worker = new Worker(new URL('./perception.worker.ts', import.meta.url), { name: 'evidence-perception', type: 'module' })
    worker.addEventListener('message', (event: MessageEvent<PerceptionWorkerResponse>) => {
      const response = event.data
      const pending = pendingWorkerRequests.get(response.id)
      if (!pending)
        return
      pendingWorkerRequests.delete(response.id)
      if (response.type === 'done')
        pending.resolve(response.summary)
      else
        pending.reject(response.missing ? new EvidenceFileMissingError(response.message) : new Error(response.message))
    })
    worker.addEventListener('error', () => {
      // The worker failed to start (or crashed): reject what it held and stop using it.
      workerUnavailable = true
      perceptionWorker = null
      for (const pending of pendingWorkerRequests.values())
        pending.reject(new Error('worker-failed'))
      pendingWorkerRequests.clear()
    })
    perceptionWorker = worker
    return worker
  }
  catch {
    workerUnavailable = true
    return null
  }
}

/** The shared cache used by the registered providers and the details view. */
export const evidenceSources = new EvidenceSources()

/** Lets tests give the details view the same cache as their providers. */
export const evidenceSourcesKey: InjectionKey<EvidenceSources> = Symbol('evidence-sources')
