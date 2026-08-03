<script setup lang="ts">
import { computed } from 'vue'

import ControlButton from '../../basic/components/ControlButton.vue'

import { SERVER_TICK_RATE } from '../../timeline/domain'

const props = withDefaults(defineProps<{
  compact?: boolean
  durationTicks: number
  isPlaying: boolean
  label?: string
  playheadTick: number
}>(), {
  compact: false,
  label: 'Timeline playback controls',
})

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

function togglePlayback(): void {
  if (props.isPlaying)
    emit('pause')
  else
    emit('play')
}
</script>

<template>
  <section
    :aria-label="label"
    class="relative flex shrink-0 items-center justify-center"
    :class="compact ? 'px-1' : 'min-h-12 px-4 py-1.5'"
  >
    <div class="flex items-center justify-center" :class="compact ? 'gap-0.5' : 'gap-1'">
      <ControlButton :compact="compact" label="Go to start" title="Go to start" @click="emit('start')">
        <span aria-hidden="true" class="i-mingcute-skip-previous-line text-base" />
      </ControlButton>
      <ControlButton :compact="compact" label="Step backward" title="Step backward (←; hold to rewind, Option/Alt to accelerate)" @click="emit('stepBackward')">
        <span aria-hidden="true" class="i-mingcute-left-line text-base" />
      </ControlButton>
      <ControlButton
        v-if="compact"
        compact
        :label="isPlaying ? 'Pause' : 'Play'"
        :title="isPlaying ? 'Pause (Space)' : 'Play (Space)'"
        @click="togglePlayback"
      >
        <span aria-hidden="true" :class="isPlaying ? 'i-mingcute-pause-fill' : 'i-mingcute-play-fill'" class="text-base" />
      </ControlButton>
      <template v-else>
        <ControlButton label="Play" :disabled="isPlaying" title="Play (Space)" @click="emit('play')">
          <span aria-hidden="true" class="i-mingcute-play-fill text-base" />
        </ControlButton>
        <ControlButton label="Pause" :disabled="!isPlaying" title="Pause (Space)" @click="emit('pause')">
          <span aria-hidden="true" class="i-mingcute-pause-fill text-base" />
        </ControlButton>
      </template>
      <output
        aria-label="Current timecode"
        class="[font-variant-numeric:tabular-nums] text-center text-neutral-400 font-mono"
        :class="compact ? 'mx-1 min-w-38 text-[10px]' : 'mx-2 min-w-47 text-xs'"
      >
        {{ currentTimecode }} / {{ durationTimecode }}
      </output>
      <ControlButton :compact="compact" label="Step forward" title="Step forward (→; hold to fast-forward, Option/Alt to accelerate)" @click="emit('stepForward')">
        <span aria-hidden="true" class="i-mingcute-right-line text-base" />
      </ControlButton>
      <ControlButton :compact="compact" label="Go to end" title="Go to end" @click="emit('end')">
        <span aria-hidden="true" class="i-mingcute-skip-forward-line text-base" />
      </ControlButton>
    </div>
  </section>
</template>
