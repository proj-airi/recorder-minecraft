import type { MaybeRefOrGetter, ShallowRef } from 'vue'

import type { ParsedCaptureEvents } from './captureEvents'

import { computed, onScopeDispose, shallowRef, toValue, watch } from 'vue'

import { streamCaptureEvents } from './captureEvents'

/**
 * Shared, cached parse of one `events.jsonl`. The refs are module-level, so every panel that asks
 * for the same URL reads the same parse, and the file is fetched and parsed once.
 */
export interface CaptureEventsHandle {
  data: Readonly<ShallowRef<null | ParsedCaptureEvents>>
  error: Readonly<ShallowRef<null | string>>
  /** Lines parsed so far while loading. */
  progress: Readonly<ShallowRef<number>>
  reload: () => void
  status: Readonly<ShallowRef<CaptureEventsStatus>>
  url: string
}

export type CaptureEventsStatus = 'error' | 'loading' | 'ready'

interface CacheEntry {
  controller: AbortController
  data: ShallowRef<null | ParsedCaptureEvents>
  error: ShallowRef<null | string>
  handle: CaptureEventsHandle
  lastUsed: number
  progress: ShallowRef<number>
  status: ShallowRef<CaptureEventsStatus>
  users: number
}

/** Parsed Plays kept after no panel uses them any more. */
const IDLE_CACHE_LIMIT = 6
const cache = new Map<string, CacheEntry>()

/**
 * Returns the shared handle for an events URL and starts loading it if needed. The handle is not
 * retained: an idle entry may be evicted later. Components should use the composables below.
 */
export function captureEventsFor(url: string): CaptureEventsHandle {
  return entryFor(url).handle
}

/** Drops every cached parse. For tests. */
export function clearCaptureEventsCache(): void {
  for (const entry of cache.values())
    entry.controller.abort()
  cache.clear()
}

/** Handle for one reactive events URL, or null without a URL. */
export function useCaptureEvents(url: MaybeRefOrGetter<string | undefined>) {
  const sources = useCaptureEventSources(() => [toValue(url)])
  return computed(() => {
    const current = toValue(url)
    return current ? sources.value.get(current) ?? null : null
  })
}

/**
 * Handles for a reactive list of events URLs. Handles stay cached while this scope uses them.
 * Duplicate and empty URLs are ignored.
 */
export function useCaptureEventSources(urls: MaybeRefOrGetter<readonly (string | undefined)[]>) {
  const retained = new Set<string>()
  const wanted = computed(() => Array.from(new Set(toValue(urls).filter((url): url is string => Boolean(url)))))

  watch(wanted, (next) => {
    const nextSet = new Set(next)
    for (const url of next) {
      if (!retained.has(url)) {
        retained.add(url)
        retain(url)
      }
    }
    for (const url of Array.from(retained)) {
      if (!nextSet.has(url)) {
        retained.delete(url)
        release(url)
      }
    }
  }, { immediate: true })

  onScopeDispose(() => {
    for (const url of retained)
      release(url)
    retained.clear()
  })

  return computed(() => new Map(wanted.value.map(url => [url, entryFor(url).handle] as const)) as ReadonlyMap<string, CaptureEventsHandle>)
}

function entryFor(url: string): CacheEntry {
  const existing = cache.get(url)
  if (existing) {
    existing.lastUsed = performance.now()
    return existing
  }

  const data = shallowRef<null | ParsedCaptureEvents>(null)
  const error = shallowRef<null | string>(null)
  const progress = shallowRef(0)
  const status = shallowRef<CaptureEventsStatus>('loading')
  const entry: CacheEntry = {
    controller: new AbortController(),
    data,
    error,
    handle: { data, error, progress, reload: () => start(entry, url), status, url },
    lastUsed: performance.now(),
    progress,
    status,
    users: 0,
  }
  cache.set(url, entry)
  start(entry, url)
  return entry
}

function errorMessage(value: unknown): string {
  if (value && typeof value === 'object' && 'message' in value && typeof value.message === 'string')
    return value.message
  return String(value)
}

function evictIdle(): void {
  const idle = Array.from(cache.entries())
    .filter(([, entry]) => entry.users === 0)
    .sort(([, left], [, right]) => left.lastUsed - right.lastUsed)
  for (const [url, entry] of idle.slice(0, Math.max(0, idle.length - IDLE_CACHE_LIMIT))) {
    entry.controller.abort()
    cache.delete(url)
  }
}

function release(url: string): void {
  const entry = cache.get(url)
  if (!entry)
    return
  entry.users = Math.max(0, entry.users - 1)
  entry.lastUsed = performance.now()
  evictIdle()
}

function retain(url: string): void {
  entryFor(url).users += 1
}

function start(entry: CacheEntry, url: string): void {
  entry.controller.abort()
  const controller = new AbortController()
  entry.controller = controller
  entry.status.value = 'loading'
  entry.error.value = null
  entry.progress.value = 0
  void (async () => {
    try {
      const response = await fetch(new URL(url, window.location.href), { signal: controller.signal })
      if (!response.ok)
        throw new Error(`Events request failed with HTTP ${response.status}.`)
      const parsed = await streamCaptureEvents(response, {
        onProgress: (lines) => {
          entry.progress.value = lines
        },
        signal: controller.signal,
      })
      if (controller.signal.aborted)
        return
      entry.data.value = parsed
      entry.progress.value = parsed.lineCount
      entry.status.value = 'ready'
    }
    catch (caught) {
      if (controller.signal.aborted)
        return
      entry.error.value = errorMessage(caught)
      entry.status.value = 'error'
    }
  })()
}
