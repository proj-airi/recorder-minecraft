<script setup lang="ts">
import { computed, onBeforeUnmount, useTemplateRef, watch } from 'vue'

import type { RenderDescriptor, RenderTimelineFrame, TickRange } from '../types/viewer'
import { clamp } from '../utils/viewer'
import { startVideoFrameLoop, supportsVideoFrameCallbacks } from '../utils/videoFrames'

const props = defineProps<{
  currentFrame: number | null
  currentTick: number
  loading: boolean
  mediaUrl: string
  render: RenderDescriptor | null
  tickRange: TickRange
  timelineFrame: RenderTimelineFrame | null
}>()

const emit = defineEmits<{
  frameChange: [frame: number]
  tickChange: [tick: number]
}>()

const video = useTemplateRef<HTMLVideoElement>('video')
let stopVideoFrames: (() => void) | null = null

const tickPercent = computed(() => {
  const span = Math.max(1, props.tickRange.end - props.tickRange.start)
  return (props.currentTick - props.tickRange.start) / span * 100
})

watch(
  () => props.currentFrame,
  (frame) => {
    const media = video.value
    if (frame === null || !props.render || !media) {
      return
    }
    if (!media.paused && !media.ended) {
      return
    }
    const exactTime = frame / props.render.fps
    if (Math.abs(media.currentTime - exactTime) > 0.075) {
      media.currentTime = exactTime
    }
  },
)

watch(() => props.mediaUrl, stopFrameLoop)
onBeforeUnmount(stopFrameLoop)

function onTickInput(event: Event): void {
  const input = event.currentTarget as HTMLInputElement
  const tick = Number.parseInt(input.value, 10)
  if (props.render && video.value) {
    video.value.currentTime = clamp(
      tick - props.render.start_tick,
      0,
      Math.max(0, props.render.frame_count - 1),
    ) / props.render.fps
  }
  emit('tickChange', tick)
}

function onVideoTimeUpdate(event: Event): void {
  const media = event.currentTarget as HTMLVideoElement
  if (!supportsVideoFrameCallbacks(media)) {
    emitVideoFrame(media.currentTime)
  }
}

function onVideoLoaded(event: Event): void {
  const media = event.currentTarget as HTMLVideoElement
  seekToSelectedFrame(media)
  if (!media.paused) {
    startFrameLoop(media)
  }
}

function onVideoPlay(event: Event): void {
  startFrameLoop(event.currentTarget as HTMLVideoElement)
}

function onVideoPause(event: Event): void {
  stopFrameLoop()
  emitVideoFrame((event.currentTarget as HTMLVideoElement).currentTime)
}

function onVideoSeeked(event: Event): void {
  emitVideoFrame((event.currentTarget as HTMLVideoElement).currentTime)
}

function startFrameLoop(media: HTMLVideoElement): void {
  stopFrameLoop()
  if (supportsVideoFrameCallbacks(media)) {
    stopVideoFrames = startVideoFrameLoop(media, emitVideoFrame)
  }
}

function stopFrameLoop(): void {
  stopVideoFrames?.()
  stopVideoFrames = null
}

function seekToSelectedFrame(media: HTMLVideoElement): void {
  if (props.currentFrame !== null && props.render) {
    media.currentTime = props.currentFrame / props.render.fps
  }
}

function emitVideoFrame(mediaTime: number): void {
  if (!props.render) {
    return
  }
  const frame = Math.round(clamp(
    Math.floor(mediaTime * props.render.fps + 1e-6),
    0,
    Math.max(0, props.render.frame_count - 1),
  ))
  if (frame !== props.currentFrame) {
    emit('frameChange', frame)
  }
}
</script>

<template>
  <section class="panel playback-panel" aria-labelledby="playback-title">
    <div class="panel-heading">
      <div>
        <p class="eyebrow">
          Timeline
        </p>
        <h2 id="playback-title">
          Playback & tick
        </h2>
      </div>
      <output class="tick-readout" :class="{ loading }">
        tick {{ currentTick.toLocaleString() }}
      </output>
    </div>

    <div v-if="render" class="video-shell">
      <video
        :key="mediaUrl"
        ref="video"
        class="video"
        :src="mediaUrl"
        controls
        playsinline
        preload="metadata"
        @loadeddata="onVideoLoaded"
        @pause="onVideoPause"
        @play="onVideoPlay"
        @seeked="onVideoSeeked"
        @timeupdate="onVideoTimeUpdate"
      >
        This browser cannot play the attached H.264 video.
      </video>
      <div class="media-proof">
        <span>H.264 · yuv420p · {{ render.fps }} FPS</span>
        <span>{{ render.frame_count.toLocaleString() }} frames</span>
        <span>{{ render.timeline_complete ? 'exact timeline' : 'timeline incomplete' }}</span>
      </div>
      <div v-if="timelineFrame" class="timeline-proof">
        <span>frame {{ timelineFrame.frame }}</span>
        <span>PTS {{ timelineFrame.pts }}</span>
        <span>server {{ timelineFrame.server_tick }}</span>
        <span>replay {{ timelineFrame.replay_tick }}</span>
        <span>scene {{ timelineFrame.scene_frame }}</span>
      </div>
    </div>
    <div v-else class="render-absent">
      <div class="render-icon" aria-hidden="true">
        ◫
      </div>
      <div>
        <strong>No FPV render attached</strong>
        <p>The state, reconstructed actions, trajectory, scene slice, and replay provenance remain fully usable.</p>
      </div>
    </div>

    <label class="tick-slider">
      <span>
        <small>{{ tickRange.start.toLocaleString() }}</small>
        <strong>Server timeline</strong>
        <small>{{ tickRange.end.toLocaleString() }}</small>
      </span>
      <input
        type="range"
        :min="tickRange.start"
        :max="tickRange.end"
        :value="currentTick"
        :style="{ '--tick-progress': `${tickPercent}%` }"
        @input="onTickInput"
      >
    </label>
  </section>
</template>

<style scoped>
.playback-panel { padding: 1rem; }
.panel-heading { display: flex; justify-content: space-between; align-items: start; gap: 1rem; }
.tick-readout { border: 1px solid #34503e; border-radius: 99px; padding: .3rem .6rem; color: var(--accent); background: #0b1510; font: 700 .72rem/1 var(--mono); }
.tick-readout.loading { opacity: .5; }
.video-shell { margin-top: .9rem; overflow: hidden; border: 1px solid var(--line); border-radius: .7rem; background: #030604; }
.video { display: block; width: 100%; max-height: 24rem; background: #030604; }
.media-proof { display: flex; flex-wrap: wrap; justify-content: space-between; gap: .4rem .8rem; padding: .55rem .7rem; color: var(--muted); font: 620 .65rem/1.3 var(--mono); }
.timeline-proof { display: flex; flex-wrap: wrap; gap: .35rem .7rem; padding: .5rem .7rem; border-top: 1px solid var(--line); color: #afc4b5; background: #080d0a; font: 620 .61rem/1.2 var(--mono); }
.render-absent { min-height: 11rem; display: grid; grid-template-columns: auto 1fr; align-items: center; gap: 1rem; margin-top: .9rem; padding: 1rem; border: 1px dashed #344239; border-radius: .7rem; background: #0a0f0c; }
.render-absent p { max-width: 36rem; margin: .35rem 0 0; color: var(--muted); font-size: .78rem; line-height: 1.5; }
.render-icon { width: 3rem; height: 3rem; display: grid; place-items: center; border: 1px solid #334139; border-radius: .6rem; color: var(--muted); font-size: 1.5rem; }
.tick-slider { display: grid; gap: .55rem; margin-top: .9rem; }
.tick-slider span { display: grid; grid-template-columns: 1fr auto 1fr; align-items: center; gap: .5rem; }
.tick-slider small { color: var(--muted); font: 620 .66rem/1 var(--mono); }
.tick-slider small:last-child { text-align: right; }
.tick-slider strong { color: #bdccc1; font-size: .7rem; letter-spacing: .06em; text-transform: uppercase; }
.tick-slider input { width: 100%; accent-color: var(--accent); }
</style>
