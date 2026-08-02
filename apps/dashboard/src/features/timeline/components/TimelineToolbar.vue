<script setup lang="ts">
defineProps<{
  canCut: boolean
  canRedo: boolean
  canUndo: boolean
  isPlaying: boolean
}>()

const emit = defineEmits<{
  cut: []
  end: []
  pause: []
  play: []
  redo: []
  start: []
  undo: []
  zoomIn: []
  zoomOut: []
}>()

const toolButtonClass = 'inline-flex items-center gap-1.5 rounded-md border-none bg-white/5 px-3 py-1.5 text-sm text-[#c7d0dc] transition-colors hover:border-white/20 hover:bg-white/10 hover:text-white disabled:cursor-not-allowed disabled:opacity-38'
const primaryToolButtonClass = 'border-[#70d7bd]/30 bg-[#70d7bd]/12 text-[#9ce6d2]'
</script>

<template>
  <header class="flex flex-wrap items-center justify-between gap-3 border-b border-white/8 px-4 py-3">
    <div class="flex items-center gap-2">
      <button aria-label="Go to start" :class="toolButtonClass" title="Go to start" type="button" @click="emit('start')">
        <span aria-hidden="true" class="i-mingcute-skip-previous-line text-base" />
      </button>
      <button
        aria-label="Play"
        :class="[toolButtonClass, primaryToolButtonClass]"
        :disabled="isPlaying"
        title="Play"
        type="button"
        @click="emit('play')"
      >
        <span aria-hidden="true" class="i-mingcute-play-fill text-base" />
      </button>
      <button
        aria-label="Pause"
        :class="toolButtonClass"
        :disabled="!isPlaying"
        title="Pause"
        type="button"
        @click="emit('pause')"
      >
        <span aria-hidden="true" class="i-mingcute-pause-fill text-base" />
      </button>
      <button aria-label="Go to end" :class="toolButtonClass" title="Go to end" type="button" @click="emit('end')">
        <span aria-hidden="true" class="i-mingcute-skip-forward-line text-base" />
      </button>
      <span class="h-6 w-px bg-white/8" />
      <button
        aria-label="Cut selected segment at the playhead"
        :class="toolButtonClass"
        :disabled="!canCut"
        title="Cut selected segment at the playhead"
        type="button"
        @click="emit('cut')"
      >
        <span aria-hidden="true" class="i-mingcute-scissors-line text-base" />
      </button>
      <button aria-label="Undo" :class="toolButtonClass" :disabled="!canUndo" title="Undo (Ctrl/⌘ Z)" type="button" @click="emit('undo')">
        <span aria-hidden="true" class="i-mingcute-back-2-line text-base" />
      </button>
      <button aria-label="Redo" :class="toolButtonClass" :disabled="!canRedo" title="Redo (Ctrl/⌘ Shift Z)" type="button" @click="emit('redo')">
        <span aria-hidden="true" class="i-mingcute-forward-2-line text-base" />
      </button>
    </div>

    <div class="flex items-center gap-2">
      <button aria-label="Zoom out" :class="toolButtonClass" type="button" @click="emit('zoomOut')">
        <span aria-hidden="true" class="i-mingcute-zoom-out-line text-base" />
      </button>
      <button aria-label="Zoom in" :class="toolButtonClass" type="button" @click="emit('zoomIn')">
        <span aria-hidden="true" class="i-mingcute-zoom-in-line text-base" />
      </button>
    </div>
  </header>
</template>
