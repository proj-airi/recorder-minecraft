<script setup lang="ts">
import { computed, onMounted, useTemplateRef, watch } from 'vue'

import TimelineTrackHeaders from './TimelineTrackHeaders.vue'

import { playerLaneColors } from '../../players/colors'
import { episodeAlignment } from '../replay'
import { useTimelineDockContext } from './timelineDockContext'

defineOptions({ inheritAttrs: false })

const context = useTimelineDockContext()
const scrollViewport = useTemplateRef<HTMLDivElement>('scrollViewport')
const alignment = computed(() => episodeAlignment(context.episode.value))
// Shared with the Inputs cards, so one player has one color in every panel.
const trackColors = computed(() => playerLaneColors(context.episode.value))

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
      :alignment-label="alignment.label"
      :editable="context.editable.value"
      :layout="context.session.layout.value"
      :scroll-container="scrollViewport"
      :scroll-top="context.verticalScrollTop.value"
      :selected-track-id="context.session.selectedTrackId.value"
      :sessions="context.episode.value.sessions"
      :track-colors="trackColors"
      @reload="context.session.reloadDataTrack"
      @reorder="context.reorderTrack"
      @select="context.session.selectTrack"
      @toggle-empty="context.session.toggleEmptyDataTracks"
      @toggle-group="context.session.toggleGroup"
    />
  </div>
</template>
