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
  leftClick: boolean
  right: boolean
  rightClick: boolean
  selectedSlot: number
  serverTick: number
  sneak: boolean
  sprint: boolean
}

export interface ReplayControlSource {
  eventsUrl?: string
  startServerTick?: string
}

interface ClickActions {
  leftClick: boolean
  rightClick: boolean
}

interface ReplayControlState {
  current: ComputedRef<ControlStateSample | null>
  error: Readonly<ShallowRef<null | string>>
  isLoading: Readonly<ShallowRef<boolean>>
  sampleCount: ComputedRef<number>
}

interface ReplayControlStream {
  clicksByTick: Map<number, ClickActions>
  samples: ControlStateSample[]
}

export function parseClickActionsLine(line: string): null | { actions: ClickActions, serverTick: number } {
  if (!line.includes('"packetApply"'))
    return null

  try {
    const event = JSON.parse(line) as {
      identity?: { serverTick?: unknown }
      packetApply?: {
        packet?: {
          action?: unknown
          actionKind?: unknown
          identity?: { packetType?: unknown }
          interaction?: unknown
        }
      }
    }
    const packet = event.packetApply?.packet
    const serverTick = Number(event.identity?.serverTick)
    if (!packet || !Number.isFinite(serverTick))
      return null

    const actionKind = normalizedValue(packet.actionKind)
    const packetType = normalizedValue(packet.identity?.packetType).split(':').at(-1) ?? ''
    const interaction = normalizedValue(packet.interaction)
    const action = normalizedValue(packet.action)
    const leftClick = actionKind === 'swing'
      || packetType === 'swing'
      || (actionKind === 'interact' && interaction === 'attack')
      || (actionKind === 'player_action' && ['abort_destroy_block', 'start_destroy_block', 'stop_destroy_block'].includes(action))
    const rightClick = actionKind === 'use'
      || packetType.startsWith('use_item')
      || (actionKind === 'interact' && ['interact', 'interact_at'].includes(interaction))

    return leftClick || rightClick ? { actions: { leftClick, rightClick }, serverTick } : null
  }
  catch {
    return null
  }
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
      leftClick: false,
      right: state.right === true,
      rightClick: false,
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
  replay: Readonly<Ref<null | ReplayControlSource>>,
  playheadTick: Readonly<Ref<number>>,
): ReplayControlState {
  const samples = shallowRef<ControlStateSample[]>([])
  const clicksByTick = shallowRef(new Map<number, ClickActions>())
  const isLoading = shallowRef(false)
  const error = shallowRef<null | string>(null)
  let request: AbortController | undefined

  const current = computed(() => {
    if (samples.value.length === 0)
      return null
    const startTick = Number(replay.value?.startServerTick ?? samples.value[0]!.serverTick)
    const serverTick = startTick + playheadTick.value
    const sample = sampleAtOrBefore(samples.value, serverTick)
    const clicks = clicksByTick.value.get(serverTick)
    return clicks ? { ...sample, ...clicks } : sample
  })

  watch(
    () => replay.value?.eventsUrl,
    async (eventsUrl) => {
      request?.abort()
      samples.value = []
      clicksByTick.value = new Map()
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
        const stream = await readControlStateStream(response, controller.signal)
        samples.value = stream.samples
        clicksByTick.value = stream.clicksByTick
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

function normalizedValue(value: unknown): string {
  return typeof value === 'string' ? value.trim().toLowerCase() : ''
}

function parseLines(lines: string[]): ReplayControlStream {
  const samples: ControlStateSample[] = []
  const clicksByTick = new Map<number, ClickActions>()
  parseLinesInto(lines, samples, clicksByTick)
  return { clicksByTick, samples }
}

function parseLinesInto(lines: string[], samples: ControlStateSample[], clicksByTick: Map<number, ClickActions>): void {
  for (const line of lines) {
    const sample = parseControlStateLine(line)
    if (sample) {
      samples.push(sample)
      continue
    }

    const click = parseClickActionsLine(line)
    if (!click)
      continue
    const previous = clicksByTick.get(click.serverTick)
    clicksByTick.set(click.serverTick, {
      leftClick: previous?.leftClick === true || click.actions.leftClick,
      rightClick: previous?.rightClick === true || click.actions.rightClick,
    })
  }
}

async function readControlStateStream(response: Response, signal: AbortSignal): Promise<ReplayControlStream> {
  const reader = response.body?.getReader()
  if (!reader)
    return parseLines((await response.text()).split('\n'))

  const decoder = new TextDecoder()
  const samples: ControlStateSample[] = []
  const clicksByTick = new Map<number, ClickActions>()
  let remainder = ''
  while (true) {
    if (signal.aborted)
      throw new DOMException('Aborted', 'AbortError')
    const { done, value } = await reader.read()
    remainder += decoder.decode(value, { stream: !done })
    const lines = remainder.split('\n')
    remainder = lines.pop() ?? ''
    parseLinesInto(lines, samples, clicksByTick)
    if (done)
      break
  }
  parseLinesInto([remainder], samples, clicksByTick)
  return { clicksByTick, samples }
}
