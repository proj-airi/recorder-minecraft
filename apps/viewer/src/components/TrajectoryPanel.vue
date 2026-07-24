<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, useTemplateRef, watch } from 'vue'

import type { PlayerTickState, TrajectoryResponse, TrajectoryTrack } from '../types/viewer'
import { boundTrajectoryTracks, projectTrajectoryPoint, trajectoryBounds } from '../utils/viewer'

const props = defineProps<{
  state: PlayerTickState | null
  trajectory: TrajectoryResponse | null
}>()

const canvas = useTemplateRef<HTMLCanvasElement>('canvas')
const canvasWrap = useTemplateRef<HTMLDivElement>('canvasWrap')
let resizeObserver: ResizeObserver | null = null

const tracks = computed(() => boundTrajectoryTracks(props.trajectory?.tracks ?? [], 5000))
const dimensionCount = computed(() => new Set(tracks.value.map(track => track.dimension)).size)

onMounted(() => {
  resizeObserver = new ResizeObserver(draw)
  if (canvasWrap.value) {
    resizeObserver.observe(canvasWrap.value)
  }
  draw()
})

onBeforeUnmount(() => resizeObserver?.disconnect())
watch([tracks, () => props.state], draw)

function draw(): void {
  const element = canvas.value
  const wrapper = canvasWrap.value
  if (!element || !wrapper) {
    return
  }

  const width = Math.max(280, Math.floor(wrapper.clientWidth))
  const height = 310
  const ratio = window.devicePixelRatio || 1
  element.width = Math.floor(width * ratio)
  element.height = Math.floor(height * ratio)
  element.style.width = `${width}px`
  element.style.height = `${height}px`

  const context = element.getContext('2d')
  if (!context) {
    return
  }
  context.setTransform(ratio, 0, 0, ratio, 0, 0)
  context.clearRect(0, 0, width, height)
  context.fillStyle = '#070b08'
  context.fillRect(0, 0, width, height)

  drawGrid(context, width, height)
  const bounds = trajectoryBounds(tracks.value)
  if (!bounds) {
    return
  }

  for (const [trackIndex, track] of tracks.value.entries()) {
    drawTrack(context, track, track.color ?? trackColor(trackIndex), bounds, width, height)
  }

  const state = props.state
  if (state) {
    const current = projectTrajectoryPoint(state.position, bounds, width, height, 24)
    context.beginPath()
    context.arc(current.x, current.y, 5, 0, Math.PI * 2)
    context.fillStyle = '#f7f1ca'
    context.fill()
    context.lineWidth = 2
    context.strokeStyle = '#0a100c'
    context.stroke()
  }
}

function drawTrack(
  context: CanvasRenderingContext2D,
  track: TrajectoryTrack,
  color: string,
  bounds: NonNullable<ReturnType<typeof trajectoryBounds>>,
  width: number,
  height: number,
): void {
  context.lineWidth = 2
  context.lineJoin = 'round'
  context.lineCap = 'round'
  context.strokeStyle = color
  context.beginPath()
  let hasPath = false
  for (const point of track.points) {
    const projected = projectTrajectoryPoint(point, bounds, width, height, 24)
    if (!hasPath || point.break_before) {
      context.moveTo(projected.x, projected.y)
    }
    else {
      context.lineTo(projected.x, projected.y)
    }
    hasPath = true
  }
  context.stroke()
}

function drawGrid(context: CanvasRenderingContext2D, width: number, height: number): void {
  context.strokeStyle = '#16231b'
  context.lineWidth = 1
  for (let x = 20; x < width; x += 32) {
    context.beginPath()
    context.moveTo(x, 0)
    context.lineTo(x, height)
    context.stroke()
  }
  for (let y = 20; y < height; y += 32) {
    context.beginPath()
    context.moveTo(0, y)
    context.lineTo(width, y)
    context.stroke()
  }
}

function trackColor(index: number): string {
  return ['#75dd93', '#70b7ff', '#f5c96b', '#d890ff', '#ff8b80'][index % 5]
}
</script>

<template>
  <section class="panel trajectory-panel" aria-labelledby="trajectory-title">
    <div class="panel-heading">
      <div>
        <p class="eyebrow">
          Bounded query
        </p>
        <h2 id="trajectory-title">
          Top-down trajectory
        </h2>
      </div>
      <div class="trajectory-stats">
        <strong>{{ trajectory?.total_points.toLocaleString() ?? 0 }}</strong>
        <span>indexed points · {{ dimensionCount }} dimension{{ dimensionCount === 1 ? '' : 's' }}</span>
      </div>
    </div>
    <div ref="canvasWrap" class="canvas-wrap">
      <canvas ref="canvas" class="trajectory-canvas" aria-label="Top-down X and Z player trajectory" />
      <p v-if="tracks.length === 0" class="canvas-empty">
        No trajectory points are available.
      </p>
      <span class="axis-x">+X →</span>
      <span class="axis-z">+Z ↑</span>
    </div>
    <div class="legend">
      <span v-for="(track, index) in tracks" :key="`${track.connection_id}:${track.dimension}`">
        <i :style="{ backgroundColor: track.color ?? trackColor(index) }" />
        {{ track.dimension }} · {{ track.points.length.toLocaleString() }} shown
      </span>
      <span v-if="trajectory?.truncated" class="bounded-note">display decimated; totals remain exact</span>
    </div>
  </section>
</template>

<style scoped>
.trajectory-panel { min-width: 0; padding: 1rem; }
.panel-heading { display: flex; justify-content: space-between; align-items: start; gap: 1rem; }
.trajectory-stats { color: var(--muted); text-align: right; font-size: .64rem; }.trajectory-stats strong { display: block; color: #dce9df; font: 700 .8rem/1.2 var(--mono); }.trajectory-stats span { display: block; margin-top: .15rem; }
.canvas-wrap { position: relative; min-height: 310px; margin-top: .8rem; overflow: hidden; border: 1px solid var(--line); border-radius: .65rem; background: #070b08; }
.trajectory-canvas { display: block; max-width: 100%; }
.canvas-empty { position: absolute; inset: 0; display: grid; place-content: center; margin: 0; color: var(--muted); font-size: .78rem; pointer-events: none; }
.axis-x, .axis-z { position: absolute; color: #779083; font: 650 .58rem/1 var(--mono); pointer-events: none; }.axis-x { right: .55rem; bottom: .5rem; }.axis-z { left: .55rem; top: .5rem; }
.legend { display: flex; flex-wrap: wrap; gap: .45rem .8rem; margin-top: .65rem; color: var(--muted); font-size: .64rem; }
.legend span { display: inline-flex; align-items: center; gap: .3rem; }.legend i { width: .55rem; height: .55rem; border-radius: 50%; }.legend .bounded-note { margin-left: auto; color: var(--warning); }
</style>
