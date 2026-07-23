export interface CaptureArtifact {
  session_id: string
  relative_path: string
  state: 'open' | 'complete' | 'incomplete' | 'empty'
  size_bytes: number
  epoch_count: number
  published_epoch_count: number
  unpublished_epoch_count: number
  first_tick: number | null
  last_tick: number | null
}

export interface ReplayArtifact {
  artifact_id: string
  relative_path: string
  size_bytes: number
  sha256: string
  session_id: string
  segment_id: string
  segment_ordinal: number
  player_uuid: string
  connection_id: string | null
}

export interface DatasetArtifact {
  id: string
  dataset_id: string
  session_id: string
  sample_count: number
  state_count: number
  connection_count: number
  size_bytes: number
  rgb_samples: number
}

export interface ArtifactCatalog {
  capture_sessions: CaptureArtifact[]
  replay_archives: ReplayArtifact[]
  datasets: DatasetArtifact[]
  issues: Array<{ artifact_type: string, relative_path: string, message: string }>
  rejected_datasets: Array<{ name: string, message: string }>
  truncated: boolean
  datasets_indexing: boolean
}

export interface DatasetConnection {
  player_uuid: string
  player_name: string | null
  connection_id: string
  first_tick: number
  last_tick: number
  state_count: number
  rgb_states: number
}

export interface RenderJob {
  id: string
  dataset_id: string
  state: string
  payload: {
    session_id: string
    player_uuid: string
    connection_id: string
    render: { width: number, height: number, fps: number, no_gui?: boolean }
  }
  progress?: { current?: number, total?: number, message?: string } | null
  error?: string | null
  updated_at: string
}

export interface RenderWorker {
  id: string
  name: string
  state: string
  current_job_id: string | null
  heartbeat_at: string
}
