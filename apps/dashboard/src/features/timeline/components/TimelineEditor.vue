<script setup lang="ts">
import type { TimelineSession } from '../composables/useTimelineSession'
import type { EpisodeDraft } from '../domain'

import { computed, provide, readonly, shallowRef, toRef } from 'vue'

import TimelineEditorPanel from './TimelineEditorPanel.vue'

import { timelineDockContextKey } from './timelineDockContext'

const props = defineProps<{
  canRedo: boolean
  canUndo: boolean
  episode: EpisodeDraft
  session: TimelineSession
}>()

const emit = defineEmits<{
  close: []
  cutSegment: [segmentId: string, atTick: number]
  redo: []
  reorderTrack: [sourceIndex: number, targetIndex: number]
  undo: []
}>()

const episode = toRef(props, 'episode')
const verticalScrollTop = shallowRef(props.session.engine.value.scrollTop)
const canCut = computed(() => {
  const selectedId = props.session.selectedSegmentId.value
  const selected = props.episode.segments.find(segment => segment.id === selectedId)
  return selected !== undefined
    && props.session.playheadTick.value > selected.startTick
    && props.session.playheadTick.value < selected.endTick
})

function cutAtPlayhead(): void {
  const selectedId = props.session.selectedSegmentId.value
  if (selectedId)
    emit('cutSegment', selectedId, props.session.playheadTick.value)
}

function setVerticalScrollTop(scrollTop: number): void {
  verticalScrollTop.value = Math.max(0, scrollTop)
}

// NOTICE: The track list owns vertical scrolling while the Canvas renderer consumes the projected
// offset through `state.scrollTop`; the upstream renderer applies it at
// `https://github.com/techsquidtv/canvas-timeline/blob/1536a2dbc54e3a333ace360894a2e4508b295cf1/packages/renderer/src/render/tracks.ts#L39-L73`.
provide(timelineDockContextKey, {
  canCut,
  canRedo: readonly(toRef(props, 'canRedo')),
  canUndo: readonly(toRef(props, 'canUndo')),
  cutAtPlayhead,
  episode,
  redo: () => emit('redo'),
  reorderTrack: (sourceIndex, targetIndex) => emit('reorderTrack', sourceIndex, targetIndex),
  session: props.session,
  setVerticalScrollTop,
  undo: () => emit('undo'),
  verticalScrollTop: readonly(verticalScrollTop),
  zoomBy: props.session.zoomBy,
})
</script>

<template>
  <TimelineEditorPanel class="h-full min-h-0" />
</template>
