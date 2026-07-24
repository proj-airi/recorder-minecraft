import { describe, expect, it, vi } from 'vitest'

import { buildViewerApiUrl, createViewerApiClient, parseViewerLaunchUrl } from '../src/api/client'

function bundleSummaryResponse(): Record<string, unknown> {
  return {
    bundle_id: 'a'.repeat(64),
    has_render: false,
    metadata: {
      bundle_id: 'a'.repeat(64),
      connection: { id: 'connection' },
      format: 'minerec-play-bundle',
      format_version: 1,
      inventory: [],
      known_modality_gaps: [],
      player: { name: 'Player', uuid: 'player-uuid' },
      render: null,
      replays: [{
        ordinal: 0,
        path: 'replays/000000--segment.zip',
        replay_ticks: { end: 21, start: 20 },
        segment_id: 'segment',
        server_ticks: { end: 11, start: 10 },
        sha256: 'b'.repeat(64),
        size_bytes: 100,
      }],
      sensitivity: 'private',
      server: { instance_id: 'server-instance', name: 'Server' },
      session: { id: 'session' },
      tick_range: { end: 11, start: 10, tick_rate_hz: 20 },
      utc_range: { end: '2026-07-24T00:00:01Z', start: '2026-07-24T00:00:00Z' },
    },
    replays: [],
  }
}

describe('viewer launch URL', () => {
  it('captures the token in memory and removes it from the visible path', () => {
    expect(parseViewerLaunchUrl('http://127.0.0.1:49152/viewer?theme=dark&token=secret-value#tick')).toEqual({
      sanitizedPath: '/viewer?theme=dark#tick',
      token: 'secret-value',
    })
  })

  it('normalizes an empty launch token to null', () => {
    expect(parseViewerLaunchUrl('http://127.0.0.1:49152/?token=%20')).toEqual({
      sanitizedPath: '/',
      token: null,
    })
  })
})

describe('viewer API URL construction', () => {
  it('keeps requests on the supplied origin and encodes parameters', () => {
    expect(buildViewerApiUrl('/scene/slice', { dimension: 'minecraft:the end', tick: 42 }, 'http://127.0.0.1:40001')).toBe(
      'http://127.0.0.1:40001/api/v1/viewer/scene/slice?dimension=minecraft%3Athe+end&tick=42',
    )
  })

  it('rejects traversing and backslash endpoints', () => {
    expect(() => buildViewerApiUrl('/../outside')).toThrow(/safe absolute path/)
    expect(() => buildViewerApiUrl('/ticks\\1')).toThrow(/safe absolute path/)
  })
})

describe('viewer API token and upload behavior', () => {
  it('streams the File itself with real upload/validation progress and stages without committing', async () => {
    const file = new File(['portable bundle bytes'], 'test bundle.mcplay.zip', { type: 'application/zip' })
    let sentBody: Document | null | XMLHttpRequestBodyInit = null
    const headers = new Map<string, string>()
    const progress: string[] = []
    const uploadHandlers: {
      onload: ((event: ProgressEvent) => void) | null
      onprogress: ((event: ProgressEvent) => void) | null
    } = { onload: null, onprogress: null }
    const fakeXhr = {
      abort: vi.fn(),
      onabort: null,
      onerror: null,
      onload: null,
      open: vi.fn(),
      responseText: '',
      responseType: '',
      send(body: Document | null | XMLHttpRequestBodyInit) {
        sentBody = body
        uploadHandlers.onprogress?.({ loaded: file.size } as ProgressEvent)
        uploadHandlers.onload?.({} as ProgressEvent)
        fakeXhr.status = 201
        fakeXhr.responseText = JSON.stringify({
          archive_sha256: 'f'.repeat(64),
          bundle: bundleSummaryResponse(),
          staged_import_id: 'staged-import-id-123',
        })
        ;(fakeXhr.onload as ((event: ProgressEvent) => void) | null)?.({} as ProgressEvent)
      },
      setRequestHeader: vi.fn((name: string, value: string) => headers.set(name, value)),
      status: 0,
      upload: uploadHandlers as unknown as XMLHttpRequestUpload,
      withCredentials: false,
    }
    const xhr = fakeXhr as unknown as XMLHttpRequest
    const client = createViewerApiClient({
      origin: 'http://127.0.0.1:40123',
      token: 'launch-secret',
      xhrFactory: () => xhr,
    })

    const staged = await client.stageBundle(file, {
      onProgress: (loaded, total) => progress.push(`${loaded}/${total}`),
      onValidationStart: () => progress.push('validating'),
    })

    expect(xhr.open).toHaveBeenCalledWith('POST', 'http://127.0.0.1:40123/api/v1/viewer/import', true)
    expect(sentBody).toBe(file)
    expect(headers.get('X-Minerec-Viewer-Token')).toBe('launch-secret')
    expect(headers.get('X-Minerec-Bundle-Name')).toBe('test%20bundle.mcplay.zip')
    expect(headers.get('X-Minerec-Bundle-Size')).toBe(String(file.size))
    expect(progress).toEqual([`${file.size}/${file.size}`, `${file.size}/${file.size}`, 'validating'])
    expect(staged.staged_import_id).toBe('staged-import-id-123')
  })

  it('uses the token only in the media URL where a video element cannot set headers', () => {
    const client = createViewerApiClient({ origin: 'http://127.0.0.1:40123', token: 'launch secret' })
    expect(client.getRenderMediaUrl('bundle-revision')).toBe(
      'http://127.0.0.1:40123/api/v1/viewer/render/fpv?bundle_id=bundle-revision&token=launch+secret',
    )
  })

  it('scopes staged reads with an opaque header', async () => {
    let requestInit: RequestInit | undefined
    const fetchImpl = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      requestInit = init
      return new Response(JSON.stringify({
        frame: { frame_id: 'frame-10', replay_tick: 20 },
        state: {
          air: 300,
          dimension: 'minecraft:overworld',
          entity_id: 7,
          experience_level: 0,
          experience_progress: 0,
          food_level: 20,
          game_mode: 'survival',
          health: 20,
          payload: {},
          pose: 'standing',
          position: [0, 64, 0],
          selected_slot: 0,
          sneaking: false,
          sprinting: false,
          state_barrier_apply_sequence: 1,
          tick: 10,
          velocity: [0, 0, 0],
        },
      }), { status: 200 })
    }) as typeof fetch
    const client = createViewerApiClient({
      fetchImpl,
      origin: 'http://127.0.0.1:40123',
      token: 'launch-secret',
    })

    await client.getTickState(10, { stagedImportId: 'staged-import-id-123' })

    expect(new Headers(requestInit?.headers).get('X-Minerec-Viewer-Staged-Import')).toBe('staged-import-id-123')
  })

  it('normalizes the bridge metadata wrapper into the typed bundle view model', async () => {
    const fetchImpl = vi.fn(async () => new Response(JSON.stringify(bundleSummaryResponse()), {
      headers: { 'Content-Type': 'application/json' },
      status: 200,
    })) as typeof fetch
    const client = createViewerApiClient({
      fetchImpl,
      origin: 'http://127.0.0.1:40123',
      token: 'launch-secret',
    })

    const bundle = await client.getBundle()

    expect(bundle.identity).toMatchObject({
      connection_id: 'connection',
      player_name: 'Player',
      server_name: 'Server',
    })
    expect(bundle.tick_range).toEqual({ end: 11, start: 10 })
    expect(bundle.integrity.validated).toBe(true)
    expect(bundle.render).toBeNull()
  })

  it('normalizes portable scene state and preserves unknown cells distinctly from air', async () => {
    const fetchImpl = vi.fn(async (input: RequestInfo | URL) => {
      const path = new URL(String(input)).pathname
      const body = path.includes('/ticks/')
        ? {
            frame: { frame_id: 'frame-10', replay_tick: 20 },
            state: {
              air: 300,
              dimension: 'minecraft:overworld',
              entity_id: 7,
              experience_level: 3,
              experience_progress: 0.25,
              food_level: 19,
              game_mode: 'survival',
              health: 20,
              payload: { abilities: { may_fly: false }, inventory: [{ slot: 0 }] },
              pose: 'standing',
              position: [1.25, 64, 2],
              selected_slot: 2,
              sneaking: false,
              sprinting: true,
              state_barrier_apply_sequence: 9,
              tick: 10,
              velocity: [0.1, 0, 0],
            },
          }
        : {
            block_entities: [],
            cells: [
              { block_state: 'minecraft:air', covered: true, world_position: [0, 64, 0] },
              { block_state: 'minecraft:stone', covered: true, world_position: [1, 64, 0] },
              { block_state: null, covered: false, world_position: [2, 64, 0] },
            ],
            column_origin: 0,
            coordinate: 64,
            dimension: 'minecraft:overworld',
            height: 3,
            row_origin: 0,
            tick: 10,
            width: 3,
          }
      return new Response(JSON.stringify(body), {
        headers: { 'Content-Type': 'application/json' },
        status: 200,
      })
    }) as typeof fetch
    const client = createViewerApiClient({
      fetchImpl,
      origin: 'http://127.0.0.1:40123',
      token: 'launch-secret',
    })

    const state = await client.getTickState(10)
    const slice = await client.getSceneSlice(10, 'minecraft:overworld', 64, 1)

    expect(state.position).toEqual({ x: 1.25, y: 64, z: 2 })
    expect(state.controls).toMatchObject({ sneak: false, sprint: true })
    expect(state.payload.inventory).toEqual([{ slot: 0 }])
    expect(slice.cells.map(cell => cell.kind)).toEqual(['air', 'block', 'unknown'])
  })

  it('preserves opaque Scene V2 frame ids in render timeline pages', async () => {
    const fetchImpl = vi.fn(async () => new Response(JSON.stringify({
      frames: [{
        frame_index: 0,
        pts: 0,
        replay_tick: 20,
        scene_frame: 'segment-id:10',
        server_tick: 10,
      }],
      next_frame: null,
    }), { status: 200 })) as typeof fetch
    const client = createViewerApiClient({
      fetchImpl,
      origin: 'http://127.0.0.1:40123',
      token: 'launch-secret',
    })

    const timeline = await client.getRenderTimeline(0, 400)

    expect(timeline.frames[0]?.scene_frame).toBe('segment-id:10')
  })

  it('keeps decimated trajectory samples connected while preserving true source breaks', async () => {
    const fetchImpl = vi.fn(async () => new Response(JSON.stringify({
      points: [
        { dimension: 'minecraft:overworld', position: [0, 64, 0], source_ordinal: 1, tick: 10 },
        { dimension: 'minecraft:overworld', position: [10, 64, 10], source_ordinal: 50, tick: 59 },
        { dimension: 'minecraft:overworld', position: [11, 64, 10], source_ordinal: 51, tick: 70 },
      ],
      returned_points: 3,
      total_points: 100,
      truncated: true,
    }), {
      headers: { 'Content-Type': 'application/json' },
      status: 200,
    })) as typeof fetch
    const client = createViewerApiClient({
      fetchImpl,
      origin: 'http://127.0.0.1:40123',
      token: 'launch-secret',
    })

    const trajectory = await client.getTrajectory(10)

    expect(trajectory.tracks[0].points.map(point => point.break_before)).toEqual([false, false, true])
    expect(trajectory).toMatchObject({ returned_points: 3, total_points: 100, truncated: true })
  })
})
