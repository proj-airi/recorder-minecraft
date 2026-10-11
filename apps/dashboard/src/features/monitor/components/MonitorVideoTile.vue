<script setup lang="ts">
import type { TimelineSession } from '../../timeline/composables/useTimelineSession'
import type { PlayPlacement } from '../../timeline/domain'
import type { VideoPosition } from '../videoTime'

import { computed, onBeforeUnmount, shallowRef, useTemplateRef, watch } from 'vue'

import { placementContainsTick, playServerTickAt } from '../../timeline/ticks'
import { assumedVideoPosition, useVideoTiming, videoPositionForServerTick } from '../videoTime'

const props = defineProps<{
  placement: PlayPlacement
  session: TimelineSession
}>()

// NOTICE: A playing video may drift this far outside the window of the image it should show
// before it is seeked back. Smaller values make playback stutter from decoder clock jitter.
const PLAYING_DRIFT_SECONDS = 0.18

const video = useTemplateRef<HTMLVideoElement>('video')
const source = computed(() => props.placement.source)
const sourceUrl = computed(() => source.value.videoUrl ? new URL(source.value.videoUrl, window.location.href).toString() : undefined)
const timing = useVideoTiming(source)
const playheadTick = computed(() => props.session.playheadTick.value)
const active = computed(() => placementContainsTick(props.placement, playheadTick.value))
// Outside the clip, hold the first or last frame the clip shows (trims respected).
const serverTick = computed(() => {
  const tick = Math.min(props.placement.endTick - 1, Math.max(props.placement.startTick, playheadTick.value))
  return playServerTickAt(props.placement, tick)
})
// Null while the timing file loads or after it failed: the video then stays paused rather than
// guessing a time, because a guessed time can be minutes off for a capture with long holds.
const position = computed((): null | VideoPosition => {
  const state = timing.value
  if (state.status === 'unknown')
    return assumedVideoPosition(props.placement, serverTick.value)
  if (state.status !== 'ready')
    return null
  return videoPositionForServerTick(state.anchors, serverTick.value, source.value.videoFramesPerSecond)
})
const targetTime = computed(() => position.value?.time)
const timingFormat = computed(() => timing.value.status === 'unknown' ? 'unknown' : timing.value.timing.format)
const incomplete = computed(() => timing.value.status !== 'unknown' && !timing.value.timing.complete)
const currentTime = shallowRef(0)

function syncVideo(): void {
  const element = video.value
  if (!element)
    return

  const target = position.value
  if (!target) {
    element.pause()
    return
  }

  const time = target.time
  if (!active.value || target.hold) {
    element.pause()
    if (Math.abs(element.currentTime - time) > 0.05)
      element.currentTime = time
    return
  }

  // Keep normal playback smooth: while running, seek only when the video leaves the span that
  // shows the right image, as on a scrub or a jump to the next anchor after a long hold.
  const drifted = element.currentTime < target.windowStart - PLAYING_DRIFT_SECONDS
    || element.currentTime > target.windowEnd + PLAYING_DRIFT_SECONDS
  if (!props.session.isPlaying.value || drifted)
    element.currentTime = time

  if (props.session.isPlaying.value)
    void element.play().catch(() => undefined)
  else
    element.pause()
}

function recordCurrentTime(): void {
  currentTime.value = video.value?.currentTime ?? 0
}

watch(video, syncVideo)
watch(sourceUrl, syncVideo)
watch([position, active, () => props.session.isPlaying.value], syncVideo)
onBeforeUnmount(() => video.value?.pause())

defineExpose({ serverTick, targetTime })
</script>

<template>
  <article
    class="group relative min-h-0 overflow-hidden border border-[var(--dashboard-border-color)] rounded-md bg-black shadow-lg"
    :data-connection-id="placement.connectionId"
    :data-server-tick="serverTick"
    :data-timing-format="timingFormat"
    :data-timing-status="timing.status"
    :data-video-current-time="sourceUrl ? currentTime.toFixed(3) : undefined"
    :data-video-hold="position?.hold"
    :data-video-time="targetTime?.toFixed(3)"
  >
    <video
      v-if="sourceUrl"
      ref="video"
      class="block aspect-video h-full w-full object-contain"
      crossorigin="anonymous"
      muted
      playsinline
      preload="metadata"
      :src="sourceUrl"
      @loadedmetadata="syncVideo"
      @seeked="recordCurrentTime"
      @timeupdate="recordCurrentTime"
    />
    <div
      v-else
      data-testid="monitor-no-video"
      class="aspect-video h-full w-full flex flex-col items-center justify-center gap-2 p-4 text-center text-neutral-500"
    >
      <span aria-hidden="true" class="i-mingcute-video-camera-line text-2xl" />
      <p class="m-0 text-xs text-neutral-300 font-medium">
        No video for this Play
      </p>
      <p class="m-0 max-w-xs text-[11px] leading-4">
        Run <code class="text-neutral-400">recorder-minecraft render</code> on the Play, or record it in an Airicraft playtest, to produce one.
      </p>
    </div>
    <div class="pointer-events-none absolute left-3 right-3 top-3 flex flex-wrap items-center gap-1.5">
      <div class="w-fit flex items-center rounded-lg bg-black/50 px-2 py-2 text-[10px]">
        <strong class="truncate text-white font-medium">{{ source.playerName }}</strong>
        <span class="ml-2 shrink-0 text-neutral-400">{{ source.serverName }}</span>
        <span class="ml-2 shrink-0 text-neutral-500 tabular-nums">tick {{ serverTick }}</span>
      </div>
      <template v-if="sourceUrl">
        <span
          v-if="timing.status === 'unknown'"
          data-badge="timing-unknown"
          class="rounded bg-amber-500/20 px-1.5 py-0.5 text-[10px] text-amber-200"
          title="No timing file describes this video. Video time is assumed to run with Server ticks from the Play start, so alignment is approximate."
        >timing unknown</span>
        <span
          v-if="incomplete"
          data-badge="incomplete"
          class="rounded bg-amber-500/20 px-1.5 py-0.5 text-[10px] text-amber-200"
          title="The producer reported a truncated video or timing file."
        >incomplete</span>
        <span
          v-if="active && position?.hold === 'before'"
          data-badge="waiting"
          class="rounded bg-neutral-500/30 px-1.5 py-0.5 text-[10px] text-neutral-300"
          title="The video has no image for this Server tick yet; its first image is shown."
        >waiting for first frame</span>
        <span
          v-if="active && position?.hold === 'after'"
          data-badge="last-frame"
          class="rounded bg-neutral-500/30 px-1.5 py-0.5 text-[10px] text-neutral-300"
          title="The video has no image after this Server tick; its last image is shown."
        >last frame held</span>
      </template>
    </div>
    <div
      v-if="sourceUrl && timing.status === 'loading'"
      class="pointer-events-none absolute inset-0 flex items-center justify-center gap-2 bg-black/55 text-xs text-neutral-300"
    >
      <span aria-hidden="true" class="i-mingcute-loading-line animate-spin" />
      Loading video timing…
    </div>
    <div
      v-else-if="sourceUrl && timing.status === 'error'"
      class="pointer-events-none absolute inset-0 flex flex-col items-center justify-center gap-1 bg-black/70 p-4 text-center text-xs text-red-300"
      :title="timing.message"
    >
      Video timing failed to load
      <span class="text-[11px] text-neutral-400">{{ timing.message }}</span>
    </div>
    <div v-else-if="sourceUrl && !active" class="pointer-events-none absolute inset-0 flex items-center justify-center bg-black/55 text-xs text-neutral-400">
      {{ playheadTick < placement.startTick ? 'Waiting for clip' : 'Clip ended' }}
    </div>
  </article>
</template>
