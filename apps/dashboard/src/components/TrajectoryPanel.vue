<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { finiteCoordinate, shortDimension } from '../utils'

const props = defineProps<{
  currentRecord?: any
  trajectory?: any
}>()

const canvas = ref<HTMLCanvasElement>()
const trajectoryColors = ['#78e08f', '#74b9ff', '#f1c75b', '#ff8f70', '#c7a6ff', '#61d6d0', '#f58bc8', '#b4d273']

const tracks = computed(() => props.trajectory?.tracks || [])
const note = computed(() => {
  if (!props.trajectory?.bounds || !tracks.value.length)
    return ''
  const notes = [
    `${props.trajectory.total_points} indexed positions`,
    props.trajectory.downsampled ? `${props.trajectory.returned_points} display points` : 'full resolution',
  ]
  if (props.trajectory.omitted_tracks)
    notes.push(`${props.trajectory.omitted_tracks} tracks omitted by display bound`)
  const position = props.currentRecord?.state?.position
  if (position && finiteCoordinate(position.x) && finiteCoordinate(position.y) && finiteCoordinate(position.z))
    notes.push(`current ${position.x.toFixed(2)}, ${position.y.toFixed(2)}, ${position.z.toFixed(2)}`)

  return notes.join(' · ')
})

function drawTrajectory() {
  if (!canvas.value)
    return
  const context = canvas.value.getContext('2d')
  if (!context)
    return
  const width = Math.max(320, Math.floor(canvas.value.getBoundingClientRect().width || 640))
  const height = 352
  const scaleFactor = Math.max(1, window.devicePixelRatio || 1)
  canvas.value.width = Math.floor(width * scaleFactor)
  canvas.value.height = Math.floor(height * scaleFactor)
  context.setTransform(scaleFactor, 0, 0, scaleFactor, 0, 0)
  context.clearRect(0, 0, width, height)

  const bounds = props.trajectory?.bounds
  if (!bounds || !tracks.value.length)
    return

  const padding = { top: 24, right: 24, bottom: 32, left: 40 }
  const plotWidth = width - padding.left - padding.right
  const plotHeight = height - padding.top - padding.bottom
  const rangeX = Math.max(0.001, bounds.max_x - bounds.min_x)
  const rangeZ = Math.max(0.001, bounds.max_z - bounds.min_z)
  const scale = Math.min(plotWidth / rangeX, plotHeight / rangeZ)
  const contentWidth = rangeX * scale
  const contentHeight = rangeZ * scale
  const originX = padding.left + (plotWidth - contentWidth) / 2
  const originY = padding.top + (plotHeight - contentHeight) / 2
  const project = (point: any) => ({
    x: originX + (point.x - bounds.min_x) * scale,
    y: originY + (point.z - bounds.min_z) * scale,
  })

  context.fillStyle = '#080d0a'
  context.fillRect(0, 0, width, height)
  context.strokeStyle = '#213027'
  context.fillStyle = '#74877a'
  context.lineWidth = 1
  context.font = '10px ui-monospace, SFMono-Regular, Menlo, monospace'
  for (let index = 0; index <= 4; index += 1) {
    const x = padding.left + plotWidth * index / 4
    const y = padding.top + plotHeight * index / 4
    context.beginPath()
    context.moveTo(x, padding.top)
    context.lineTo(x, padding.top + plotHeight)
    context.stroke()
    context.beginPath()
    context.moveTo(padding.left, y)
    context.lineTo(padding.left + plotWidth, y)
    context.stroke()
  }
  context.fillText(`X ${bounds.min_x.toFixed(1)}`, padding.left, height - 10)
  const maxXLabel = `X ${bounds.max_x.toFixed(1)}`
  context.fillText(maxXLabel, width - padding.right - context.measureText(maxXLabel).width, height - 10)
  context.save()
  context.translate(12, padding.top + plotHeight / 2)
  context.rotate(-Math.PI / 2)
  context.fillText(`Z ${bounds.min_z.toFixed(1)} -> ${bounds.max_z.toFixed(1)}`, -54, 0)
  context.restore()

  tracks.value.forEach((track: any, trackIndex: number) => {
    const color = trajectoryColors[trackIndex % trajectoryColors.length]
    context.strokeStyle = color
    context.lineWidth = 2
    context.lineJoin = 'round'
    context.lineCap = 'round'
    context.beginPath()
    ;(track.points || []).forEach((point: any, pointIndex: number) => {
      const projected = project(point)
      if (pointIndex === 0 || !point.continuous_from_previous)
        context.moveTo(projected.x, projected.y)
      else
        context.lineTo(projected.x, projected.y)
    })
    context.stroke()
  })

  const position = props.currentRecord?.state?.position
  if (position && finiteCoordinate(position.x) && finiteCoordinate(position.z)) {
    const current = project(position)
    context.beginPath()
    context.fillStyle = '#edf5ef'
    context.strokeStyle = '#071109'
    context.lineWidth = 2
    context.arc(current.x, current.y, 5, 0, Math.PI * 2)
    context.fill()
    context.stroke()
  }
}

watch(() => [props.trajectory, props.currentRecord], () => nextTick(drawTrajectory), { deep: true })
onMounted(() => {
  drawTrajectory()
  window.addEventListener('resize', drawTrajectory)
})
onBeforeUnmount(() => {
  window.removeEventListener('resize', drawTrajectory)
})
</script>

<template>
  <section class="trajectory-card" aria-labelledby="trajectory-heading">
    <div class="observation-heading">
      <div><p class="eyebrow">NO GUI RENDER REQUIRED</p><h2 id="trajectory-heading">Player trajectory</h2></div>
      <span class="axis-note">top-down X / Z · N ↑</span>
    </div>
    <div class="trajectory-canvas-wrap">
      <canvas ref="canvas" role="img" aria-label="Top-down player trajectory" />
      <p v-if="!trajectory?.bounds || !tracks.length" class="empty">
        {{ trajectory ? 'No indexed positions match these filters.' : 'Loading indexed positions...' }}
      </p>
    </div>
    <div class="trajectory-legend">
      <span v-for="(track, trackIndex) in tracks" :key="`${track.player_uuid}:${track.dimension}`" class="trajectory-legend-item">
        <i class="trajectory-swatch" :style="{ background: trajectoryColors[Number(trackIndex) % trajectoryColors.length] }" />
        {{ track.player_name || track.player_uuid }} · {{ shortDimension(track.dimension) }} · {{ track.horizontal_distance_blocks.toFixed(1) }} blocks
      </span>
    </div>
    <p class="muted trajectory-note">
      {{ note }}
    </p>
  </section>
</template>
