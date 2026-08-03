<script setup lang="ts">
import { Pane, Splitpanes } from 'splitpanes'
import { shallowRef } from 'vue'

import TimelineCanvasPanel from './TimelineCanvasPanel.vue'
import TimelineEditingToolbar from './TimelineEditingToolbar.vue'
import TimelineTrackPanel from './TimelineTrackPanel.vue'

import { useEditorWorkspaceContext } from '../../editor/composables/useEditorWorkspaceContext'
import { readDraggedReplayId, REPLAY_DRAG_MIME } from '../../resources/replayDrag'
import { useTimelineDockContext } from './timelineDockContext'

defineOptions({ inheritAttrs: false })

const context = useTimelineDockContext()
const workspace = useEditorWorkspaceContext()
const dragDepth = shallowRef(0)

function acceptsReplay(event: DragEvent): boolean {
  return event.dataTransfer?.types.includes(REPLAY_DRAG_MIME) ?? false
}

function onDragEnter(event: DragEvent): void {
  if (!acceptsReplay(event))
    return
  event.preventDefault()
  dragDepth.value += 1
}

function onDragOver(event: DragEvent): void {
  if (!acceptsReplay(event) || !event.dataTransfer)
    return
  event.preventDefault()
  event.dataTransfer.dropEffect = 'copy'
}

function onDragLeave(event: DragEvent): void {
  if (!acceptsReplay(event))
    return
  dragDepth.value = Math.max(0, dragDepth.value - 1)
}

function onDrop(event: DragEvent): void {
  event.preventDefault()
  dragDepth.value = 0
  if (!event.dataTransfer)
    return

  const connectionId = readDraggedReplayId(event.dataTransfer)
  const replay = workspace.catalog.replays.value.find(candidate => candidate.connectionId === connectionId)
  if (replay)
    workspace.addReplay(replay)
}
</script>

<template>
  <div
    class="relative h-full min-h-0 flex flex-col"
    @dragenter="onDragEnter"
    @dragleave="onDragLeave"
    @dragover="onDragOver"
    @drop="onDrop"
  >
    <TimelineEditingToolbar
      :can-cut="context.canCut.value"
      :can-redo="context.canRedo.value"
      :can-undo="context.canUndo.value"
      :duration-ticks="context.episode.value.durationTicks"
      :editable="context.editable.value"
      :is-playing="context.session.isPlaying.value"
      :playhead-tick="context.session.playheadTick.value"
      @cut="context.cutAtPlayhead"
      @end="context.session.goToEnd"
      @pause="context.session.pause"
      @play="context.session.play"
      @redo="context.redo"
      @start="context.session.goToStart"
      @step-backward="context.session.seekByTicks(-1)"
      @step-forward="context.session.seekByTicks(1)"
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

    <div
      v-if="dragDepth > 0"
      class="pointer-events-none absolute inset-0 z-20 flex items-center justify-center rounded-lg bg-emerald-950/75 shadow-2xl backdrop-blur-sm"
    >
      <div class="flex flex-col items-center gap-2 text-emerald-100">
        <span aria-hidden="true" class="i-mingcute-add-circle-line text-3xl" />
        <strong class="text-sm font-medium">Add replay to track</strong>
        <span class="text-xs text-emerald-200/70">The replay will align to its recording time.</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* NOTICE: Splitpanes supplies pane sizing but intentionally leaves splitter visuals to consumers.
   Keep the interactive hit target wider than its visible rule; the upstream splitter structure is
   at `https://github.com/antoniandre/splitpanes/blob/c13526b5d751ad188e19c6b6797466a7559a88d4/src/components/splitpanes/splitpanes.vue#L752-L774`. */
.timeline-editor-split :deep(> .splitpanes__splitter) {
  background: var(--dashboard-border-color);
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
  background: var(--dashboard-border-color-strong);
  outline: none;
}
</style>
