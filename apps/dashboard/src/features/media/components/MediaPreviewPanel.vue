<script setup lang="ts">
import { computed } from 'vue'

import PreviewPlaybackControls from './PreviewPlaybackControls.vue'
import ReplayVideoPlayer from './ReplayVideoPlayer.vue'

import { useEditorWorkspaceContext } from '../../editor/composables/useEditorWorkspaceContext'

const { catalog, replayPlayback } = useEditorWorkspaceContext()
const sourceUrl = computed(() => {
  const path = catalog.selectedReplay.value?.video?.url
  return path ? new URL(path, window.location.href).toString() : null
})
</script>

<template>
  <section class="h-full min-h-0 flex flex-col bg-neutral-950" aria-label="Video preview">
    <div class="relative min-h-0 flex flex-1 items-center justify-center overflow-hidden">
      <ReplayVideoPlayer
        v-if="sourceUrl"
        :playback="replayPlayback"
        :source-url="sourceUrl"
        :title="catalog.selectedReplay.value?.playerName ?? 'Replay preview'"
      />
      <p v-else-if="catalog.selectedReplay.value?.validationError" class="m-0 max-w-sm p-4 text-center text-sm text-red-300">
        {{ catalog.selectedReplay.value.validationError }}
      </p>
      <p v-else-if="!sourceUrl" class="m-0 max-w-sm p-4 text-center text-xs text-neutral-500">
        Select a replay with a composed video to preview it.
      </p>
    </div>
    <PreviewPlaybackControls :disabled="!sourceUrl" :playback="replayPlayback" />
  </section>
</template>
