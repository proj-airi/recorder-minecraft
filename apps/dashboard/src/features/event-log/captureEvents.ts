/**
 * Parser for one Play's `capture/events.jsonl` (`CaptureEvent` ProtoJSON lines, see
 * `apis/proto/recorder-minecraft/artifacts/v1/events.proto`).
 *
 * One pass produces everything the event log and the input monitor need: log entries with a
 * human-readable summary, control-state samples, momentary click actions, and hotbar state.
 * ProtoJSON omits default values, so every field is optional here and a missing number is zero.
 */

/**
 * Display group of a log entry. The event log hides the noisy groups by default.
 *
 * - `action`: applied packets a player caused on purpose (clicks, use, swing, slot changes, ...).
 * - `container`: `container_view` records (opened, contents, slot, carried, closed).
 * - `client`: `client_information` records.
 * - `connection`: join and leave of the Play (derived from the Play range) and `player_loaded`.
 * - `state-change`: a `player_state` sample whose dimension, game mode, health, or life changed.
 * - `movement`: movement and camera packets (one per client tick while moving).
 * - `protocol`: keep-alive, acknowledgements, chunk batches, and redacted payloads.
 * - `tick-state`: per-tick `player_state`, `control_state`, and `replay_timeline` samples.
 * - `arrival`: `packet_arrival` records (the network-thread side of each packet).
 */
export type CaptureEventKind = 'action' | 'arrival' | 'client' | 'connection' | 'container' | 'movement' | 'protocol' | 'state-change' | 'tick-state'

export interface CaptureEventKindInfo {
  defaultVisible: boolean
  kind: CaptureEventKind
  label: string
}

export const CAPTURE_EVENT_KINDS: readonly CaptureEventKindInfo[] = [
  { defaultVisible: true, kind: 'action', label: 'Actions' },
  { defaultVisible: true, kind: 'container', label: 'Containers' },
  { defaultVisible: true, kind: 'state-change', label: 'State changes' },
  { defaultVisible: true, kind: 'connection', label: 'Join / leave' },
  { defaultVisible: true, kind: 'client', label: 'Client info' },
  { defaultVisible: false, kind: 'movement', label: 'Movement packets' },
  { defaultVisible: false, kind: 'protocol', label: 'Protocol packets' },
  { defaultVisible: false, kind: 'tick-state', label: 'Per-tick state' },
  { defaultVisible: false, kind: 'arrival', label: 'Packet arrivals' },
]

export interface CaptureLogEntry {
  /** Unique inside one Play: the capture sequence, or the line index when it is missing. */
  id: string
  kind: CaptureEventKind
  /** Short type, e.g. `container_click`, `opened`, `player_state`. */
  label: string
  /** The raw JSON line. Synthetic entries have none. */
  raw?: string
  /** `CaptureEvent` record name in snake case, e.g. `packet_apply`. */
  record: string
  sequence: number
  serverTick: number
  summary: string
}

export interface ClickActions {
  leftClick: boolean
  rightClick: boolean
}

export interface ControlStateSample extends ClickActions {
  backward: boolean
  cameraDeltaPitch: number
  cameraDeltaYaw: number
  cameraPitch: number
  cameraYaw: number
  forward: boolean
  jump: boolean
  left: boolean
  right: boolean
  /** Zero-based hotbar slot. */
  selectedSlot: number
  serverTick: number
  sneak: boolean
  sprint: boolean
}

export interface HotbarItem {
  count: number
  itemId: string
}

export interface HotbarSample {
  /** Hotbar slots 0–8; null is an empty slot. */
  items: readonly (HotbarItem | null)[]
  selectedSlot: number
  serverTick: number
}

export interface ParsedCaptureEvents {
  clicksByTick: ReadonlyMap<number, ClickActions>
  controlSamples: readonly ControlStateSample[]
  /** Log entries in capture order (Server tick, then sequence). */
  entries: readonly CaptureLogEntry[]
  hotbarSamples: readonly HotbarSample[]
  invalidLineCount: number
  lineCount: number
}

type Json = Record<string, unknown>

const PROTOCOL_PACKETS = new Set([
  'accept_teleportation',
  'chunk_batch_received',
  'client_tick_end',
  'configuration_acknowledged',
  'custom_payload',
  'keep_alive',
  'ping_request',
  'pong',
  'resource_pack',
])

/** Incremental parser. Feed lines with `push`, then call `finish`. */
export class CaptureEventParser {
  /** Non-empty lines read so far. */
  get parsedLineCount(): number {
    return this.lineCount
  }

  private clicksByTick = new Map<number, ClickActions>()
  private controlSamples: ControlStateSample[] = []
  private entries: CaptureLogEntry[] = []
  private hotbarSamples: HotbarSample[] = []
  private invalidLineCount = 0
  private lineCount = 0

  private previousState: Json | null = null

  finish(): ParsedCaptureEvents {
    // Capture order is already tick-ordered; a stable sort guards against out-of-order lines.
    const ordered = isSorted(this.entries) ? this.entries : [...this.entries].sort(compareEntries)
    return {
      clicksByTick: this.clicksByTick,
      controlSamples: this.controlSamples,
      entries: ordered,
      hotbarSamples: this.hotbarSamples,
      invalidLineCount: this.invalidLineCount,
      lineCount: this.lineCount,
    }
  }

  push(line: string): void {
    const text = line.trim()
    if (!text)
      return
    const index = this.lineCount
    this.lineCount += 1

    let event: Json
    try {
      const parsed = JSON.parse(text) as unknown
      if (!isObject(parsed))
        throw new TypeError('not an object')
      event = parsed
    }
    catch {
      this.invalidLineCount += 1
      return
    }

    const identity = objectAt(event, 'identity')
    const serverTick = Number(identity?.serverTick)
    if (!Number.isFinite(serverTick)) {
      this.invalidLineCount += 1
      return
    }
    const sequenceValue = Number(identity?.sequence)
    const sequence = Number.isFinite(sequenceValue) ? sequenceValue : index
    const base = { id: String(sequence), raw: text, sequence, serverTick }

    const control = objectAt(event, 'controlState')
    if (control) {
      const sample = controlSample(objectAt(control, 'state') ?? {}, serverTick)
      this.controlSamples.push(sample)
      this.entries.push({ ...base, kind: 'tick-state', label: 'control_state', record: 'control_state', summary: describeControl(sample) })
      return
    }

    const apply = objectAt(event, 'packetApply')
    if (apply) {
      const packet = objectAt(apply, 'packet') ?? {}
      const click = clickActions(packet)
      if (click)
        this.mergeClick(serverTick, click)
      const described = describePacket(packet)
      this.entries.push({ ...base, kind: described.kind, label: described.label, record: 'packet_apply', summary: described.summary })
      return
    }

    const arrival = objectAt(event, 'packetArrival')
    if (arrival) {
      const described = describePacket(objectAt(arrival, 'packet') ?? {})
      this.entries.push({ ...base, kind: 'arrival', label: described.label, record: 'packet_arrival', summary: `Arrived: ${described.summary}` })
      return
    }

    const state = objectAt(event, 'playerState')
    if (state) {
      this.pushPlayerState(base, state)
      return
    }

    const container = objectAt(event, 'containerView')
    if (container) {
      const kind = enumSuffix(container.kind, 'CONTAINER_VIEW_KIND_') || 'view'
      this.entries.push({ ...base, kind: 'container', label: kind, record: 'container_view', summary: describeContainerView(kind, container) })
      return
    }

    const client = objectAt(event, 'clientInformation')
    if (client) {
      this.entries.push({ ...base, kind: 'client', label: 'client_information', record: 'client_information', summary: describeClientInformation(client) })
      return
    }

    if (objectAt(event, 'replayTimeline')) {
      this.entries.push({ ...base, kind: 'tick-state', label: 'replay_timeline', record: 'replay_timeline', summary: 'Replay timeline marker' })
      return
    }

    const record = Object.keys(event).find(key => key !== 'identity') ?? 'unknown'
    this.entries.push({ ...base, kind: 'protocol', label: snakeCase(record), record: snakeCase(record), summary: `Unrecognized record ${record}` })
  }

  private mergeClick(serverTick: number, click: ClickActions): void {
    const previous = this.clicksByTick.get(serverTick)
    this.clicksByTick.set(serverTick, {
      leftClick: previous?.leftClick === true || click.leftClick,
      rightClick: previous?.rightClick === true || click.rightClick,
    })
  }

  private pushPlayerState(base: Pick<CaptureLogEntry, 'id' | 'raw' | 'sequence' | 'serverTick'>, state: Json): void {
    const items = Array.from<HotbarItem | null>({ length: 9 }).fill(null)
    const inventory = Array.isArray(state.inventory) ? state.inventory : []
    for (const slot of inventory) {
      if (!isObject(slot))
        continue
      const index = finiteNumber(slot.slot)
      if (index >= 0 && index < 9 && typeof slot.itemId === 'string' && slot.itemId)
        items[index] = { count: finiteNumber(slot.count) || 1, itemId: slot.itemId }
    }
    this.hotbarSamples.push({ items, selectedSlot: finiteNumber(state.selectedSlot), serverTick: base.serverTick })

    const changes = this.previousState ? stateChanges(this.previousState, state) : []
    this.previousState = state
    if (changes.length > 0) {
      this.entries.push({ ...base, kind: 'state-change', label: 'player_state', record: 'player_state', summary: changes.join(' · ') })
      return
    }
    this.entries.push({ ...base, kind: 'tick-state', label: 'player_state', record: 'player_state', summary: describeStateSnapshot(state) })
  }
}

/** Momentary mouse buttons implied by an applied packet, or null for other packets. */
export function clickActions(packet: Json): ClickActions | null {
  const actionKind = normalized(packet.actionKind)
  const packetType = packetShortType(packet)
  const interaction = normalized(packet.interaction)
  const action = normalized(packet.action)
  const leftClick = actionKind === 'swing'
    || packetType === 'swing'
    || (actionKind === 'interact' && interaction === 'attack')
    || (actionKind === 'player_action' && ['abort_destroy_block', 'start_destroy_block', 'stop_destroy_block'].includes(action))
  const rightClick = actionKind === 'use'
    || packetType.startsWith('use_item')
    || (actionKind === 'interact' && ['interact', 'interact_at'].includes(interaction))
  return leftClick || rightClick ? { leftClick, rightClick } : null
}

export function controlSample(state: Json, serverTick: number): ControlStateSample {
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

/** Index of the last item whose `serverTick` is at or before `serverTick`, or -1. */
export function lastIndexAtOrBefore<T extends { serverTick: number }>(items: readonly T[], serverTick: number): number {
  let low = 0
  let high = items.length - 1
  while (low <= high) {
    const middle = (low + high) >> 1
    if (items[middle]!.serverTick <= serverTick)
      low = middle + 1
    else
      high = middle - 1
  }
  return high
}

/** Latest sample at or before `serverTick`, or null when every sample is later. */
export function latestAtOrBefore<T extends { serverTick: number }>(samples: readonly T[], serverTick: number): null | T {
  const index = lastIndexAtOrBefore(samples, serverTick)
  return index < 0 ? null : samples[index]!
}

/** Parses a whole file synchronously. Prefer `streamCaptureEvents` for large files. */
export function parseCaptureEvents(text: string): ParsedCaptureEvents {
  const parser = new CaptureEventParser()
  for (const line of text.split('\n'))
    parser.push(line)
  return parser.finish()
}

export function shortItemName(itemId: string): string {
  return (itemId.split(':').at(-1) ?? itemId).replaceAll('_', ' ')
}

/**
 * Streams and parses a response body. It yields to the event loop whenever one slice of work has
 * run for `budgetMs`, so a large file does not block input or rendering.
 */
export async function streamCaptureEvents(
  response: Response,
  options: { budgetMs?: number, onProgress?: (lineCount: number) => void, signal?: AbortSignal } = {},
): Promise<ParsedCaptureEvents> {
  const budgetMs = options.budgetMs ?? 8
  const parser = new CaptureEventParser()
  const decoder = new TextDecoder()
  let sliceStart = performance.now()
  let remainder = ''

  async function feed(lines: string[]): Promise<void> {
    for (const line of lines) {
      parser.push(line)
      if (performance.now() - sliceStart > budgetMs) {
        options.onProgress?.(parser.parsedLineCount)
        await yieldToEventLoop()
        throwIfAborted(options.signal)
        sliceStart = performance.now()
      }
    }
  }

  const reader = response.body?.getReader()
  if (!reader) {
    await feed((await response.text()).split('\n'))
    return parser.finish()
  }
  while (true) {
    throwIfAborted(options.signal)
    const { done, value } = await reader.read()
    remainder += decoder.decode(value, { stream: !done })
    const lines = remainder.split('\n')
    remainder = lines.pop() ?? ''
    await feed(lines)
    if (done)
      break
  }
  await feed([remainder])
  return parser.finish()
}

function capitalize(value: string): string {
  return value ? value[0]!.toUpperCase() + value.slice(1) : value
}

function compareEntries(left: CaptureLogEntry, right: CaptureLogEntry): number {
  return left.serverTick - right.serverTick || left.sequence - right.sequence
}

function describeAction(type: string, packet: Json): string {
  const hand = typeof packet.hand === 'string' ? handName(packet.hand) : null
  switch (type) {
    case 'container_click': {
      const parts = [
        packet.slotNumber !== undefined ? `slot ${finiteNumber(packet.slotNumber)}` : null,
        typeof packet.clickType === 'string' ? packet.clickType.toLowerCase() : null,
        packet.buttonNumber !== undefined ? `button ${finiteNumber(packet.buttonNumber)}` : null,
      ].filter(Boolean)
      const container = packet.containerId !== undefined ? ` in #${finiteNumber(packet.containerId)}` : ''
      const carried = typeof packet.carriedItem === 'string' && packet.carriedItem ? ` · cursor ${packet.carriedItem}` : ''
      return `Container click${parts.length ? ` ${parts.join(', ')}` : ''}${container}${carried}`
    }
    case 'container_close':
      return `Close container${packet.containerId !== undefined ? ` #${finiteNumber(packet.containerId)}` : ''}`
    case 'interact': {
      const target = objectAt(packet, 'target')
      const what = typeof target?.typeId === 'string' && target.typeId
        ? shortItemName(target.typeId)
        : packet.entityId !== undefined ? `entity ${finiteNumber(packet.entityId)}` : 'entity'
      const interaction = typeof packet.interaction === 'string' ? packet.interaction.toLowerCase().replaceAll('_', ' ') : 'interact'
      return `${capitalize(interaction)} ${what}${packet.secondaryAction ? ' (sneaking)' : ''}${hand ? ` with ${hand}` : ''}`
    }
    case 'player_action': {
      const action = typeof packet.action === 'string' ? packet.action.toLowerCase().replaceAll('_', ' ') : 'player action'
      const position = formatBlockPosition(objectAt(packet, 'blockPosition'))
      const face = typeof packet.direction === 'string' ? ` (${packet.direction.toLowerCase()} face)` : ''
      return `${capitalize(action)}${position ? ` at ${position}` : ''}${face}`
    }
    case 'set_carried_item':
      return packet.slot !== undefined ? `Select hotbar slot ${finiteNumber(packet.slot) + 1}` : 'Select hotbar slot'
    case 'swing':
      return `Swing ${hand ?? 'arm'}`
    case 'use_item':
      return `Use item${hand ? ` in ${hand}` : ''}`
    case 'use_item_on': {
      const hit = objectAt(packet, 'blockHit')
      const position = formatBlockPosition(objectAt(hit ?? {}, 'blockPosition') ?? objectAt(packet, 'blockPosition'))
      const face = typeof hit?.direction === 'string' ? hit.direction : typeof packet.direction === 'string' ? packet.direction : null
      return `Use item${hand ? ` in ${hand}` : ''} on block${position ? ` ${position}` : ''}${face ? ` (${face.toLowerCase()} face)` : ''}`
    }
    default: {
      const actionKind = typeof packet.actionKind === 'string' ? packet.actionKind : ''
      return actionKind && actionKind !== type ? `${type} (${actionKind})` : type
    }
  }
}

function describeClientInformation(client: Json): string {
  const source = enumSuffix(client.source, 'CLIENT_INFORMATION_SOURCE_')
  const parts = [
    typeof client.language === 'string' ? client.language : null,
    client.viewDistance !== undefined ? `view ${finiteNumber(client.viewDistance)} chunks` : null,
    typeof client.mainHand === 'string' ? `${client.mainHand} hand` : null,
    typeof client.chatVisibility === 'string' ? `chat ${client.chatVisibility}` : null,
  ].filter(Boolean)
  const origin = source === 'join_snapshot' ? 'at join' : source === 'packet' ? 'changed' : ''
  return `Client settings${origin ? ` ${origin}` : ''}: ${parts.join(', ') || 'defaults'}`
}

function describeContainerView(kind: string, container: Json): string {
  const id = finiteNumber(container.containerId)
  const menu = typeof container.menuType === 'string' ? shortItemName(container.menuType) : 'container'
  const source = objectAt(container, 'source')
  const blockType = typeof source?.blockEntityType === 'string' ? shortItemName(source.blockEntityType) : null
  const position = source ? formatBlockPosition(objectAt(source, 'blockPos')) : null
  const where = [blockType, position ? `at ${position}` : null].filter(Boolean).join(' ')
  const slots = Array.isArray(container.slots) ? container.slots.filter(isObject) : []
  switch (kind) {
    case 'carried': {
      const carried = objectAt(container, 'carriedItem')
      const item = typeof carried?.itemId === 'string' && carried.itemId ? `${finiteNumber(carried.count) || 1}× ${shortItemName(carried.itemId)}` : 'nothing'
      return `Cursor in #${id} holds ${item}`
    }
    case 'closed':
      return `Closed ${where || menu} (#${id})`
    case 'contents': {
      const items = slots.filter(slot => typeof slot.itemId === 'string' && slot.itemId)
      if (items.length === 0)
        return `Contents of #${id}: empty`
      const listed = items.slice(0, 4).map(slot => `${finiteNumber(slot.count) || 1}× ${shortItemName(String(slot.itemId))} @${finiteNumber(slot.slot)}`)
      return `Contents of #${id}: ${listed.join(', ')}${items.length > 4 ? ` +${items.length - 4} more` : ''}`
    }
    case 'opened': {
      const count = container.containerSlotCount === undefined ? '' : `, ${finiteNumber(container.containerSlotCount)} slots`
      return `Opened ${where || menu} (#${id}, ${menu}${count})`
    }
    case 'slot': {
      const slot = slots[0]
      if (!slot)
        return `Slot changed in #${id}`
      const item = typeof slot.itemId === 'string' && slot.itemId ? `${finiteNumber(slot.count) || 1}× ${shortItemName(slot.itemId)}` : 'empty'
      return `Slot ${finiteNumber(slot.slot)} of #${id} → ${item}`
    }
    default:
      return `Container #${id} ${kind}`
  }
}

function describeControl(sample: ControlStateSample): string {
  const keys = [
    sample.forward && 'W',
    sample.left && 'A',
    sample.backward && 'S',
    sample.right && 'D',
    sample.jump && 'Jump',
    sample.sneak && 'Sneak',
    sample.sprint && 'Sprint',
  ].filter(Boolean)
  return `${keys.join(' ') || 'No keys'} · yaw ${sample.cameraYaw.toFixed(1)}° pitch ${sample.cameraPitch.toFixed(1)}° · slot ${sample.selectedSlot + 1}`
}

function describeMovement(type: string, packet: Json): string {
  const camera = objectAt(packet, 'cameraOrPosition')
  const position = objectAt(camera ?? {}, 'position')
  if (position)
    return `${type} → ${formatVector(position)}`
  return type
}

function describePacket(packet: Json): { kind: CaptureEventKind, label: string, summary: string } {
  const actionKind = normalized(packet.actionKind)
  const type = packetShortType(packet) || actionKind || 'packet'
  if (actionKind === 'camera_or_position' || actionKind === 'movement_input' || type.startsWith('move_player') || type === 'player_input')
    return { kind: 'movement', label: type, summary: describeMovement(type, packet) }
  if (type === 'player_loaded')
    return { kind: 'connection', label: type, summary: 'Client finished loading the world' }
  if (actionKind === 'protocol_ack' || actionKind === 'custom_payload_redacted' || PROTOCOL_PACKETS.has(type) || type === 'client_information')
    return { kind: 'protocol', label: type, summary: `${type}${packet.payloadRedacted ? ' (payload redacted)' : ''}` }
  return { kind: 'action', label: type, summary: describeAction(type, packet) }
}

function describeStateSnapshot(state: Json): string {
  const position = objectAt(state, 'position')
  return [
    position ? formatVector(position) : null,
    state.health !== undefined ? `hp ${finiteNumber(state.health)}` : null,
    typeof state.pose === 'string' ? state.pose : null,
  ].filter(Boolean).join(' · ') || 'Player state'
}

function enumSuffix(value: unknown, prefix: string): string {
  return typeof value === 'string' ? value.replace(prefix, '').toLowerCase() : ''
}

function finiteNumber(value: unknown): number {
  const number = Number(value ?? 0)
  return Number.isFinite(number) ? number : 0
}

function formatBlockPosition(position: Json | null | undefined): null | string {
  if (!position)
    return null
  return `${finiteNumber(position.x)} ${finiteNumber(position.y)} ${finiteNumber(position.z)}`
}

function formatVector(vector: Json): string {
  return `${finiteNumber(vector.x).toFixed(1)} ${finiteNumber(vector.y).toFixed(1)} ${finiteNumber(vector.z).toFixed(1)}`
}

function handName(hand: string): string {
  const value = hand.toLowerCase()
  if (value.includes('off'))
    return 'off hand'
  if (value.includes('main'))
    return 'main hand'
  return value
}

function isObject(value: unknown): value is Json {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function isSorted(entries: readonly CaptureLogEntry[]): boolean {
  for (let index = 1; index < entries.length; index += 1) {
    if (compareEntries(entries[index - 1]!, entries[index]!) > 0)
      return false
  }
  return true
}

function normalized(value: unknown): string {
  return typeof value === 'string' ? value.trim().toLowerCase() : ''
}

function objectAt(value: Json, key: string): Json | null {
  const field = value[key]
  return isObject(field) ? field : null
}

function packetShortType(packet: Json): string {
  const identity = objectAt(packet, 'identity')
  return normalized(identity?.packetType).split(':').at(-1) ?? ''
}

function snakeCase(value: string): string {
  return value.replace(/[A-Z]/g, letter => `_${letter.toLowerCase()}`)
}

function stateChanges(previous: Json, next: Json): string[] {
  const changes: string[] = []
  if (previous.dimension !== next.dimension)
    changes.push(`Dimension ${shortItemName(String(previous.dimension ?? '?'))} → ${shortItemName(String(next.dimension ?? '?'))}`)
  if (previous.gameMode !== next.gameMode)
    changes.push(`Game mode ${String(previous.gameMode ?? '?')} → ${String(next.gameMode ?? '?')}`)
  const alive = next.alive === true
  if ((previous.alive === true) !== alive)
    changes.push(alive ? 'Respawned' : 'Died')
  const health = finiteNumber(next.health)
  const healthDelta = health - finiteNumber(previous.health)
  if (Math.abs(healthDelta) >= 0.5)
    changes.push(`Health ${finiteNumber(previous.health)} → ${health} (${healthDelta > 0 ? '+' : ''}${Number(healthDelta.toFixed(1))})`)
  if (finiteNumber(previous.foodLevel) !== finiteNumber(next.foodLevel))
    changes.push(`Food ${finiteNumber(previous.foodLevel)} → ${finiteNumber(next.foodLevel)}`)
  if (finiteNumber(previous.experienceLevel) !== finiteNumber(next.experienceLevel))
    changes.push(`XP level ${finiteNumber(previous.experienceLevel)} → ${finiteNumber(next.experienceLevel)}`)
  return changes
}

function throwIfAborted(signal: AbortSignal | undefined): void {
  if (signal?.aborted)
    throw new DOMException('Aborted', 'AbortError')
}

function yieldToEventLoop(): Promise<void> {
  return new Promise(resolve => setTimeout(resolve, 0))
}
