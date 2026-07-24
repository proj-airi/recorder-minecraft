import type {
  AppliedAction,
  ReconstructedControls,
  RenderDescriptor,
  TrajectoryPoint,
  TrajectoryTrack,
} from '../types/viewer'

export interface Bounds2d {
  maxX: number
  maxZ: number
  minX: number
  minZ: number
}

export interface CanvasPoint {
  x: number
  y: number
}

export function boundTrajectoryTracks(tracks: TrajectoryTrack[], maximumPoints = 5000): TrajectoryTrack[] {
  const boundedMaximum = Math.max(2, Math.floor(maximumPoints))
  const total = tracks.reduce((count, track) => count + track.points.length, 0)
  if (total <= boundedMaximum) {
    return tracks
  }

  let remaining = boundedMaximum
  let remainingSource = total
  return tracks.map((track, index) => {
    const isLast = index === tracks.length - 1
    const allocation = isLast
      ? remaining
      : Math.min(track.points.length, Math.max(0, Math.round(remaining * track.points.length / remainingSource)))
    remaining -= allocation
    remainingSource -= track.points.length
    return {
      ...track,
      points: decimatePoints(track.points, allocation),
    }
  })
}

export function clamp(value: number, minimum: number, maximum: number): number {
  return Math.min(maximum, Math.max(minimum, value))
}

export function colorForBlock(blockId: null | string): string {
  if (!blockId) {
    return '#59635d'
  }
  let hash = 2166136261
  for (let index = 0; index < blockId.length; index += 1) {
    hash ^= blockId.charCodeAt(index)
    hash = Math.imul(hash, 16777619)
  }
  const hue = Math.abs(hash) % 360
  return `hsl(${hue} 32% 43%)`
}

export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) {
    return '—'
  }
  if (bytes < 1024) {
    return `${bytes} B`
  }

  const units = ['KiB', 'MiB', 'GiB', 'TiB']
  let value = bytes / 1024
  let unit = units[0]
  for (let index = 1; index < units.length && value >= 1024; index += 1) {
    value /= 1024
    unit = units[index]
  }
  return `${value.toFixed(value >= 10 ? 1 : 2)} ${unit}`
}

export function playbackTimeForTick(tick: number, render: RenderDescriptor): number {
  const frame = clamp(tick - render.start_tick, 0, Math.max(0, render.frame_count - 1))
  return frame / render.fps
}

export function projectTrajectoryPoint(
  point: Pick<TrajectoryPoint, 'x' | 'z'>,
  bounds: Bounds2d,
  width: number,
  height: number,
  padding = 20,
): CanvasPoint {
  const usableWidth = Math.max(1, width - padding * 2)
  const usableHeight = Math.max(1, height - padding * 2)
  const xSpan = Math.max(1, bounds.maxX - bounds.minX)
  const zSpan = Math.max(1, bounds.maxZ - bounds.minZ)
  const scale = Math.min(usableWidth / xSpan, usableHeight / zSpan)
  const drawnWidth = xSpan * scale
  const drawnHeight = zSpan * scale
  return {
    x: (width - drawnWidth) / 2 + (point.x - bounds.minX) * scale,
    y: (height - drawnHeight) / 2 + (bounds.maxZ - point.z) * scale,
  }
}

export function reconstructControls(
  actions: AppliedAction[],
  fallback: ReconstructedControls,
): ReconstructedControls {
  const controls = { ...fallback }
  for (const action of sortAppliedActions(actions)) {
    if (!action.action_type.toLowerCase().includes('control')) {
      continue
    }
    const nested = action.payload.controls
    const payload = nested && typeof nested === 'object' && !Array.isArray(nested)
      ? nested as Record<string, unknown>
      : action.payload
    applyBoolean(payload, controls, 'forward', ['forward', 'move_forward'])
    applyBoolean(payload, controls, 'backward', ['backward', 'move_backward'])
    applyBoolean(payload, controls, 'left', ['left', 'move_left'])
    applyBoolean(payload, controls, 'right', ['right', 'move_right'])
    applyBoolean(payload, controls, 'jump', ['jump', 'jumping'])
    applyBoolean(payload, controls, 'sneak', ['sneak', 'sneaking'])
    applyBoolean(payload, controls, 'sprint', ['sprint', 'sprinting'])
    applyBoolean(payload, controls, 'primary', ['primary', 'attack', 'left_button'])
    applyBoolean(payload, controls, 'secondary', ['secondary', 'use', 'right_button'])
  }
  return controls
}

export function sortAppliedActions(actions: AppliedAction[]): AppliedAction[] {
  return [...actions].sort((left, right) => (
    left.tick - right.tick
    || left.apply_sequence - right.apply_sequence
    || left.action_type.localeCompare(right.action_type)
  ))
}

export function tickForPlaybackTime(seconds: number, render: RenderDescriptor): number {
  const frame = clamp(Math.floor(Math.max(0, seconds) * render.fps + 1e-6), 0, render.frame_count - 1)
  return clamp(render.start_tick + frame, render.start_tick, render.end_tick)
}

export function trajectoryBounds(tracks: TrajectoryTrack[]): Bounds2d | null {
  const points = tracks.flatMap(track => track.points)
  if (points.length === 0) {
    return null
  }

  return points.reduce<Bounds2d>((bounds, point) => ({
    maxX: Math.max(bounds.maxX, point.x),
    maxZ: Math.max(bounds.maxZ, point.z),
    minX: Math.min(bounds.minX, point.x),
    minZ: Math.min(bounds.minZ, point.z),
  }), {
    maxX: points[0].x,
    maxZ: points[0].z,
    minX: points[0].x,
    minZ: points[0].z,
  })
}

function applyBoolean(
  payload: Record<string, unknown>,
  controls: ReconstructedControls,
  field: keyof ReconstructedControls,
  aliases: string[],
): void {
  for (const alias of aliases) {
    if (typeof payload[alias] === 'boolean') {
      controls[field] = payload[alias]
      return
    }
  }
}

function decimatePoints(points: TrajectoryPoint[], maximum: number): TrajectoryPoint[] {
  if (maximum <= 0 || points.length === 0) {
    return []
  }
  if (maximum === 1) {
    return [points[0]]
  }
  if (points.length <= maximum) {
    return points
  }

  const result: TrajectoryPoint[] = []
  let previousSourceIndex = -1
  for (let index = 0; index < maximum; index += 1) {
    const sourceIndex = Math.round(index * (points.length - 1) / (maximum - 1))
    const skippedBreak = points
      .slice(previousSourceIndex + 1, sourceIndex + 1)
      .some(point => point.break_before)
    result.push({
      ...points[sourceIndex],
      break_before: points[sourceIndex].break_before || (index > 0 && skippedBreak),
    })
    previousSourceIndex = sourceIndex
  }
  return result
}
