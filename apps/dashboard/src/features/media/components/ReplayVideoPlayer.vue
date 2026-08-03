<script setup lang="ts">
import type { MediaErrorEvent } from 'vidstack'
import type { MediaPlayerElement } from 'vidstack/elements'

import type { ReplayPlayback } from '../composables/useReplayPlayback'

import { onBeforeUnmount, shallowRef, useTemplateRef, watch } from 'vue'

import 'vidstack/player'

const props = defineProps<{
  playback: ReplayPlayback
  sourceUrl: string
  title: string
}>()

const player = useTemplateRef<MediaPlayerElement>('player')
const error = shallowRef<null | string>(null)

watch(player, value => props.playback.bindMedia(value))
watch(() => props.sourceUrl, () => error.value = null)
onBeforeUnmount(() => props.playback.bindMedia(null))

function onError(event: MediaErrorEvent): void {
  error.value = event.detail.message || 'The selected video could not be played.'
}
</script>

<template>
  <div class="relative h-full min-h-0 w-full overflow-hidden bg-black">
    <media-player
      ref="player"
      class="replay-player h-full w-full"
      crossorigin
      load="eager"
      playsinline
      :src="sourceUrl"
      stream-type="on-demand"
      :title="title"
      view-type="video"
      @error="onError"
    >
      <media-provider />
    </media-player>
    <span class="pointer-events-none absolute left-2 top-2 z-10 max-w-[calc(100%-1rem)] truncate rounded-md bg-neutral-950/75 px-2 py-1 text-xs text-neutral-100 shadow-sm backdrop-blur-sm">
      {{ title }}
    </span>
    <p v-if="error" class="absolute inset-0 m-0 flex items-center justify-center bg-neutral-950/90 p-4 text-center text-xs text-red-300">
      {{ error }}
    </p>
  </div>
</template>

<style scoped>
.replay-player {
  --video-bg: #09090b;
  --video-border: 0;
  --video-border-radius: 0;
  --video-brand: #34d399;
  display: block;
}

.replay-player :deep(video) {
  height: 100%;
  object-fit: contain;
  width: 100%;
}
</style>
