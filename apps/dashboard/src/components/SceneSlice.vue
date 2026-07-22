<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'

import { finiteCoordinate } from '../utils'

const props = defineProps<{
  canLoad: boolean
  error?: string
  loading: boolean
  sample?: any
  slice?: any
}>()

const emit = defineEmits<{
  load: [axis: string, coordinate: number, radius: number]
}>()

const axis = ref('y')
const coordinate = ref(-1)
const radius = ref(32)
const canvas = ref<HTMLCanvasElement>()

const scene = computed(() => props.sample?.modalities?.scene)
const policyLabel = computed(() => {
  const scope = props.slice?.scope ?? scene.value?.scope
  const metadataPolicy = props.slice?.metadata_policy ?? scene.value?.metadata_policy
  const sensitive = props.slice?.sensitive ?? scene.value?.sensitive
  const sensitivity = sensitive === true
    ? 'sensitive / privileged metadata'
    : sensitive === false
      ? 'non-sensitive metadata'
      : 'sensitivity unknown'
  return [scope || 'scope unavailable', sensitivity, metadataPolicy || 'metadata policy unavailable'].join(' · ')
})
const entityLabels = computed(() => uniqueLabels(props.slice?.entities, 'entity'))
const blockEntityLabels = computed(() => uniqueLabels(props.slice?.block_entities, 'block entity'))
const emptyMessage = computed(() => {
  if (props.loading)
    return 'Loading scene slice...'
  if (props.error)
    return props.error
  if (props.slice)
    return ''

  const reason = scene.value?.reason
  if (reason === 'scene_not_attached')
    return 'Scene data has not been generated for this dataset.'
  if (typeof reason === 'string' && reason.includes('queued'))
    return 'Scene generation is queued.'
  if (typeof reason === 'string' && reason.includes('failed'))
    return `Scene generation failed: ${reason}`
  if (typeof reason === 'string' && reason)
    return `Scene unavailable: ${reason}`

  return props.sample ? 'Scene data is unavailable for this tick.' : 'Select a tick with scene data.'
})
const note = computed(() => {
  const slice = props.slice
  if (!slice)
    return ''
  const cells = Array.isArray(slice.cells) ? slice.cells : []
  const covered = cells.filter((cell: any) => cell?.covered === true).length
  const paletteSize = Array.isArray(slice.palette) ? slice.palette.length : 0
  return `${covered}/${cells.length} cells covered · ${paletteSize} block states · ${slice.axis}=${slice.coordinate} · ${(slice.entities || []).length} entities · ${(slice.block_entities || []).length} block entities; dark cells are unknown`
})
const canRequest = computed(() => props.canLoad
  && Number.isInteger(coordinate.value)
  && Number.isInteger(radius.value)
  && radius.value >= 1
  && radius.value <= 64)

function uniqueLabels(items: any, fallback: string) {
  return [...new Set((Array.isArray(items) ? items : []).map(item => sceneLabel(item, fallback)))]
}

function sceneLabel(item: any, fallback: string) {
  return item?.name
    || item?.custom_name
    || item?.type_id
    || item?.type
    || item?.entity_type
    || item?.block_entity_type
    || fallback
}

function sceneProjection(item: any, slice: any) {
  const projection = item?.projection || item?.projected
  if (finiteCoordinate(projection?.column) && finiteCoordinate(projection?.row))
    return projection

  const position = item?.position || item?.world_position
  const coordinateFor = (coordinateAxis: string) => {
    if (Array.isArray(position))
      return position[{ x: 0, y: 1, z: 2 }[coordinateAxis] as number]
    return position?.[coordinateAxis]
  }
  const column = coordinateFor(slice.column_axis)
  const row = coordinateFor(slice.row_axis)
  return finiteCoordinate(column) && finiteCoordinate(row) ? { column, row } : null
}

function drawSceneMarker(context: CanvasRenderingContext2D, item: any, slice: any, kind: 'block-entity' | 'entity') {
  const projection = sceneProjection(item, slice)
  if (!projection)
    return
  const markerOffset = kind === 'block-entity' ? 0.5 : 0
  const x = projection.column - slice.column_origin + markerOffset
  const y = projection.row - slice.row_origin + markerOffset
  if (x < 0 || x > slice.width || y < 0 || y > slice.height)
    return

  context.save()
  if (kind === 'entity') {
    const { min_column: minColumn, max_column: maxColumn, min_row: minRow, max_row: maxRow } = projection
    if ([minColumn, maxColumn, minRow, maxRow].every(finiteCoordinate)) {
      context.fillStyle = '#ffcf5a55'
      context.fillRect(
        minColumn - slice.column_origin,
        minRow - slice.row_origin,
        Math.max(0.15, maxColumn - minColumn),
        Math.max(0.15, maxRow - minRow),
      )
    }
    context.fillStyle = '#ffcf5a'
    context.strokeStyle = '#2b1f05'
    context.lineWidth = 0.35
    context.beginPath()
    context.arc(x, y, 0.65, 0, Math.PI * 2)
    context.fill()
    context.stroke()
  }
  else {
    context.translate(x, y)
    context.rotate(Math.PI / 4)
    context.fillStyle = '#61d6d0'
    context.strokeStyle = '#082c2a'
    context.lineWidth = 0.3
    context.fillRect(-0.55, -0.55, 1.1, 1.1)
    context.strokeRect(-0.55, -0.55, 1.1, 1.1)
  }
  context.restore()
}

function cellColor(slice: any, cell: any) {
  const palette = Array.isArray(slice?.palette) ? slice.palette : []
  const paletteIndex = cell?.palette_index
  if (
    cell?.covered !== true
    || !Number.isInteger(paletteIndex)
    || paletteIndex < 0
    || paletteIndex >= palette.length
  )
    return [24, 29, 26]
  return Array.isArray(cell.color) && cell.color.length === 3
    ? cell.color
    : [120, 160, 125]
}

async function draw(slice: any) {
  await nextTick()
  const context = canvas.value?.getContext('2d')
  if (!canvas.value || !context)
    return
  if (!slice) {
    context.clearRect(0, 0, canvas.value.width, canvas.value.height)
    canvas.value.width = 0
    canvas.value.height = 0
    canvas.value.style.width = ''
    canvas.value.style.height = ''
    return
  }

  canvas.value.width = slice.width
  canvas.value.height = slice.height
  const image = context.createImageData(slice.width, slice.height)
  ;(slice.cells || []).forEach((cell: any, cellIndex: number) => {
    const color = cellColor(slice, cell)
    image.data.set([...color, 255], cellIndex * 4)
  })
  context.putImageData(image, 0, 0)
  ;(slice.entities || []).forEach((entity: any) => drawSceneMarker(context, entity, slice, 'entity'))
  ;(slice.block_entities || []).forEach((entity: any) => drawSceneMarker(context, entity, slice, 'block-entity'))
  canvas.value.style.width = `${Math.min(720, slice.width * 8)}px`
  canvas.value.style.height = `${Math.min(720, slice.height * 8)}px`
}

function requestSlice() {
  if (canRequest.value)
    emit('load', axis.value, coordinate.value, radius.value)
}

watch(
  [() => props.sample, axis, () => props.canLoad],
  async () => {
    const value = props.sample?.state?.position?.[axis.value]
    coordinate.value = finiteCoordinate(value) ? Math.floor(value) : -1
    await nextTick()
    requestSlice()
  },
  { immediate: true },
)
watch(() => props.slice, draw, { immediate: true })
</script>

<template>
  <details id="scene-details" open>
    <summary>Scene slice · {{ policyLabel }}</summary>
    <div class="scene-tools">
      <label>Axis
        <select v-model="axis">
          <option>x</option>
          <option>y</option>
          <option>z</option>
        </select>
      </label>
      <label>World coordinate
        <input v-model.number="coordinate" type="number" step="1" @change="requestSlice">
      </label>
      <label>Radius
        <input v-model.number="radius" type="number" min="1" max="64" step="1" @change="requestSlice">
      </label>
      <button class="quiet" :disabled="!canRequest || loading" @click="requestSlice">
        {{ loading ? 'Loading...' : 'Reload slice' }}
      </button>
    </div>
    <div class="scene-canvas-wrap">
      <canvas
        ref="canvas"
        class="scene-canvas"
        role="img"
        :aria-label="slice ? `${slice.axis} axis scene slice at world coordinate ${slice.coordinate}` : emptyMessage"
      />
      <p v-if="!slice" class="empty scene-empty">
        {{ emptyMessage }}
      </p>
    </div>
    <div class="scene-legend">
      <span v-for="label in entityLabels" :key="`entity:${label}`" class="scene-legend-item">
        <i class="scene-legend-marker" />{{ label }}
      </span>
      <span v-for="label in blockEntityLabels" :key="`block-entity:${label}`" class="scene-legend-item">
        <i class="scene-legend-marker block-entity" />{{ label }}
      </span>
    </div>
    <p class="muted scene-note">
      {{ note }}
    </p>
  </details>
</template>
