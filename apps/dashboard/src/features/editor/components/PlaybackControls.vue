<script setup lang="ts">
import { computed } from 'vue'

import ControlButton from '../../basic/components/ControlButton.vue'

import { SERVER_TICK_RATE } from '../../timeline/domain'

const props = defineProps<{
  durationTicks: number
  isPlaying: boolean
  playheadTick: number
}>()

const emit = defineEmits<{
  end: []
  pause: []
  play: []
  start: []
  stepBackward: []
  stepForward: []
}>()

const currentTimecode = computed(() => formatTimecode(props.playheadTick))
const durationTimecode = computed(() => formatTimecode(props.durationTicks))

function formatTimecode(tick: number): string {
  const boundedTick = Math.max(0, Math.round(tick))
  const frames = boundedTick % SERVER_TICK_RATE
  const totalSeconds = Math.floor(boundedTick / SERVER_TICK_RATE)
  const seconds = totalSeconds % 60
  const minutes = Math.floor(totalSeconds / 60) % 60
  const hours = Math.floor(totalSeconds / 3_600)
  return [hours, minutes, seconds, frames].map(value => String(value).padStart(2, '0')).join(':')
}
</script>

<template>
  <section aria-label="Timeline playback controls" class="relative min-h-12 flex shrink-0 items-center justify-center border-b border-white/8 px-4 py-1.5">
    <div class="flex items-center justify-center gap-1">
      <ControlButton label="Go to start" title="Go to start" @click="emit('start')">
        <span aria-hidden="true" class="i-mingcute-skip-previous-line text-base" />
      </ControlButton>
      <ControlButton label="Step backward" title="Step backward (←; hold to rewind, Option/Alt to accelerate)" @click="emit('stepBackward')">
        <span aria-hidden="true" class="i-mingcute-left-line text-base" />
      </ControlButton>
      <ControlButton label="Play" :disabled="isPlaying" title="Play (Space)" @click="emit('play')">
        <span aria-hidden="true" class="i-mingcute-play-fill text-base" />
      </ControlButton>
      <ControlButton label="Pause" :disabled="!isPlaying" title="Pause (Space)" @click="emit('pause')">
        <span aria-hidden="true" class="i-mingcute-pause-fill text-base" />
      </ControlButton>
      <output aria-label="Current timecode" class="[font-variant-numeric:tabular-nums] mx-2 min-w-47 text-center text-xs text-neutral-400 font-mono">
        {{ currentTimecode }} / {{ durationTimecode }}
      </output>
      <ControlButton label="Step forward" title="Step forward (→; hold to fast-forward, Option/Alt to accelerate)" @click="emit('stepForward')">
        <span aria-hidden="true" class="i-mingcute-right-line text-base" />
      </ControlButton>
      <ControlButton label="Go to end" title="Go to end" @click="emit('end')">
        <span aria-hidden="true" class="i-mingcute-skip-forward-line text-base" />
      </ControlButton>
    </div>
  </section>
</template>
