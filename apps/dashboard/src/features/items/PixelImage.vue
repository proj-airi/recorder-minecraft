<script setup lang="ts">
import { shallowRef, watch } from 'vue'

const props = defineProps<{
  alt?: string
  /** null renders the fallback slot directly. */
  url: null | string
}>()
const emit = defineEmits<{
  status: [status: 'error' | 'loading' | 'ready']
}>()

// The default slot (a text or icon fallback) shows until the image loads and stays when it fails,
// so a missing jar or texture never leaves a blank space.
const status = shallowRef<'error' | 'loading' | 'ready'>('loading')
watch(() => props.url, (url) => {
  status.value = url ? 'loading' : 'error'
}, { immediate: true })
watch(status, value => emit('status', value), { immediate: true })
</script>

<template>
  <span class="min-h-0 min-w-0 flex items-center justify-center" :data-icon-status="status">
    <!--
      No referrer: some URLs (player faces) go to a third-party render service. Not
      `loading="lazy"`: the image is display:none (v-show) until it loads, and browsers never load a
      lazy image that is not rendered, so it would stay on the fallback forever.
    -->
    <img
      v-if="url && status !== 'error'"
      v-show="status === 'ready'"
      :alt="alt ?? ''"
      class="[image-rendering:pixelated] h-full w-full object-contain"
      decoding="async"
      draggable="false"
      referrerpolicy="no-referrer"
      :src="url"
      @error="status = 'error'"
      @load="status = 'ready'"
    >
    <slot v-if="status !== 'ready'" />
  </span>
</template>
