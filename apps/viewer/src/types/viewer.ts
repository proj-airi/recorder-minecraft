export interface ActionsResponse {
  actions: AppliedAction[]
  from_tick: number
  to_tick: number
  truncated: boolean
}

export interface AppliedAction {
  action_type: string
  apply_sequence: number
  detail: null | string
  label: string
  payload: Record<string, unknown>
  tick: number
}

export interface BundleIdentity {
  connection_id: string
  player_name: string
  player_uuid: string
  server_instance_id: string
  server_name: string
  session_id: string
}

export interface BundleInventoryEntry {
  media_role: string
  path: string
  sha256: string
  size: number
}

export interface BundleSummary {
  bundle_id: string
  format: 'minerec-play-bundle'
  identity: BundleIdentity
  integrity: IntegritySummary
  inventory: BundleInventoryEntry[]
  modality_gaps: string[]
  render: null | RenderDescriptor
  sensitivity: string
  tick_range: TickRange
  utc_range: UtcRange
  version: 1
}

export type ImportPhase = 'committing' | 'error' | 'idle' | 'loading' | 'ready' | 'uploading' | 'validating'

export interface ImportProgress {
  file_name: null | string
  file_size: null | number
  message: string
  phase: ImportPhase
  uploaded_bytes: null | number
}

export interface IntegritySummary {
  inventory_bytes: number
  inventory_entries: number
  validated: boolean
  validated_at: null | string
  warnings: string[]
}

export interface PlayerTickState {
  air: number
  apply_barrier: number
  controls: ReconstructedControls
  current_player_entity_id: number
  dimension: string
  food: number
  game_mode: string
  health: number
  payload: Record<string, unknown>
  pose: string
  position: Position3d
  replay_tick: number
  scene_frame: number | string
  selected_slot: number
  tick: number
  velocity: Position3d
  xp_level: number
  xp_progress: number
}

export interface Position3d {
  x: number
  y: number
  z: number
}

export interface ReconstructedControls {
  backward: boolean | null
  forward: boolean | null
  jump: boolean | null
  left: boolean | null
  primary: boolean | null
  right: boolean | null
  secondary: boolean | null
  sneak: boolean | null
  sprint: boolean | null
}

export interface RenderDescriptor {
  codec: 'h264'
  end_tick: number
  fps: 20
  frame_count: number
  media_type: 'video/mp4'
  path: string
  pixel_format: 'yuv420p'
  sha256: string
  size: number
  start_tick: number
  timeline_complete: boolean
}

export interface RenderTimelineFrame {
  frame: number
  pts: number
  replay_tick: number
  scene_frame: number | string
  server_tick: number
}

export interface RenderTimelineResponse {
  frames: RenderTimelineFrame[]
  next_frame: null | number
}

export interface ReplayDescriptor {
  end_tick: number
  ordinal: number
  path: string
  replay_end_tick: number
  replay_start_tick: number
  segment_id: string
  sha256: string
  size: number
  start_tick: number
}

export interface ReplaysResponse {
  replays: ReplayDescriptor[]
}

export interface SceneCell {
  block_entity_id: null | number
  block_id: null | string
  contents_known: boolean | null
  kind: SceneCellKind
  x: number
  z: number
}

export type SceneCellKind = 'air' | 'block' | 'unknown'

export interface SceneSliceResponse {
  bounded: boolean
  cells: SceneCell[]
  center_x: number
  center_z: number
  dimension: string
  height: number
  radius: number
  tick: number
  width: number
  y: number
}

export interface StagedImportResult {
  archive_sha256: string
  bundle: BundleSummary
  staged_import_id: string
}

export interface TickRange {
  end: number
  start: number
}

export interface TrajectoryPoint {
  break_before: boolean
  tick: number
  x: number
  z: number
}

export interface TrajectoryResponse {
  returned_points: number
  total_points: number
  tracks: TrajectoryTrack[]
  truncated: boolean
}

export interface TrajectoryTrack {
  color: null | string
  connection_id: string
  dimension: string
  player_uuid: string
  points: TrajectoryPoint[]
}

export interface UtcRange {
  ended_at: string
  started_at: string
}
