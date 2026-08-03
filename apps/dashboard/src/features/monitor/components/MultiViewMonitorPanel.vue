<script setup lang="ts">
import { computed } from 'vue'

import MonitorVideoTile from './MonitorVideoTile.vue'

import { useEditorWorkspaceContext } from '../../editor/composables/useEditorWorkspaceContext'

const context = useEditorWorkspaceContext()
const views = computed(() => context.episode().tracks.flatMap((track) => {
  const segment = context.episode().segments.find(candidate => candidate.trackId === track.id)
  return track.replay && segment ? [{ segment, source: track.replay, trackId: track.id }] : []
}))
</script>

<template>
  <section aria-label="Multi-view monitor" class="h-full min-h-0 flex flex-col bg-neutral-950">
    <div v-if="views.length" class="min-h-0 flex-1 overflow-auto p-2">
      <div class="grid auto-rows-fr grid-cols-[repeat(auto-fit,minmax(min(18rem,100%),1fr))] min-h-full content-center gap-2">
        <MonitorVideoTile
          v-for="view in views"
          :key="view.trackId"
          :segment="view.segment"
          :session="context.session"
          :source="view.source"
        />
      </div>
    </div>
    <div v-else class="min-h-0 flex flex-1 flex-col items-center justify-center gap-3 p-6 text-center text-neutral-500">
      <span aria-hidden="true" class="i-mingcute-grid-line text-3xl" />
      <p class="m-0 max-w-xs text-xs leading-5">
        Drag replay resources onto the timeline to build a synchronized multi-view monitor.
      </p>
    </div>
  </section>
</template>
