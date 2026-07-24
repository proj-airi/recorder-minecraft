<script setup lang="ts">
import { computed, onMounted, useTemplateRef, watch } from 'vue'

import type { PlayerTickState, SceneCell, SceneSliceResponse } from '../types/viewer'
import { clamp, colorForBlock } from '../utils/viewer'

const props = defineProps<{
  loading: boolean
  radius: number
  slice: SceneSliceResponse | null
  state: PlayerTickState | null
  y: number
}>()

const emit = defineEmits<{
  radiusChange: [radius: number]
  yChange: [y: number]
}>()

const canvas = useTemplateRef<HTMLCanvasElement>('canvas')

const knownBlockCount = computed(() => props.slice?.cells.filter(cell => cell.kind === 'block').length ?? 0)
const unknownCount = computed(() => {
  const slice = props.slice
  if (!slice) {
    return 0
  }
  const observed = slice.cells.filter(cell => cell.kind !== 'unknown').length
  return Math.max(0, slice.width * slice.height - observed)
})

onMounted(draw)
watch([() => props.slice, () => props.state], draw)

function onYChange(event: Event): void {
  emit('yChange', Number.parseInt((event.currentTarget as HTMLInputElement).value, 10))
}

function onRadiusChange(event: Event): void {
  emit('radiusChange', Number.parseInt((event.currentTarget as HTMLSelectElement).value, 10))
}

function draw(): void {
  const element = canvas.value
  const slice = props.slice
  if (!element || !slice) {
    return
  }

  const cellSize = Math.floor(clamp(520 / Math.max(slice.width, slice.height), 7, 18))
  const width = slice.width * cellSize
  const height = slice.height * cellSize
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
  const cells = new Map(slice.cells.map(cell => [`${cell.x}:${cell.z}`, cell]))
  const minX = slice.center_x - slice.radius
  const minZ = slice.center_z - slice.radius

  for (let row = 0; row < slice.height; row += 1) {
    for (let column = 0; column < slice.width; column += 1) {
      const x = minX + column
      const z = minZ + row
      const cell = cells.get(`${x}:${z}`)
      drawCell(context, cell, column * cellSize, (slice.height - row - 1) * cellSize, cellSize)
    }
  }

  const state = props.state
  if (state && state.dimension === slice.dimension && Math.floor(state.position.y) === slice.y) {
    const playerX = (state.position.x - minX) * cellSize
    const playerY = (slice.height - (state.position.z - minZ)) * cellSize
    context.beginPath()
    context.arc(playerX, playerY, Math.max(3, cellSize * .32), 0, Math.PI * 2)
    context.fillStyle = '#fff4b8'
    context.fill()
    context.lineWidth = 2
    context.strokeStyle = '#1b1300'
    context.stroke()
  }
}

function drawCell(
  context: CanvasRenderingContext2D,
  cell: SceneCell | undefined,
  x: number,
  y: number,
  size: number,
): void {
  if (!cell || cell.kind === 'unknown') {
    context.fillStyle = '#18201b'
    context.fillRect(x, y, size, size)
    context.fillStyle = '#242d27'
    context.fillRect(x, y, Math.ceil(size / 2), Math.ceil(size / 2))
    context.fillRect(x + Math.floor(size / 2), y + Math.floor(size / 2), Math.ceil(size / 2), Math.ceil(size / 2))
  }
  else if (cell.kind === 'air') {
    context.fillStyle = '#080d0a'
    context.fillRect(x, y, size, size)
  }
  else {
    context.fillStyle = colorForBlock(cell.block_id)
    context.fillRect(x, y, size, size)
  }

  context.strokeStyle = '#26312966'
  context.lineWidth = 1
  context.strokeRect(x + .5, y + .5, size - 1, size - 1)

  if (cell?.block_entity_id !== null && cell?.block_entity_id !== undefined) {
    context.fillStyle = cell.contents_known ? '#65dad2' : '#f0b85d'
    const marker = Math.max(2, size * .22)
    context.fillRect(x + size / 2 - marker / 2, y + size / 2 - marker / 2, marker, marker)
  }
}
</script>

<template>
  <section class="panel scene-panel" aria-labelledby="scene-title">
    <div class="panel-heading">
      <div>
        <p class="eyebrow">
          Bounded reconstruction
        </p>
        <h2 id="scene-title">
          2D scene slice
        </h2>
      </div>
      <span class="scene-status" :class="{ loading }">
        {{ loading ? 'querying…' : `${knownBlockCount} blocks` }}
      </span>
    </div>

    <div class="scene-controls">
      <label>
        <span>Y level</span>
        <input type="number" min="-2048" max="2048" step="1" :value="y" @change="onYChange">
      </label>
      <label>
        <span>Radius</span>
        <select :value="radius" @change="onRadiusChange">
          <option :value="8">8 blocks</option>
          <option :value="16">16 blocks</option>
          <option :value="24">24 blocks</option>
          <option :value="32">32 blocks</option>
        </select>
      </label>
      <div class="scene-coordinates">
        <span>{{ slice?.dimension ?? state?.dimension ?? '—' }}</span>
        <strong v-if="state">{{ state.position.x.toFixed(1) }}, {{ state.position.z.toFixed(1) }}</strong>
      </div>
    </div>

    <div class="scene-canvas-wrap">
      <canvas ref="canvas" class="scene-canvas" aria-label="Bounded reconstructed block slice" />
      <p v-if="!slice" class="canvas-empty">
        No scene slice is available at this tick.
      </p>
    </div>
    <div class="scene-legend">
      <span><i class="legend-air" />Air</span>
      <span><i class="legend-block" />Known block</span>
      <span><i class="legend-unknown" />Unknown ({{ unknownCount }})</span>
      <span><i class="legend-container" />Block entity</span>
    </div>
    <p class="scene-note">
      Unknown cells are never shown as air. Amber block-entity markers mean container contents were not observed.
    </p>
  </section>
</template>

<style scoped>
.scene-panel { min-width: 0; padding: 1rem; }
.panel-heading { display: flex; justify-content: space-between; align-items: start; gap: 1rem; }
.scene-status { color: var(--accent); font: 680 .68rem/1 var(--mono); }.scene-status.loading { opacity: .45; }
.scene-controls { display: grid; grid-template-columns: 7rem 8rem 1fr; gap: .55rem; align-items: end; margin-top: .8rem; }
.scene-controls label { display: grid; gap: .3rem; }.scene-controls label span { color: var(--muted); font-size: .62rem; font-weight: 700; letter-spacing: .06em; text-transform: uppercase; }
.scene-controls input, .scene-controls select { width: 100%; border: 1px solid var(--line); border-radius: .4rem; outline: none; background: #0a100c; color: var(--text); padding: .5rem; font: 650 .72rem/1 var(--mono); }
.scene-controls input:focus, .scene-controls select:focus { border-color: var(--accent); }
.scene-coordinates { min-width: 0; text-align: right; }.scene-coordinates span, .scene-coordinates strong { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }.scene-coordinates span { color: var(--muted); font-size: .63rem; }.scene-coordinates strong { margin-top: .2rem; font: 680 .7rem/1 var(--mono); }
.scene-canvas-wrap { position: relative; min-height: 310px; display: grid; place-items: center; margin-top: .7rem; overflow: auto; border: 1px solid var(--line); border-radius: .65rem; background: #070b08; }
.scene-canvas { display: block; image-rendering: pixelated; }
.canvas-empty { position: absolute; inset: 0; display: grid; place-content: center; margin: 0; color: var(--muted); font-size: .78rem; pointer-events: none; }
.scene-legend { display: flex; flex-wrap: wrap; gap: .45rem .8rem; margin-top: .65rem; color: var(--muted); font-size: .64rem; }
.scene-legend span { display: inline-flex; align-items: center; gap: .3rem; }.scene-legend i { width: .65rem; height: .65rem; border: 1px solid #39473e; }
.legend-air { background: #080d0a; }.legend-block { background: #58725f; }.legend-unknown { background: repeating-conic-gradient(#242d27 0 25%, #18201b 0 50%) 50% / 4px 4px; }.legend-container { background: #65dad2; transform: rotate(45deg) scale(.75); }
.scene-note { margin: .6rem 0 0; color: var(--muted); font-size: .66rem; line-height: 1.45; }
@media (max-width: 520px) { .scene-controls { grid-template-columns: 1fr 1fr; }.scene-coordinates { grid-column: 1 / -1; text-align: left; } }
</style>
