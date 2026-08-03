import type { RecorderMinecraftApiV1Replay } from '@proj-airi/recorder-minecraft-api'
import type { ComputedRef, Ref, ShallowRef } from 'vue'

import { computed, onScopeDispose, shallowReadonly, shallowRef, watch } from 'vue'

export interface ControlStateSample {
  backward: boolean
  cameraDeltaPitch: number
  cameraDeltaYaw: number
  cameraPitch: number
  cameraYaw: number
  forward: boolean
  jump: boolean
  left: boolean
  right: boolean
  selectedSlot: number
  serverTick: number
  sneak: boolean
  sprint: boolean
}

interface ReplayControlState {
  current: ComputedRef<ControlStateSample | null>
  error: Readonly<ShallowRef<null | string>>
  isLoading: Readonly<ShallowRef<boolean>>
  sampleCount: ComputedRef<number>
}

export function parseControlStateLine(line: string): ControlStateSample | null {
  if (!line.includes('"controlState"'))
    return null
  try {
    const event = JSON.parse(line) as {
      controlState?: { state?: Record<string, unknown> }
      identity?: { serverTick?: unknown }
    }
    const state = event.controlState?.state
    const serverTick = Number(event.identity?.serverTick)
    if (!state || !Number.isFinite(serverTick))
      return null
    return {
      backward: state.backward === true,
      cameraDeltaPitch: finiteNumber(state.cameraDeltaPitch),
      cameraDeltaYaw: finiteNumber(state.cameraDeltaYaw),
      cameraPitch: finiteNumber(state.cameraPitch),
      cameraYaw: finiteNumber(state.cameraYaw),
      forward: state.forward === true,
      jump: state.jump === true,
      left: state.left === true,
      right: state.right === true,
      selectedSlot: finiteNumber(state.selectedSlot),
      serverTick,
      sneak: state.sneak === true,
      sprint: state.sprint === true,
    }
  }
  catch {
    return null
  }
}

export function sampleAtOrBefore(samples: ControlStateSample[], serverTick: number): ControlStateSample {
  let low = 0
  let high = samples.length - 1
  while (low <= high) {
    const middle = (low + high) >> 1
    if (samples[middle]!.serverTick <= serverTick)
      low = middle + 1
    else
      high = middle - 1
  }
  return samples[Math.max(0, high)]!
}

export function useReplayControlState(
  replay: Readonly<Ref<null | RecorderMinecraftApiV1Replay>>,
  playheadTick: Readonly<Ref<number>>,
): ReplayControlState {
  const samples = shallowRef<ControlStateSample[]>([])
  const isLoading = shallowRef(false)
  const error = shallowRef<null | string>(null)
  let request: AbortController | undefined

  const current = computed(() => {
    if (samples.value.length === 0)
      return null
    const startTick = Number(replay.value?.startServerTick ?? samples.value[0]!.serverTick)
    return sampleAtOrBefore(samples.value, startTick + playheadTick.value)
  })

  watch(
    () => replay.value?.eventsUrl,
    async (eventsUrl) => {
      request?.abort()
      samples.value = []
      error.value = null
      if (!eventsUrl)
        return

      const controller = new AbortController()
      request = controller
      isLoading.value = true
      try {
        const response = await fetch(new URL(eventsUrl, window.location.href), { signal: controller.signal })
        if (!response.ok)
          throw new Error(`Events request failed with HTTP ${response.status}.`)
        samples.value = await readControlStateStream(response, controller.signal)
      }
      catch (caught) {
        if (!controller.signal.aborted)
          error.value = errorMessage(caught)
      }
      finally {
        if (request === controller) {
          request = undefined
          isLoading.value = false
        }
      }
    },
    { immediate: true },
  )

  onScopeDispose(() => request?.abort())

  return {
    current,
    error: shallowReadonly(error),
    isLoading: shallowReadonly(isLoading),
    sampleCount: computed(() => samples.value.length),
  }
}

function errorMessage(value: unknown): string {
  if (value && typeof value === 'object' && 'message' in value && typeof value.message === 'string')
    return value.message
  return String(value)
}

function finiteNumber(value: unknown): number {
  const number = Number(value ?? 0)
  return Number.isFinite(number) ? number : 0
}

async function readControlStateStream(response: Response, signal: AbortSignal): Promise<ControlStateSample[]> {
  const reader = response.body?.getReader()
  if (!reader)
    return (await response.text()).split('\n').map(parseControlStateLine).filter(sample => sample !== null)

  const decoder = new TextDecoder()
  const samples: ControlStateSample[] = []
  let remainder = ''
  while (true) {
    if (signal.aborted)
      throw new DOMException('Aborted', 'AbortError')
    const { done, value } = await reader.read()
    remainder += decoder.decode(value, { stream: !done })
    const lines = remainder.split('\n')
    remainder = lines.pop() ?? ''
    for (const line of lines) {
      const sample = parseControlStateLine(line)
      if (sample)
        samples.push(sample)
    }
    if (done)
      break
  }
  const finalSample = parseControlStateLine(remainder)
  if (finalSample)
    samples.push(finalSample)
  return samples
}
