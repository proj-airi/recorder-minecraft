import type { AppliedAction, RenderDescriptor, TrajectoryTrack } from '../src/types/viewer'

import { describe, expect, it } from 'vitest'

import {
  boundTrajectoryTracks,
  colorForBlock,
  playbackTimeForTick,
  projectTrajectoryPoint,
  reconstructControls,
  sortAppliedActions,
  tickForPlaybackTime,
  trajectoryBounds,
} from '../src/utils/viewer'

describe('action ordering', () => {
  it('orders actions by tick and application sequence without mutating input', () => {
    const input: AppliedAction[] = [
      { action_type: 'use', apply_sequence: 2, detail: null, label: 'Use', payload: {}, tick: 8 },
      { action_type: 'move', apply_sequence: 1, detail: null, label: 'Move', payload: {}, tick: 8 },
      { action_type: 'look', apply_sequence: 9, detail: null, label: 'Look', payload: {}, tick: 7 },
    ]
    const output = sortAppliedActions(input)
    expect(output.map(action => action.action_type)).toEqual(['look', 'move', 'use'])
    expect(input.map(action => action.action_type)).toEqual(['use', 'move', 'look'])
  })

  it('reconstructs held controls from the latest ordered control observation', () => {
    const fallback = {
      backward: null,
      forward: null,
      jump: null,
      left: null,
      primary: null,
      right: null,
      secondary: null,
      sneak: false,
      sprint: false,
    }
    const actions: AppliedAction[] = [
      { action_type: 'control_state', apply_sequence: 2, detail: null, label: 'Controls', payload: { forward: true, sprinting: true }, tick: 8 },
      { action_type: 'control_state', apply_sequence: 3, detail: null, label: 'Controls', payload: { forward: false, jump: true }, tick: 8 },
    ]
    expect(reconstructControls(actions, fallback)).toEqual({
      ...fallback,
      forward: false,
      jump: true,
      sprint: true,
    })
  })
})

describe('bounded trajectory rendering', () => {
  const track = (dimension: string, count: number): TrajectoryTrack => ({
    color: null,
    connection_id: 'connection',
    dimension,
    player_uuid: 'player',
    points: Array.from({ length: count }, (_, index) => ({
      break_before: index === 4,
      tick: index,
      x: index,
      z: index * 2,
    })),
  })

  it('keeps the first and last points while bounding client draw work', () => {
    const output = boundTrajectoryTracks([track('overworld', 100)], 10)
    expect(output[0].points).toHaveLength(10)
    expect(output[0].points[0].tick).toBe(0)
    expect(output[0].points.at(-1)?.tick).toBe(99)
    expect(output[0].points.some(point => point.break_before)).toBe(true)
  })

  it('projects X/Z bounds into padded canvas coordinates', () => {
    const tracks = [track('overworld', 3)]
    const bounds = trajectoryBounds(tracks)
    expect(bounds).not.toBeNull()
    expect(projectTrajectoryPoint({ x: 0, z: 0 }, bounds!, 200, 100, 10)).toEqual({ x: 80, y: 90 })
    expect(projectTrajectoryPoint({ x: 2, z: 4 }, bounds!, 200, 100, 10)).toEqual({ x: 120, y: 10 })
  })
})

describe('render timeline utilities', () => {
  const render: RenderDescriptor = {
    codec: 'h264',
    end_tick: 139,
    fps: 20,
    frame_count: 40,
    media_type: 'video/mp4',
    path: 'renders/fpv.mp4',
    pixel_format: 'yuv420p',
    sha256: '0'.repeat(64),
    size: 123,
    start_tick: 100,
    timeline_complete: true,
  }

  it('maps constant-rate frame positions to ticks at exact frame boundaries', () => {
    expect(tickForPlaybackTime(0, render)).toBe(100)
    expect(tickForPlaybackTime(0.999, render)).toBe(119)
    expect(tickForPlaybackTime(100, render)).toBe(139)
    expect(playbackTimeForTick(120, render)).toBe(1)
  })
})

describe('block palette', () => {
  it('is deterministic and distinguishes block identifiers', () => {
    expect(colorForBlock('minecraft:stone')).toBe(colorForBlock('minecraft:stone'))
    expect(colorForBlock('minecraft:stone')).not.toBe(colorForBlock('minecraft:oak_planks'))
  })
})
