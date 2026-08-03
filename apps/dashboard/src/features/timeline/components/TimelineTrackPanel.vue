<script setup lang="ts">
import { onMounted, useTemplateRef, watch } from 'vue'

import TimelineTrackHeaders from './TimelineTrackHeaders.vue'

import { useTimelineDockContext } from './timelineDockContext'

defineOptions({ inheritAttrs: false })

const context = useTimelineDockContext()
const scrollViewport = useTemplateRef<HTMLDivElement>('scrollViewport')

function onScroll(event: Event): void {
  context.setVerticalScrollTop((event.currentTarget as HTMLDivElement).scrollTop)
}

function syncScrollTop(scrollTop: number): void {
  const viewport = scrollViewport.value
  if (viewport && viewport.scrollTop !== scrollTop)
    viewport.scrollTop = scrollTop
}

onMounted(() => syncScrollTop(context.verticalScrollTop.value))
watch(context.verticalScrollTop, syncScrollTop)
</script>

<template>
  <div
    ref="scrollViewport"
    aria-label="Tracks"
    class="[scrollbar-gutter:stable] h-full min-h-0 overflow-y-auto overscroll-none"
    role="region"
    @scroll.passive="onScroll"
  >
    <TimelineTrackHeaders
      :scroll-container="scrollViewport"
      :tracks="context.episode.value.tracks"
      :editable="context.editable.value"
      @reorder="context.reorderTrack"
    />
  </div>
</template>
