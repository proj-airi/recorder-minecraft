<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'

const props = defineProps<{
  canLoad: boolean
  sample?: any
}>()

const emit = defineEmits<{
  load: [axis: string, index: number]
}>()

const axis = ref('y')
const index = ref(-1)
const canvas = ref<HTMLCanvasElement>()
const note = ref('')

const shape = computed(() => props.sample?.modalities?.voxels?.shape)

watch([shape, axis], () => {
  const value = shape.value?.[axis.value]
  index.value = Number.isInteger(value) && value > 0 ? Math.floor(value / 2) : -1
}, { immediate: true })

async function draw(slice: any) {
  await nextTick()
  const context = canvas.value?.getContext('2d')
  if (!canvas.value || !context || !slice)
    return
  canvas.value.width = slice.width
  canvas.value.height = slice.height
  const image = context.createImageData(slice.width, slice.height)
  ;(slice.cells || []).forEach((cell: any, cellIndex: number) => {
    const color = cell.covered ? (cell.color || [120, 160, 125]) : [28, 32, 30]
    image.data.set([...color, 255], cellIndex * 4)
  })
  context.putImageData(image, 0, 0)
  canvas.value.style.width = `${Math.min(640, slice.width * 8)}px`
  note.value = `${slice.covered_cells}/${slice.total_cells} slice cells covered · ${slice.axis}=${slice.world_coordinate}; dark cells are unknown`
}

defineExpose({ draw })
</script>

<template>
  <details id="voxel-details">
    <summary>Voxel slice</summary>
    <div class="voxel-tools">
      <select v-model="axis">
        <option>x</option>
        <option>y</option>
        <option>z</option>
      </select>
      <input v-model.number="index" type="number">
      <button class="quiet" :disabled="!canLoad" @click="emit('load', axis, index)">
        Load slice
      </button>
    </div>
    <canvas ref="canvas" />
    <p class="muted">
      {{ note }}
    </p>
  </details>
</template>
