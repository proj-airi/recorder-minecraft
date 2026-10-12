<script setup lang="ts">
import { shallowRef, watch } from 'vue'

const props = defineProps<{
  /** null renders the fallback slot directly. */
  url: null | string
}>()

// The default slot (a text or icon fallback) shows until the image loads and stays when it fails,
// so a missing jar or texture never leaves a blank space.
const status = shallowRef<'error' | 'loading' | 'ready'>('loading')
watch(() => props.url, (url) => {
  status.value = url ? 'loading' : 'error'
}, { immediate: true })
</script>

<template>
  <span class="pixel-image-root" :data-icon-status="status">
    <img
      v-if="url && status !== 'error'"
      v-show="status === 'ready'"
      alt=""
      class="pixel-image"
      draggable="false"
      :src="url"
      @error="status = 'error'"
      @load="status = 'ready'"
    >
    <slot v-if="status !== 'ready'" />
  </span>
</template>

<style scoped>
.pixel-image-root {
  align-items: center;
  display: flex;
  justify-content: center;
  min-height: 0;
  min-width: 0;
}

.pixel-image {
  height: 100%;
  width: 100%;
  /* Minecraft textures are pixel art; keep texels square when scaled. */
  image-rendering: pixelated;
  object-fit: contain;
}
</style>
