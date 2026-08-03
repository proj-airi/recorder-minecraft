<script setup lang="ts">
import { Pane, Splitpanes } from 'splitpanes'

import PlaybackControls from '../../editor/components/PlaybackControls.vue'
import TimelineCanvasPanel from './TimelineCanvasPanel.vue'
import TimelineEditingToolbar from './TimelineEditingToolbar.vue'
import TimelineTrackPanel from './TimelineTrackPanel.vue'

import { useTimelineDockContext } from './timelineDockContext'

defineOptions({ inheritAttrs: false })

const context = useTimelineDockContext()
</script>

<template>
  <div class="h-full min-h-0 flex flex-col">
    <PlaybackControls
      :duration-ticks="context.episode.value.durationTicks"
      :is-playing="context.session.isPlaying.value"
      :playhead-tick="context.session.playheadTick.value"
      @end="context.session.goToEnd"
      @pause="context.session.pause"
      @play="context.session.play"
      @start="context.session.goToStart"
      @step-backward="context.session.seekByTicks(-1)"
      @step-forward="context.session.seekByTicks(1)"
    />

    <TimelineEditingToolbar
      :can-cut="context.canCut.value"
      :can-redo="context.canRedo.value"
      :can-undo="context.canUndo.value"
      @cut="context.cutAtPlayhead"
      @redo="context.redo"
      @undo="context.undo"
      @zoom-in="context.zoomBy(1.25)"
      @zoom-out="context.zoomBy(0.8)"
    />

    <Splitpanes class="timeline-editor-split min-h-0 flex-1">
      <Pane :min-size="12" :size="20">
        <TimelineTrackPanel />
      </Pane>
      <Pane :min-size="40" :size="80">
        <TimelineCanvasPanel />
      </Pane>
    </Splitpanes>
  </div>
</template>

<style scoped>
/* NOTICE: Splitpanes supplies pane sizing but intentionally leaves splitter visuals to consumers.
   Keep the interactive hit target wider than its visible rule; the upstream splitter structure is
   at `https://github.com/antoniandre/splitpanes/blob/c13526b5d751ad188e19c6b6797466a7559a88d4/src/components/splitpanes/splitpanes.vue#L752-L774`. */
.timeline-editor-split :deep(> .splitpanes__splitter) {
  background: #3f3f46;
  flex: 0 0 1px;
  position: relative;
  transition: background-color 120ms ease;
  z-index: 4;
}

.timeline-editor-split :deep(> .splitpanes__splitter::before) {
  content: '';
  inset: 0 -4px;
  position: absolute;
}

.timeline-editor-split :deep(> .splitpanes__splitter:hover),
.timeline-editor-split :deep(> .splitpanes__splitter:focus-visible) {
  background: #a3a3a3;
  outline: none;
}
</style>
