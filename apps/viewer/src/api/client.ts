import type {
  ActionsResponse,
  BundleSummary,
  PlayerTickState,
  RenderTimelineResponse,
  ReplayDescriptor,
  ReplaysResponse,
  SceneSliceResponse,
  StagedImportResult,
  TrajectoryResponse,
} from '../types/viewer'

const API_PREFIX = '/api/v1/viewer'
const TOKEN_HEADER = 'X-Minerec-Viewer-Token'
const STAGED_IMPORT_HEADER = 'X-Minerec-Viewer-Staged-Import'

export interface ViewerApiClient {
  commitStagedImport: (stagedImportId: string) => Promise<BundleSummary>
  discardStagedImport: (stagedImportId: string) => Promise<void>
  getActions: (fromTick: number, toTick: number, limit: number, context?: ViewerReadContext) => Promise<ActionsResponse>
  getBundle: (context?: ViewerReadContext) => Promise<BundleSummary>
  getRenderMediaUrl: (bundleId: string) => string
  getRenderTimeline: (fromFrame: number, limit: number, context?: ViewerReadContext) => Promise<RenderTimelineResponse>
  getReplays: (context?: ViewerReadContext) => Promise<ReplaysResponse>
  getSceneSlice: (
    tick: number,
    dimension: string,
    y: number,
    radius: number,
    context?: ViewerReadContext,
  ) => Promise<SceneSliceResponse>
  getTickState: (tick: number, context?: ViewerReadContext) => Promise<PlayerTickState>
  getTrajectory: (maxPoints: number, context?: ViewerReadContext) => Promise<TrajectoryResponse>
  readonly hasToken: boolean
  stageBundle: (
    file: File,
    callbacks?: ViewerUploadCallbacks,
    signal?: AbortSignal,
  ) => Promise<StagedImportResult>
}

export interface ViewerApiClientOptions {
  fetchImpl?: typeof fetch
  origin?: string
  token: null | string
  xhrFactory?: () => XMLHttpRequest
}

export interface ViewerLaunchContext {
  sanitizedPath: string
  token: null | string
}

export interface ViewerReadContext {
  signal?: AbortSignal
  stagedImportId?: string
}

export interface ViewerUploadCallbacks {
  onProgress?: (loadedBytes: number, totalBytes: number) => void
  onValidationStart?: () => void
}

interface XhrBundleUploadOptions {
  callbacks: ViewerUploadCallbacks
  file: File
  signal?: AbortSignal
  token: string
  url: string
  xhrFactory: () => XMLHttpRequest
}

export class ViewerApiError extends Error {
  readonly status: number

  constructor(message: string, status = 0) {
    super(message)
    this.name = 'ViewerApiError'
    this.status = status
  }
}

export function buildViewerApiUrl(
  endpoint: string,
  params: Record<string, number | string | undefined> = {},
  origin = 'http://127.0.0.1',
): string {
  if (!endpoint.startsWith('/') || endpoint.includes('..') || endpoint.includes('\\')) {
    throw new Error('Viewer API endpoint must be a safe absolute path')
  }

  const url = new URL(`${API_PREFIX}${endpoint}`, origin)
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined) {
      url.searchParams.set(key, String(value))
    }
  }
  return url.toString()
}

export function createViewerApiClient(options: ViewerApiClientOptions): ViewerApiClient {
  const token = normalizeToken(options.token)
  const origin = options.origin ?? globalThis.location?.origin ?? 'http://127.0.0.1'
  const fetchImpl = options.fetchImpl ?? globalThis.fetch
  const xhrFactory = options.xhrFactory ?? (() => new XMLHttpRequest())

  async function requestJson<T>(endpoint: string, init: RequestInit = {}): Promise<T> {
    if (!token) {
      throw new ViewerApiError('This viewer was not launched with an API token.')
    }
    if (!fetchImpl) {
      throw new ViewerApiError('Fetch is unavailable in this browser.')
    }

    const headers = new Headers(init.headers)
    headers.set('Accept', 'application/json')
    headers.set(TOKEN_HEADER, token)
    const response = await fetchImpl(buildViewerApiUrl(endpoint, {}, origin), {
      ...init,
      credentials: 'same-origin',
      headers,
      mode: 'same-origin',
    })

    if (!response.ok) {
      throw new ViewerApiError(await readApiError(response), response.status)
    }
    return response.json() as Promise<T>
  }

  function readRequest(context?: ViewerReadContext): RequestInit {
    const headers = new Headers()
    if (context?.stagedImportId) {
      headers.set(STAGED_IMPORT_HEADER, context.stagedImportId)
    }
    return { headers, signal: context?.signal }
  }

  return {
    async commitStagedImport(stagedImportId) {
      const response = await requestJson<unknown>(`/imports/${safeStagedImportId(stagedImportId)}/commit`, {
        method: 'POST',
      })
      return normalizeBundle(response)
    },
    async discardStagedImport(stagedImportId) {
      await requestJson<unknown>(`/imports/${safeStagedImportId(stagedImportId)}/discard`, {
        method: 'POST',
      })
    },
    async getActions(fromTick, toTick, limit, context) {
      const endpoint = withQuery('/actions', {
        from_tick: toInteger(fromTick, 'from tick'),
        limit: clampInteger(limit, 1, 500, 'action limit'),
        to_tick: toInteger(toTick, 'to tick'),
      })
      const response = await requestJson<unknown>(endpoint, readRequest(context))
      return normalizeActions(response, fromTick, toTick)
    },
    async getBundle(context) {
      const response = await requestJson<unknown>('/bundle', readRequest(context))
      return normalizeBundle(response)
    },
    getRenderMediaUrl(bundleId) {
      if (!token) {
        return ''
      }
      return buildViewerApiUrl('/render/fpv', { bundle_id: bundleId, token }, origin)
    },
    async getRenderTimeline(fromFrame, limit, context) {
      const endpoint = withQuery('/render/fpv/timeline', {
        from_frame: clampInteger(fromFrame, 0, Number.MAX_SAFE_INTEGER, 'timeline frame'),
        limit: clampInteger(limit, 1, 1000, 'timeline limit'),
      })
      const response = await requestJson<unknown>(endpoint, readRequest(context))
      return normalizeTimeline(response)
    },
    async getReplays(context) {
      const response = await requestJson<unknown>('/replays', readRequest(context))
      return normalizeReplaysResponse(response)
    },
    async getSceneSlice(tick, dimension, y, radius, context) {
      const endpoint = withQuery('/scene/slice', {
        dimension,
        radius: clampInteger(radius, 1, 32, 'scene radius'),
        tick: toInteger(tick, 'tick'),
        y: toInteger(y, 'scene y'),
      })
      const response = await requestJson<unknown>(endpoint, readRequest(context))
      return normalizeSceneSlice(response, radius)
    },
    async getTickState(tick, context) {
      const response = await requestJson<unknown>(`/ticks/${toInteger(tick, 'tick')}`, readRequest(context))
      return normalizeTickState(response)
    },
    async getTrajectory(maxPoints, context) {
      const endpoint = withQuery('/trajectory', {
        max_points: clampInteger(maxPoints, 2, 5000, 'trajectory point limit'),
      })
      const response = await requestJson<unknown>(endpoint, readRequest(context))
      return normalizeTrajectory(response)
    },
    hasToken: token !== null,
    async stageBundle(file, callbacks = {}, signal) {
      if (!token) {
        throw new ViewerApiError('This viewer was not launched with an API token.')
      }
      const response = await uploadBundleWithXhr({
        callbacks,
        file,
        signal,
        token,
        url: buildViewerApiUrl('/import', {}, origin),
        xhrFactory,
      })
      const result = objectValue(response, 'staged import response')
      return {
        archive_sha256: textValue(result.archive_sha256, 'uploaded archive hash'),
        bundle: normalizeBundle(result.bundle),
        staged_import_id: safeStagedImportId(textValue(result.staged_import_id, 'staged import id')),
      }
    },
  }
}

export function parseViewerLaunchUrl(href: string): ViewerLaunchContext {
  const url = new URL(href)
  const token = normalizeToken(url.searchParams.get('token'))
  url.searchParams.delete('token')
  const search = url.searchParams.toString()

  return {
    sanitizedPath: `${url.pathname}${search ? `?${search}` : ''}${url.hash}`,
    token,
  }
}

function arrayValue(value: unknown, label: string): unknown[] {
  if (!Array.isArray(value)) {
    throw new ViewerApiError(`The bridge returned invalid ${label}.`)
  }
  return value
}

function blockIdentifier(value: unknown): string {
  if (typeof value === 'string') {
    return value
  }
  const state = optionalObject(value)
  for (const key of ['id', 'name', 'block', 'block_id']) {
    if (typeof state[key] === 'string') {
      return state[key]
    }
  }
  return 'minecraft:unknown_block'
}

function booleanValue(value: unknown, label: string): boolean {
  if (typeof value !== 'boolean') {
    throw new ViewerApiError(`The bridge returned an invalid ${label}.`)
  }
  return value
}

function clampInteger(value: number, minimum: number, maximum: number, label: string): number {
  return Math.min(maximum, Math.max(minimum, toInteger(value, label)))
}

function findBlockEntity(value: unknown, position: [number, number, number]): null | Record<string, unknown> {
  if (!Array.isArray(value)) {
    return null
  }
  for (const item of value) {
    const candidate = optionalObject(item)
    const candidatePosition = candidate.position
    if (Array.isArray(candidatePosition) && candidatePosition.length === 3 && candidatePosition.every((coordinate, index) => coordinate === position[index])) {
      return candidate
    }
  }
  return null
}

function hasObservedContents(blockEntity: Record<string, unknown>): boolean {
  const payload = optionalObject(blockEntity.payload)
  return ['Items', 'items', 'inventory'].some(key => Array.isArray(payload[key]))
}

function humanize(value: string): string {
  return value.replaceAll('_', ' ').replace(/\b\w/g, letter => letter.toUpperCase())
}

function integerValue(value: unknown, label: string): number {
  const result = numberValue(value, label)
  if (!Number.isSafeInteger(result)) {
    throw new ViewerApiError(`The bridge returned an invalid ${label}.`)
  }
  return result
}

function integerVectorValue(value: unknown, label: string): [number, number, number] {
  if (!Array.isArray(value) || value.length !== 3) {
    throw new ViewerApiError(`The bridge returned an invalid ${label}.`)
  }
  return [integerValue(value[0], label), integerValue(value[1], label), integerValue(value[2], label)]
}

function isAirBlock(value: unknown): boolean {
  if (value === null || value === undefined) {
    return true
  }
  const id = blockIdentifier(value)
  return id === 'minecraft:air' || id === 'minecraft:cave_air' || id === 'minecraft:void_air'
}

function normalizeActions(value: unknown, fromTick: number, toTick: number): ActionsResponse {
  const response = objectValue(value, 'actions response')
  const actions = arrayValue(response.actions, 'actions').map((item, index) => {
    const action = objectValue(item, 'action')
    const payload = optionalObject(action.payload)
    const actionType = textValue(action.action_type, 'action type')
    return {
      action_type: actionType,
      apply_sequence: optionalInteger(action.sequence) ?? index,
      detail: summarizePayload(payload),
      label: humanize(actionType),
      payload,
      tick: integerValue(action.server_tick, 'action tick'),
    }
  })
  return {
    actions,
    from_tick: fromTick,
    to_tick: toTick,
    truncated: response.truncated === true,
  }
}

function normalizeBundle(value: unknown): BundleSummary {
  const summary = objectValue(value, 'bundle summary')
  const metadata = objectValue(summary.metadata, 'bundle metadata')
  if (textValue(metadata.format, 'bundle format') !== 'minerec-play-bundle') {
    throw new ViewerApiError('The bridge returned an unsupported bundle format.')
  }
  const version = integerValue(metadata.format_version, 'bundle format version')
  if (version !== 1) {
    throw new ViewerApiError('The bridge returned an unsupported bundle version.')
  }
  const server = objectValue(metadata.server, 'server identity')
  const player = objectValue(metadata.player, 'player identity')
  const session = objectValue(metadata.session, 'session identity')
  const connection = objectValue(metadata.connection, 'connection identity')
  const utcRange = objectValue(metadata.utc_range, 'UTC range')
  const tickRange = objectValue(metadata.tick_range, 'tick range')
  const inventory = arrayValue(metadata.inventory, 'bundle inventory').map((item) => {
    const entry = objectValue(item, 'bundle inventory entry')
    return {
      media_role: textValue(entry.media_role, 'inventory media role'),
      path: textValue(entry.path, 'inventory path'),
      sha256: textValue(entry.sha256, 'inventory hash'),
      size: integerValue(entry.size_bytes, 'inventory size'),
    }
  })
  const render = normalizeRender(metadata.render, tickRange)
  return {
    bundle_id: textValue(summary.bundle_id ?? metadata.bundle_id, 'bundle id'),
    format: 'minerec-play-bundle',
    identity: {
      connection_id: textValue(connection.id, 'connection id'),
      player_name: textValue(player.name, 'player name'),
      player_uuid: textValue(player.uuid, 'player UUID'),
      server_instance_id: textValue(server.instance_id, 'server instance id'),
      server_name: textValue(server.name, 'server name'),
      session_id: textValue(session.id, 'session id'),
    },
    integrity: {
      inventory_bytes: inventory.reduce((total, entry) => total + entry.size, 0),
      inventory_entries: inventory.length,
      validated: true,
      validated_at: null,
      warnings: [],
    },
    inventory,
    modality_gaps: stringArray(metadata.known_modality_gaps, 'known modality gaps'),
    render,
    sensitivity: textValue(metadata.sensitivity, 'bundle sensitivity'),
    tick_range: {
      end: integerValue(tickRange.end, 'end tick'),
      start: integerValue(tickRange.start, 'start tick'),
    },
    utc_range: {
      ended_at: textValue(utcRange.end, 'connection end'),
      started_at: textValue(utcRange.start, 'connection start'),
    },
    version: 1,
  }
}

function normalizeRender(value: unknown, tickRange: Record<string, unknown>): BundleSummary['render'] {
  if (value === null || value === undefined) {
    return null
  }
  const render = objectValue(value, 'render descriptor')
  const video = objectValue(render.video, 'render video descriptor')
  const fps = integerValue(video.fps, 'render FPS')
  if (fps !== 20 || textValue(video.codec, 'render codec') !== 'h264' || textValue(video.pixel_format, 'render pixel format') !== 'yuv420p') {
    throw new ViewerApiError('The bridge returned a non-v1 render descriptor.')
  }
  return {
    codec: 'h264',
    end_tick: integerValue(tickRange.end, 'render end tick'),
    fps: 20,
    frame_count: integerValue(video.frame_count, 'render frame count'),
    media_type: 'video/mp4',
    path: textValue(video.path, 'render video path'),
    pixel_format: 'yuv420p',
    sha256: textValue(video.sha256, 'render video hash'),
    size: integerValue(video.size_bytes, 'render video size'),
    start_tick: integerValue(tickRange.start, 'render start tick'),
    timeline_complete: true,
  }
}

function normalizeReplayArray(value: unknown): ReplayDescriptor[] {
  return arrayValue(value, 'replays').map((item) => {
    const replay = objectValue(item, 'replay descriptor')
    const replayTicks = optionalObject(replay.replay_ticks)
    const serverTicks = optionalObject(replay.server_ticks)
    return {
      end_tick: integerValue(replay.end_server_tick ?? serverTicks.end, 'replay end tick'),
      ordinal: integerValue(replay.ordinal, 'replay ordinal'),
      path: textValue(replay.path ?? replay.archive_path, 'replay path'),
      replay_end_tick: integerValue(replay.end_replay_tick ?? replayTicks.end, 'segment replay end tick'),
      replay_start_tick: integerValue(replay.start_replay_tick ?? replayTicks.start, 'segment replay start tick'),
      segment_id: textValue(replay.segment_id, 'replay segment id'),
      sha256: textValue(replay.sha256, 'replay hash'),
      size: integerValue(replay.size_bytes, 'replay size'),
      start_tick: integerValue(replay.start_server_tick ?? serverTicks.start, 'replay start tick'),
    }
  })
}

function normalizeReplaysResponse(value: unknown): ReplaysResponse {
  const response = objectValue(value, 'replays response')
  return { replays: normalizeReplayArray(response.replays) }
}

function normalizeSceneSlice(value: unknown, radius: number): SceneSliceResponse {
  const response = objectValue(value, 'scene slice')
  const cells = arrayValue(response.cells, 'scene slice cells').map((item, index) => {
    const cell = objectValue(item, 'scene slice cell')
    const position = integerVectorValue(cell.world_position, 'scene cell position')
    const covered = booleanValue(cell.covered, 'scene cell coverage')
    const blockState = cell.block_state
    const blockEntity = findBlockEntity(response.block_entities, position)
    return {
      block_entity_id: blockEntity ? index + 1 : null,
      block_id: covered && !isAirBlock(blockState) ? blockIdentifier(blockState) : null,
      contents_known: blockEntity ? hasObservedContents(blockEntity) : null,
      kind: covered ? (isAirBlock(blockState) ? 'air' : 'block') : 'unknown',
      x: position[0],
      z: position[2],
    } satisfies SceneSliceResponse['cells'][number]
  })
  const width = integerValue(response.width, 'scene slice width')
  const height = integerValue(response.height, 'scene slice height')
  const columnOrigin = integerValue(response.column_origin, 'scene slice column origin')
  const rowOrigin = integerValue(response.row_origin, 'scene slice row origin')
  return {
    bounded: true,
    cells,
    center_x: columnOrigin + Math.floor(width / 2),
    center_z: rowOrigin + Math.floor(height / 2),
    dimension: textValue(response.dimension, 'scene slice dimension'),
    height,
    radius,
    tick: integerValue(response.tick, 'scene slice tick'),
    width,
    y: integerValue(response.coordinate, 'scene slice coordinate'),
  }
}

function normalizeTickState(value: unknown): PlayerTickState {
  const response = objectValue(value, 'tick response')
  const frame = objectValue(response.frame, 'scene frame')
  const state = objectValue(response.state, 'player state')
  const position = vectorValue(state.position, 'player position')
  const velocity = vectorValue(state.velocity, 'player velocity')
  const payload = optionalObject(state.payload)
  return {
    air: integerValue(state.air, 'player air'),
    apply_barrier: integerValue(state.state_barrier_apply_sequence, 'apply barrier'),
    controls: {
      backward: null,
      forward: null,
      jump: null,
      left: null,
      primary: null,
      right: null,
      secondary: null,
      sneak: booleanValue(state.sneaking, 'sneaking'),
      sprint: booleanValue(state.sprinting, 'sprinting'),
    },
    current_player_entity_id: integerValue(state.entity_id, 'player entity id'),
    dimension: textValue(state.dimension, 'player dimension'),
    food: integerValue(state.food_level, 'food level'),
    game_mode: textValue(state.game_mode, 'game mode'),
    health: numberValue(state.health, 'player health'),
    payload,
    pose: textValue(state.pose, 'player pose'),
    position,
    replay_tick: integerValue(frame.replay_tick, 'replay tick'),
    scene_frame: stringOrInteger(frame.frame_id, 'scene frame id'),
    selected_slot: integerValue(state.selected_slot, 'selected slot'),
    tick: integerValue(state.tick, 'player tick'),
    velocity,
    xp_level: integerValue(state.experience_level, 'experience level'),
    xp_progress: numberValue(state.experience_progress, 'experience progress'),
  }
}

function normalizeTimeline(value: unknown): RenderTimelineResponse {
  const response = objectValue(value, 'render timeline response')
  return {
    frames: arrayValue(response.frames, 'render timeline frames').map((item) => {
      const frame = objectValue(item, 'render timeline frame')
      return {
        frame: integerValue(frame.frame_index, 'render frame index'),
        pts: numberValue(frame.pts, 'render frame PTS'),
        replay_tick: integerValue(frame.replay_tick, 'render replay tick'),
        scene_frame: stringOrInteger(frame.scene_frame, 'render scene frame'),
        server_tick: integerValue(frame.server_tick, 'render server tick'),
      }
    }),
    next_frame: response.next_frame === null ? null : integerValue(response.next_frame, 'next render frame'),
  }
}

function normalizeToken(value: null | string): null | string {
  const normalized = value?.trim() ?? ''
  return normalized.length > 0 ? normalized : null
}

function normalizeTrajectory(value: unknown): TrajectoryResponse {
  const response = objectValue(value, 'trajectory response')
  const rawPoints = arrayValue(response.points, 'trajectory points')
  const grouped = new Map<string, TrajectoryResponse['tracks'][number]>()
  let previousSourceOrdinal: null | number = null
  let previousTick: null | number = null
  let previousDimension: null | string = null
  for (const item of rawPoints) {
    const point = objectValue(item, 'trajectory point')
    const dimension = textValue(point.dimension, 'trajectory dimension')
    const position = vectorValue(point.position, 'trajectory position')
    const sourceOrdinal = optionalInteger(point.source_ordinal)
    const tick = integerValue(point.tick, 'trajectory tick')
    let track = grouped.get(dimension)
    if (!track) {
      track = {
        color: null,
        connection_id: '',
        dimension,
        player_uuid: '',
        points: [],
      }
      grouped.set(dimension, track)
    }
    track.points.push({
      break_before: previousTick !== null && (
        previousDimension !== dimension
        || ((sourceOrdinal === null || previousSourceOrdinal === null || sourceOrdinal === previousSourceOrdinal + 1) && tick !== previousTick + 1)
      ),
      tick,
      x: position.x,
      z: position.z,
    })
    previousDimension = dimension
    previousSourceOrdinal = sourceOrdinal
    previousTick = tick
  }
  const tracks = [...grouped.values()]
  const returnedPoints = tracks.reduce((total, track) => total + track.points.length, 0)
  return {
    returned_points: optionalInteger(response.returned_points) ?? returnedPoints,
    total_points: optionalInteger(response.total_points) ?? returnedPoints,
    tracks,
    truncated: response.truncated === true,
  }
}

function numberValue(value: unknown, label: string): number {
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    throw new ViewerApiError(`The bridge returned an invalid ${label}.`)
  }
  return value
}

function objectValue(value: unknown, label: string): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new ViewerApiError(`The bridge returned an invalid ${label}.`)
  }
  return value as Record<string, unknown>
}

function optionalInteger(value: unknown): null | number {
  return typeof value === 'number' && Number.isSafeInteger(value) ? value : null
}

function optionalObject(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}
}

function primitiveSummary(value: unknown): string {
  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean' || value === null) {
    return String(value)
  }
  return Array.isArray(value) ? `[${value.length}]` : '{…}'
}

async function readApiError(response: Response): Promise<string> {
  const fallback = `Viewer request failed (${response.status})`
  const contentType = response.headers.get('content-type') ?? ''
  if (!contentType.includes('application/json')) {
    return fallback
  }

  try {
    const body = await response.json() as { error?: unknown, message?: unknown }
    if (typeof body.error === 'string' && body.error.length > 0) {
      return body.error
    }
    if (typeof body.message === 'string' && body.message.length > 0) {
      return body.message
    }
  }
  catch {
    return fallback
  }
  return fallback
}

function safeStagedImportId(value: string): string {
  if (!/^[\w-]{16,128}$/.test(value)) {
    throw new ViewerApiError('The bridge returned an invalid staged import id.')
  }
  return value
}

function stringArray(value: unknown, label: string): string[] {
  const values = arrayValue(value, label)
  if (!values.every(item => typeof item === 'string')) {
    throw new ViewerApiError(`The bridge returned invalid ${label}.`)
  }
  return values as string[]
}

function stringOrInteger(value: unknown, label: string): number | string {
  if (typeof value === 'string' && value.length > 0) {
    return value
  }
  return integerValue(value, label)
}

function summarizePayload(payload: Record<string, unknown>): null | string {
  const entries = Object.entries(payload).slice(0, 4)
  if (entries.length === 0) {
    return null
  }
  const summary = entries.map(([key, value]) => `${key}=${primitiveSummary(value)}`).join(', ')
  return summary.length > 160 ? `${summary.slice(0, 157)}…` : summary
}

function textValue(value: unknown, label: string): string {
  if (typeof value !== 'string' || value.length === 0) {
    throw new ViewerApiError(`The bridge returned an invalid ${label}.`)
  }
  return value
}

function toInteger(value: number, label: string): number {
  if (!Number.isSafeInteger(value)) {
    throw new TypeError(`${label} must be a safe integer`)
  }
  return value
}

function uploadBundleWithXhr(options: XhrBundleUploadOptions): Promise<unknown> {
  return new Promise((resolve, reject) => {
    if (options.signal?.aborted) {
      reject(new DOMException('Bundle import was cancelled.', 'AbortError'))
      return
    }

    const xhr = options.xhrFactory()
    let settled = false
    const abort = () => xhr.abort()
    const cleanup = () => options.signal?.removeEventListener('abort', abort)
    const fail = (error: unknown) => {
      if (settled) {
        return
      }
      settled = true
      cleanup()
      reject(error)
    }
    const succeed = (value: unknown) => {
      if (settled) {
        return
      }
      settled = true
      cleanup()
      resolve(value)
    }
    xhr.open('POST', options.url, true)
    xhr.responseType = 'text'
    xhr.withCredentials = true
    xhr.setRequestHeader('Accept', 'application/json')
    xhr.setRequestHeader('Content-Type', 'application/zip')
    xhr.setRequestHeader(TOKEN_HEADER, options.token)
    xhr.setRequestHeader('X-Minerec-Bundle-Name', encodeURIComponent(options.file.name))
    xhr.setRequestHeader('X-Minerec-Bundle-Size', String(options.file.size))
    xhr.upload.onprogress = (event) => {
      options.callbacks.onProgress?.(
        Math.min(options.file.size, event.loaded),
        options.file.size,
      )
    }
    xhr.upload.onload = () => {
      options.callbacks.onProgress?.(options.file.size, options.file.size)
      options.callbacks.onValidationStart?.()
    }
    xhr.onload = () => {
      let value: unknown
      try {
        value = JSON.parse(xhr.responseText)
      }
      catch {
        fail(new ViewerApiError('The viewer bridge returned invalid JSON.', xhr.status))
        return
      }
      if (xhr.status < 200 || xhr.status >= 300) {
        const error = optionalObject(value).error
        fail(new ViewerApiError(typeof error === 'string' ? error : `Viewer request failed (${xhr.status}).`, xhr.status))
        return
      }
      succeed(value)
    }
    xhr.onerror = () => fail(new ViewerApiError('The local viewer bridge could not be reached.'))
    xhr.onabort = () => fail(new DOMException('Bundle import was cancelled.', 'AbortError'))
    options.signal?.addEventListener('abort', abort, { once: true })
    xhr.send(options.file)
  })
}

function vectorValue(value: unknown, label: string): { x: number, y: number, z: number } {
  if (!Array.isArray(value) || value.length !== 3) {
    throw new ViewerApiError(`The bridge returned an invalid ${label}.`)
  }
  return {
    x: numberValue(value[0], label),
    y: numberValue(value[1], label),
    z: numberValue(value[2], label),
  }
}

function withQuery(endpoint: string, params: Record<string, number | string>): string {
  const query = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    query.set(key, String(value))
  }
  return `${endpoint}?${query.toString()}`
}
