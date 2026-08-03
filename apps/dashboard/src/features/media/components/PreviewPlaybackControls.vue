<script setup lang="ts">
import type { ReplayPlayback } from '../composables/useReplayPlayback'

import PlaybackControls from '../../editor/components/PlaybackControls.vue'

defineProps<{
  disabled: boolean
  playback: ReplayPlayback
}>()
</script>

<template>
  <div
    class="shrink-0 border-t border-[var(--dashboard-border-color)] bg-neutral-900/90"
    :class="disabled ? 'pointer-events-none opacity-45' : ''"
  >
    <PlaybackControls
      compact
      :duration-ticks="playback.durationTicks.value"
      :is-playing="playback.isPlaying.value"
      label="Preview playback controls"
      :playhead-tick="playback.playheadTick.value"
      @end="playback.goToEnd"
      @pause="playback.pause"
      @play="playback.play"
      @start="playback.goToStart"
      @step-backward="playback.seekByTicks(-1)"
      @step-forward="playback.seekByTicks(1)"
    />
  </div>
</template>
