import type { ComputedRef, Ref } from 'vue'

import type { ClickActions, ControlStateSample } from '../../event-log/captureEvents'

import { computed } from 'vue'

import { clickActions, controlSample, latestAtOrBefore } from '../../event-log/captureEvents'
import { useCaptureEvents } from '../../event-log/useCaptureEvents'

export type { ControlStateSample } from '../../event-log/captureEvents'

export interface ReplayControlSource {
  eventsUrl?: string
}

export interface ReplayControlState {
  current: ComputedRef<ControlStateSample | null>
  error: ComputedRef<null | string>
  isLoading: ComputedRef<boolean>
  sampleCount: ComputedRef<number>
}

/** Mouse buttons implied by one applied-packet line, for tests and one-off parsing. */
export function parseClickActionsLine(line: string): null | { actions: ClickActions, serverTick: number } {
  const event = parseLine(line)
  const packet = objectAt(objectAt(event, 'packetApply'), 'packet')
  const serverTick = Number(objectAt(event, 'identity')?.serverTick)
  if (!packet || !Number.isFinite(serverTick))
    return null
  const actions = clickActions(packet)
  return actions ? { actions, serverTick } : null
}

/** Control-state sample of one line, for tests and one-off parsing. */
export function parseControlStateLine(line: string): ControlStateSample | null {
  const event = parseLine(line)
  const state = objectAt(objectAt(event, 'controlState'), 'state')
  const serverTick = Number(objectAt(event, 'identity')?.serverTick)
  if (!state || !Number.isFinite(serverTick))
    return null
  return controlSample(state, serverTick)
}

/** The sample at or before `serverTick`; the first sample when every sample is later. */
export function sampleAtOrBefore(samples: readonly ControlStateSample[], serverTick: number): ControlStateSample {
  return latestAtOrBefore(samples, serverTick) ?? samples[0]!
}

/**
 * Control state of one Play at `serverTick`, read from the shared parsed events (see
 * `features/event-log/useCaptureEvents.ts`), so the file is fetched and parsed once for all panels.
 *
 * `serverTick` must already be mapped from the playhead through the Play placement, e.g. with
 * `playServerTickAt(placement, playheadTick)`, so trims and moves are respected.
 */
export function useReplayControlState(
  replay: Readonly<Ref<null | ReplayControlSource>>,
  serverTick: Readonly<Ref<null | number>>,
): ReplayControlState {
  const handle = useCaptureEvents(() => replay.value?.eventsUrl)
  const parsed = computed(() => handle.value?.data.value ?? null)

  return {
    current: computed(() => {
      const samples = parsed.value?.controlSamples ?? []
      if (samples.length === 0)
        return null
      const tick = serverTick.value ?? samples[0]!.serverTick
      const sample = sampleAtOrBefore(samples, tick)
      const clicks = parsed.value?.clicksByTick.get(tick)
      return clicks ? { ...sample, ...clicks } : sample
    }),
    error: computed(() => handle.value?.status.value === 'error' ? handle.value.error.value : null),
    isLoading: computed(() => handle.value?.status.value === 'loading'),
    sampleCount: computed(() => parsed.value?.controlSamples.length ?? 0),
  }
}

function objectAt(value: null | Record<string, unknown> | undefined, key: string): null | Record<string, unknown> {
  const field = value?.[key]
  return typeof field === 'object' && field !== null && !Array.isArray(field) ? field as Record<string, unknown> : null
}

function parseLine(line: string): null | Record<string, unknown> {
  try {
    const parsed = JSON.parse(line) as unknown
    return typeof parsed === 'object' && parsed !== null ? parsed as Record<string, unknown> : null
  }
  catch {
    return null
  }
}
