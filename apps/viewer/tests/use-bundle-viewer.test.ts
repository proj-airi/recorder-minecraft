import type { ViewerApiClient, ViewerReadContext, ViewerUploadCallbacks } from '../src/api/client'
import type {
  ActionsResponse,
  BundleSummary,
  PlayerTickState,
  RenderTimelineResponse,
  SceneSliceResponse,
} from '../src/types/viewer'

import { describe, expect, it, vi } from 'vitest'

import { useBundleViewer } from '../src/composables/useBundleViewer'

const START_TICK = 100

function bundleSummary(bundleId: string, rendered = true): BundleSummary {
  return {
    bundle_id: bundleId,
    format: 'minerec-play-bundle',
    identity: {
      connection_id: 'connection',
      player_name: 'Player',
      player_uuid: 'player',
      server_instance_id: 'server-instance',
      server_name: 'Server',
      session_id: 'session',
    },
    integrity: {
      inventory_bytes: 1,
      inventory_entries: 1,
      validated: true,
      validated_at: null,
      warnings: [],
    },
    inventory: [],
    modality_gaps: [],
    render: rendered
      ? {
          codec: 'h264',
          end_tick: 699,
          fps: 20,
          frame_count: 600,
          media_type: 'video/mp4',
          path: 'renders/fpv.mp4',
          pixel_format: 'yuv420p',
          sha256: 'a'.repeat(64),
          size: 1,
          start_tick: START_TICK,
          timeline_complete: true,
        }
      : null,
    sensitivity: 'private',
    tick_range: { end: 699, start: START_TICK },
    utc_range: { ended_at: '2026-07-24T00:01:00Z', started_at: '2026-07-24T00:00:00Z' },
    version: 1,
  }
}

function sceneSlice(tick: number): SceneSliceResponse {
  return {
    bounded: true,
    cells: [],
    center_x: tick,
    center_z: 0,
    dimension: 'minecraft:overworld',
    height: 3,
    radius: 1,
    tick,
    width: 3,
    y: 64,
  }
}

function tickState(tick: number): PlayerTickState {
  return {
    air: 300,
    apply_barrier: tick,
    controls: {
      backward: false,
      forward: false,
      jump: false,
      left: false,
      primary: false,
      right: false,
      secondary: false,
      sneak: false,
      sprint: false,
    },
    current_player_entity_id: 1,
    dimension: 'minecraft:overworld',
    food: 20,
    game_mode: 'survival',
    health: 20,
    payload: { inventory: [] },
    pose: 'standing',
    position: { x: tick, y: 64, z: 0 },
    replay_tick: tick + 1000,
    scene_frame: tick - START_TICK,
    selected_slot: 0,
    tick,
    velocity: { x: 0, y: 0, z: 0 },
    xp_level: 0,
    xp_progress: 0,
  }
}

function timelinePage(fromFrame: number, count = 400): RenderTimelineResponse {
  return {
    frames: Array.from({ length: Math.min(count, 600 - fromFrame) }, (_, offset) => {
      const frame = fromFrame + offset
      return {
        frame,
        pts: frame,
        replay_tick: frame + 1000,
        scene_frame: frame,
        server_tick: START_TICK + frame,
      }
    }),
    next_frame: fromFrame + count < 600 ? fromFrame + count : null,
  }
}

function viewerApi(active: BundleSummary): ViewerApiClient {
  return {
    commitStagedImport: vi.fn(async () => active),
    discardStagedImport: vi.fn(async () => {}),
    getActions: vi.fn(async (fromTick): Promise<ActionsResponse> => ({
      actions: [{
        action_type: 'control_state',
        apply_sequence: fromTick,
        detail: null,
        label: 'Control State',
        payload: { forward: true },
        tick: fromTick,
      }],
      from_tick: fromTick,
      to_tick: fromTick,
      truncated: false,
    })),
    getBundle: vi.fn(async () => active),
    getRenderMediaUrl: bundleId => `/render/fpv?bundle_id=${bundleId}`,
    getRenderTimeline: vi.fn(async fromFrame => timelinePage(fromFrame)),
    getReplays: vi.fn(async () => ({ replays: [] })),
    getSceneSlice: vi.fn(async tick => sceneSlice(tick)),
    getTickState: vi.fn(async tick => tickState(tick)),
    getTrajectory: vi.fn(async () => ({
      returned_points: 0,
      total_points: 0,
      tracks: [],
      truncated: false,
    })),
    hasToken: true,
    stageBundle: vi.fn(async () => ({
      archive_sha256: 'b'.repeat(64),
      bundle: active,
      staged_import_id: 'staged-import-id-123',
    })),
  }
}

describe('bundle viewer orchestration', () => {
  it('loads the exact timeline page for a tick beyond the initial 400-frame cache', async () => {
    const warning = vi.spyOn(console, 'warn').mockImplementation(() => {})
    const api = viewerApi(bundleSummary('active-bundle'))
    const viewer = useBundleViewer(api)
    await viewer.initialize()

    await viewer.selectTick(650)

    expect(api.getRenderTimeline).toHaveBeenNthCalledWith(1, 0, 400, undefined)
    expect(api.getRenderTimeline).toHaveBeenNthCalledWith(2, 400, 400, expect.objectContaining({ signal: expect.any(AbortSignal) }))
    expect(viewer.selectedTick.value).toBe(650)
    expect(viewer.selectedRenderFrame.value).toBe(550)
    expect(viewer.selectedTimelineFrame.value?.server_tick).toBe(650)
    warning.mockRestore()
  })

  it('discards a hydrated candidate failure without replacing the active bundle', async () => {
    const warning = vi.spyOn(console, 'warn').mockImplementation(() => {})
    const active = bundleSummary('active-bundle', false)
    const candidate = bundleSummary('candidate-bundle', false)
    const api = viewerApi(active)
    const getTickState = vi.mocked(api.getTickState)
    getTickState.mockImplementation(async (tick: number, context?: ViewerReadContext) => {
      if (context?.stagedImportId) {
        throw new Error('candidate hydration failed')
      }
      return tickState(tick)
    })
    vi.mocked(api.stageBundle).mockImplementation(async (
      _file: File,
      callbacks?: ViewerUploadCallbacks,
    ) => {
      callbacks?.onProgress?.(10, 10)
      callbacks?.onValidationStart?.()
      return {
        archive_sha256: 'b'.repeat(64),
        bundle: candidate,
        staged_import_id: 'staged-import-id-123',
      }
    })
    const viewer = useBundleViewer(api)
    await viewer.initialize()

    await viewer.importBundle(new File(['0123456789'], 'candidate.mcplay.zip'))

    expect(api.commitStagedImport).not.toHaveBeenCalled()
    expect(api.discardStagedImport).toHaveBeenCalledWith('staged-import-id-123')
    expect(viewer.bundle.value?.bundle_id).toBe('active-bundle')
    expect(viewer.importProgress.value.phase).toBe('error')
    expect(viewer.importProgress.value.message).toContain('active bundle was not replaced')
    warning.mockRestore()
  })
})
