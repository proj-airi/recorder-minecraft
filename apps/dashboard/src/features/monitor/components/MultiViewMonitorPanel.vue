<script setup lang="ts">
import { computed } from 'vue'

import MonitorVideoTile from './MonitorVideoTile.vue'

import { useEditorWorkspaceContext } from '../../editor/composables/useEditorWorkspaceContext'
import { monitorLaneViews } from '../videoTime'

const context = useEditorWorkspaceContext()
// One tile per player lane, so every player at the playhead is visible at once.
const views = computed(() => monitorLaneViews(context.episode(), context.session.playheadTick.value))
</script>

<template>
  <section aria-label="Multi-view monitor" class="h-full min-h-0 flex flex-col bg-neutral-950">
    <div v-if="views.length" class="[container-type:size] min-h-0 flex-1 overflow-auto p-1">
      <!-- Tiles keep the 16:9 video shape instead of stretching to the row height, so the panel
           background, not black letterbox bars inside each tile, takes up the spare space. A single
           row never grows taller than the panel: each tile's width is capped at the 16:9 width of
           the panel height minus the host padding (`cqh` of the size container). Several rows
           fall back to scrolling. -->
      <div class="grid grid-cols-[repeat(auto-fit,minmax(min(18rem,100%),1fr))] min-h-full content-center items-center gap-1">
        <MonitorVideoTile
          v-for="view in views"
          :key="view.lane.id"
          :data-lane-id="view.lane.id"
          :placement="view.placement"
          class="w-[min(100%,calc((100cqh-0.5rem)*16/9))] justify-self-center"
          :session="context.session"
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
