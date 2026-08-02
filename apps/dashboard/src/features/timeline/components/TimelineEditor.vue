<script setup lang="ts">
import { storeToRefs } from 'pinia'
import { Pane, Splitpanes } from 'splitpanes'
import { computed, onBeforeUnmount, onMounted, shallowRef, useTemplateRef } from 'vue'

import TimelineCanvas from './TimelineCanvas.vue'
import TimelineToolbar from './TimelineToolbar.vue'
import TimelineTrackHeaders from './TimelineTrackHeaders.vue'

import { useTimelineSession } from '../composables/useTimelineSession'
import { useEpisodeStore } from '../stores/episode'

const episodeStore = useEpisodeStore()
const { episode } = storeToRefs(episodeStore)
const session = useTimelineSession(episode, episodeStore.commitSegmentEdit)
const timelineViewport = useTemplateRef<HTMLDivElement>('timelineViewport')
// NOTICE: Vertical scrolling stays in this shared DOM viewport so both canvases remain bounded to
// the visible editor height. The upstream renderer already projects tracks with `state.scrollTop`;
// see `https://github.com/techsquidtv/canvas-timeline/blob/1536a2dbc54e3a333ace360894a2e4508b295cf1/packages/renderer/src/render/tracks.ts#L39-L73`.
const verticalScrollTop = shallowRef(session.engine.value.scrollTop)
const canCut = computed(() => {
  const selectedId = session.selectedSegmentId.value
  const selected = episode.value.segments.find(segment => segment.id === selectedId)
  return selected !== undefined
    && session.playheadTick.value > selected.startTick
    && session.playheadTick.value < selected.endTick
})

function cutAtPlayhead(): void {
  const selectedId = session.selectedSegmentId.value
  if (selectedId)
    episodeStore.cutSegment(selectedId, session.playheadTick.value)
}

function isEditingText(target: EventTarget | null): boolean {
  return target instanceof HTMLElement
    && (target.isContentEditable || target.matches('input, textarea, select'))
}

function onKeyDown(event: KeyboardEvent): void {
  const hasUndoModifier = event.ctrlKey || event.metaKey
  if (!hasUndoModifier || event.key.toLowerCase() !== 'z' || isEditingText(event.target))
    return

  event.preventDefault()
  if (event.shiftKey)
    episodeStore.redo()
  else
    episodeStore.undo()
}

function onTimelineScroll(event: Event): void {
  verticalScrollTop.value = (event.currentTarget as HTMLDivElement).scrollTop
}

onMounted(() => window.addEventListener('keydown', onKeyDown))
onBeforeUnmount(() => window.removeEventListener('keydown', onKeyDown))
</script>

<template>
  <div class="grid grid-rows-[auto_minmax(0,1fr)] h-full min-h-0">
    <TimelineToolbar
      :can-cut="canCut"
      :can-redo="episodeStore.canRedo"
      :can-undo="episodeStore.canUndo"
      :is-playing="session.isPlaying.value"
      @cut="cutAtPlayhead"
      @end="session.goToEnd"
      @pause="session.pause"
      @play="session.play"
      @redo="episodeStore.redo"
      @start="session.goToStart"
      @undo="episodeStore.undo"
      @zoom-in="session.zoomBy(1.25)"
      @zoom-out="session.zoomBy(0.8)"
    />

    <div
      ref="timelineViewport"
      class="[container-type:size] [scrollbar-gutter:stable] min-h-0 overflow-y-auto overscroll-none"
      @scroll.passive="onTimelineScroll"
    >
      <Splitpanes
        class="timeline-scroll-content h-auto min-h-full overflow-visible"
        :keyboard-step="2"
        :maximize-panes="false"
      >
        <Pane :max-size="30" :min-size="8" :size="12">
          <TimelineTrackHeaders
            :scroll-container="timelineViewport"
            :tracks="episode.tracks"
            @reorder="episodeStore.reorderTrack"
          />
        </Pane>

        <Pane :min-size="50" :size="88">
          <!-- NOTICE: The Splitpanes row grows to the full virtualized track height, so `h-full`
               would size the sticky canvas against all tracks. `h-[100cqh]` reads the shared
               scroll viewport instead and remains CSS-driven when its parent resizes. -->
          <div class="sticky top-0 h-[100cqh] min-w-0 self-start">
            <TimelineCanvas
              :engine="session.engine.value"
              :render-revision="session.renderRevision.value"
              :scroll-top="verticalScrollTop"
              :selected-segment-id="session.selectedSegmentId.value"
              @cancel-edit="session.rebuild"
              @commit-edit="session.commitEdit"
              @select-segment="session.selectSegment"
            />
          </div>
        </Pane>
      </Splitpanes>
    </div>
  </div>
</template>

<style scoped>
/* NOTICE: Splitpanes normally gives every pane `height: 100%` and `overflow: hidden`. This
   timeline instead needs its virtualized Track pane to establish the outer scroll height, and the
   Canvas pane must not become the sticky containing block. The upstream defaults are at
   `https://github.com/antoniandre/splitpanes/blob/c13526b5d751ad188e19c6b6797466a7559a88d4/src/components/splitpanes/splitpanes.vue#L752-L774`. */
.timeline-scroll-content :deep(.splitpanes__pane) {
  height: auto;
  min-width: 0;
  overflow: visible;
}

.timeline-scroll-content :deep(> .splitpanes__splitter) {
  background: #3f3f46;
  flex: 0 0 1px;
  position: relative;
  transition: background-color 120ms ease;
  z-index: 4;
}

.timeline-scroll-content :deep(> .splitpanes__splitter::before) {
  content: '';
  inset: 0 -4px;
  position: absolute;
}

.timeline-scroll-content :deep(> .splitpanes__splitter:hover),
.timeline-scroll-content :deep(> .splitpanes__splitter:focus-visible) {
  background: #a3a3a3;
  outline: none;
}
</style>
