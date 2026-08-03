<script setup lang="ts">
import Button from '../../basic/components/Button.vue'

defineProps<{
  canCut: boolean
  canRedo: boolean
  canUndo: boolean
}>()

const emit = defineEmits<{
  cut: []
  redo: []
  undo: []
  zoomIn: []
  zoomOut: []
}>()
</script>

<template>
  <section aria-label="Timeline editing tools" class="flex flex-wrap items-center justify-between gap-3 border-b border-white/8 px-4 py-2">
    <div class="flex items-center gap-2">
      <Button
        :disabled="!canCut"
        label="Cut selected segment at the playhead"
        title="Cut selected segment at the playhead"
        @click="emit('cut')"
      >
        <span aria-hidden="true" class="i-mingcute-scissors-line text-base" />
      </Button>
      <Button :disabled="!canUndo" label="Undo" title="Undo (Ctrl/⌘ Z)" @click="emit('undo')">
        <span aria-hidden="true" class="i-mingcute-back-2-line text-base" />
      </Button>
      <Button :disabled="!canRedo" label="Redo" title="Redo (Ctrl/⌘ Shift Z)" @click="emit('redo')">
        <span aria-hidden="true" class="i-mingcute-forward-2-line text-base" />
      </Button>
    </div>

    <div class="flex items-center gap-2">
      <Button label="Zoom out" title="Zoom out" @click="emit('zoomOut')">
        <span aria-hidden="true" class="i-mingcute-zoom-out-line text-base" />
      </Button>
      <Button label="Zoom in" title="Zoom in" @click="emit('zoomIn')">
        <span aria-hidden="true" class="i-mingcute-zoom-in-line text-base" />
      </Button>
    </div>
  </section>
</template>
