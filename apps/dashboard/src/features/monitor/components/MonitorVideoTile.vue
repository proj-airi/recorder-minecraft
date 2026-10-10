<script setup lang="ts">
import type { TimelineSession } from '../../timeline/composables/useTimelineSession'
import type { PlayPlacement } from '../../timeline/domain'

import { computed, onBeforeUnmount, toRef, useTemplateRef, watch } from 'vue'

import { placementContainsTick, playServerTickAt } from '../../timeline/ticks'
import { useFramesIndex, videoTimeForServerTick } from '../videoTime'

const props = defineProps<{
  placement: PlayPlacement
  session: TimelineSession
}>()

const video = useTemplateRef<HTMLVideoElement>('video')
const source = computed(() => props.placement.source)
const sourceUrl = computed(() => source.value.videoUrl ? new URL(source.value.videoUrl, window.location.href).toString() : undefined)
const framesIndex = useFramesIndex(toRef(() => source.value.framesIndexUrl))
const playheadTick = computed(() => props.session.playheadTick.value)
const active = computed(() => placementContainsTick(props.placement, playheadTick.value))
// Outside the clip, hold the first or last frame the clip shows (trims respected).
const serverTick = computed(() => {
  const tick = Math.min(props.placement.endTick - 1, Math.max(props.placement.startTick, playheadTick.value))
  return playServerTickAt(props.placement, tick)
})
const targetTime = computed(() => videoTimeForServerTick(props.placement, serverTick.value, framesIndex.value, source.value.videoFramesPerSecond))

function syncVideo(): void {
  const element = video.value
  if (!element)
    return

  const time = targetTime.value
  if (!active.value) {
    element.pause()
    if (Math.abs(element.currentTime - time) > 0.05)
      element.currentTime = time
    return
  }

  // Keep normal video playback smooth while running; only correct meaningful clock drift.
  if (!props.session.isPlaying.value || Math.abs(element.currentTime - time) > 0.18)
    element.currentTime = time

  if (props.session.isPlaying.value)
    void element.play().catch(() => undefined)
  else
    element.pause()
}

watch(video, syncVideo)
watch(sourceUrl, syncVideo)
watch([targetTime, active, () => props.session.isPlaying.value], syncVideo)
onBeforeUnmount(() => video.value?.pause())

defineExpose({ serverTick, targetTime })
</script>

<template>
  <article
    class="group relative min-h-0 overflow-hidden border border-[var(--dashboard-border-color)] rounded-md bg-black shadow-lg"
    :data-connection-id="placement.connectionId"
    :data-server-tick="serverTick"
    :data-video-time="targetTime.toFixed(3)"
  >
    <video
      ref="video"
      class="block aspect-video h-full w-full object-contain"
      crossorigin="anonymous"
      muted
      playsinline
      preload="metadata"
      :src="sourceUrl"
      @loadedmetadata="syncVideo"
    />
    <div class="pointer-events-none absolute left-3 right-0 top-3 w-fit flex items-center rounded-lg bg-black/50 px-2 pb-2 pt-2 text-[10px]">
      <strong class="truncate text-white font-medium">{{ source.playerName }}</strong>
      <span class="ml-2 shrink-0 text-neutral-400">{{ source.serverName }}</span>
      <span class="ml-2 shrink-0 text-neutral-500 tabular-nums">tick {{ serverTick }}</span>
    </div>
    <div v-if="!active" class="pointer-events-none absolute inset-0 flex items-center justify-center bg-black/55 text-xs text-neutral-400">
      {{ playheadTick < placement.startTick ? 'Waiting for clip' : 'Clip ended' }}
    </div>
  </article>
</template>
