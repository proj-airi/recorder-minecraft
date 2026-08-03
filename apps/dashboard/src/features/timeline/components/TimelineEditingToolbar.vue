<script setup lang="ts">
import ControlButton from '../../basic/components/ControlButton.vue'
import PlaybackControls from '../../editor/components/PlaybackControls.vue'

defineProps<{
  canCut: boolean
  canRedo: boolean
  canUndo: boolean
  durationTicks: number
  editable: boolean
  isPlaying: boolean
  playheadTick: number
}>()

const emit = defineEmits<{
  cut: []
  end: []
  pause: []
  play: []
  redo: []
  start: []
  stepBackward: []
  stepForward: []
  undo: []
  zoomIn: []
  zoomOut: []
}>()
</script>

<template>
  <section aria-label="Timeline toolbar" class="min-h-8 flex shrink-0 items-center justify-between border-b border-[var(--dashboard-border-color)] bg-neutral-900/60 px-1">
    <div v-if="editable" class="flex shrink-0 items-center gap-0.5">
      <ControlButton
        compact
        :disabled="!canCut"
        label="Cut selected segment at the playhead"
        title="Cut selected segment at the playhead"
        @click="emit('cut')"
      >
        <span aria-hidden="true" class="i-mingcute-scissors-line text-base" />
      </ControlButton>
      <ControlButton compact :disabled="!canUndo" label="Undo" title="Undo (Ctrl/⌘ Z)" @click="emit('undo')">
        <span aria-hidden="true" class="i-mingcute-back-2-line text-base" />
      </ControlButton>
      <ControlButton compact :disabled="!canRedo" label="Redo" title="Redo (Ctrl/⌘ Shift Z)" @click="emit('redo')">
        <span aria-hidden="true" class="i-mingcute-forward-2-line text-base" />
      </ControlButton>
    </div>
    <div v-else class="w-18 shrink-0" aria-hidden="true" />

    <PlaybackControls
      compact
      :duration-ticks="durationTicks"
      :is-playing="isPlaying"
      :playhead-tick="playheadTick"
      @end="emit('end')"
      @pause="emit('pause')"
      @play="emit('play')"
      @start="emit('start')"
      @step-backward="emit('stepBackward')"
      @step-forward="emit('stepForward')"
    />

    <div class="flex shrink-0 items-center gap-0.5">
      <ControlButton compact label="Zoom out" title="Zoom out" @click="emit('zoomOut')">
        <span aria-hidden="true" class="i-mingcute-zoom-out-line text-base" />
      </ControlButton>
      <ControlButton compact label="Zoom in" title="Zoom in" @click="emit('zoomIn')">
        <span aria-hidden="true" class="i-mingcute-zoom-in-line text-base" />
      </ControlButton>
    </div>
  </section>
</template>
