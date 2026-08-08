<script setup lang="ts">
import type { TimelineSession } from '../../timeline/composables/useTimelineSession'
import type { EpisodeReplaySource, EpisodeSegment } from '../../timeline/domain'

import { computed, onBeforeUnmount, useTemplateRef, watch } from 'vue'

import { SERVER_TICK_RATE } from '../../timeline/domain'

const props = defineProps<{
  segment: EpisodeSegment
  session: TimelineSession
  source: EpisodeReplaySource
}>()

const video = useTemplateRef<HTMLVideoElement>('video')
const sourceUrl = computed(() => props.source.videoUrl ? new URL(props.source.videoUrl, window.location.href).toString() : undefined)
const relativeTick = computed(() => props.session.playheadTick.value - props.segment.startTick)
const active = computed(() => relativeTick.value >= 0 && props.session.playheadTick.value < props.segment.endTick)

function syncVideo(): void {
  const element = video.value
  if (!element)
    return

  const targetTime = Math.max(0, relativeTick.value / SERVER_TICK_RATE)
  if (!active.value) {
    element.pause()
    if (Math.abs(element.currentTime - targetTime) > 0.05)
      element.currentTime = targetTime
    return
  }

  // Keep normal video playback smooth while running; only correct meaningful clock drift.
  if (!props.session.isPlaying.value || Math.abs(element.currentTime - targetTime) > 0.18)
    element.currentTime = targetTime

  if (props.session.isPlaying.value)
    void element.play().catch(() => undefined)
  else
    element.pause()
}

watch(video, syncVideo)
watch(sourceUrl, syncVideo)
watch([relativeTick, active, () => props.session.isPlaying.value], syncVideo)
onBeforeUnmount(() => video.value?.pause())
</script>

<template>
  <article class="group relative min-h-0 overflow-hidden border border-[var(--dashboard-border-color)] rounded-md bg-black shadow-lg">
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
    </div>
    <div v-if="!active" class="pointer-events-none absolute inset-0 flex items-center justify-center bg-black/55 text-xs text-neutral-400">
      {{ relativeTick < 0 ? 'Waiting for clip' : 'Clip ended' }}
    </div>
  </article>
</template>
