<script setup lang="ts">
import type { RecorderMinecraftApiV1Replay } from '@proj-airi/recorder-minecraft-api'

import type { ServerTickRange } from '../../timeline/replay'

import { onBeforeUnmount, onMounted, shallowRef, useTemplateRef, watch } from 'vue'

import PlayerAvatar from '../../players/components/PlayerAvatar.vue'

import { formatDurationTicks, parseTick } from '../../basic/format'
import { useVideoTiming, videoPositionForServerTick } from '../../monitor/videoTime'
import { SERVER_TICK_RATE } from '../../timeline/domain'
import { replaySource } from '../../timeline/replay'
import { assumedServerTick, moveRangeEdge, serverTickForVideoTime } from '../playRange'

const props = defineProps<{
  /** Row the preview belongs to; the popover is placed beside it. */
  anchor: HTMLElement
  onTimeline: boolean
  replay: RecorderMinecraftApiV1Replay
}>()

const emit = defineEmits<{
  add: [range: ServerTickRange]
  close: []
  /** Pointer entered (true) or left (false) the popover, so the row hover can hand over to it. */
  hover: [inside: boolean]
}>()

const POPOVER_WIDTH = 380
const play: ServerTickRange = {
  endServerTick: parseTick(props.replay.endServerTick) ?? 0,
  startServerTick: parseTick(props.replay.startServerTick) ?? 0,
}
const range = shallowRef<ServerTickRange>({ ...play })
const playhead = shallowRef(play.startServerTick)
const playing = shallowRef(false)
const position = shallowRef({ left: 0, top: 0 })
const video = useTemplateRef<HTMLVideoElement>('video')
const track = useTemplateRef<HTMLDivElement>('track')
const root = useTemplateRef<HTMLElement>('root')
const timing = useVideoTiming(shallowRef(replaySource(props.replay)))
const playTicks = Math.max(1, play.endServerTick - play.startServerTick)

function place(): void {
  const rect = props.anchor.getBoundingClientRect()
  const height = root.value?.offsetHeight ?? 360
  // Beside the row, outside the resource panel, kept inside the window.
  const left = Math.min(window.innerWidth - POPOVER_WIDTH - 8, rect.right + 8)
  const top = Math.max(8, Math.min(window.innerHeight - height - 8, rect.top - 24))
  position.value = { left, top }
}

function percent(tick: number): string {
  return `${((tick - play.startServerTick) / playTicks) * 100}%`
}

function seek(tick: number): void {
  playhead.value = Math.min(play.endServerTick, Math.max(play.startServerTick, Math.round(tick)))
  const element = video.value
  if (!element)
    return
  const anchors = timing.value.status === 'ready' ? timing.value.anchors : null
  const target = anchors
    ? videoPositionForServerTick(anchors, playhead.value, props.replay.video?.framesPerSecond || undefined)?.time
    : (playhead.value - play.startServerTick) / SERVER_TICK_RATE
  if (target !== undefined && Number.isFinite(target))
    element.currentTime = target
}

function onTimeUpdate(): void {
  const element = video.value
  if (!element || !playing.value)
    return
  const anchors = timing.value.status === 'ready' ? timing.value.anchors : null
  const tick = anchors ? serverTickForVideoTime(anchors, element.currentTime) : assumedServerTick(play.startServerTick, element.currentTime)
  if (tick === null)
    return
  playhead.value = tick
  // Loop inside the selected range so the user sees exactly what will be added.
  if (tick >= range.value.endServerTick)
    seek(range.value.startServerTick)
}

function togglePlay(): void {
  const element = video.value
  if (!element)
    return
  if (playing.value) {
    element.pause()
    playing.value = false
    return
  }
  if (playhead.value < range.value.startServerTick || playhead.value >= range.value.endServerTick)
    seek(range.value.startServerTick)
  playing.value = true
  void element.play().catch(() => playing.value = false)
}

function tickAtPointer(event: PointerEvent): number {
  const rect = track.value!.getBoundingClientRect()
  const fraction = Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width))
  return play.startServerTick + fraction * playTicks
}

let dragging: 'end' | 'playhead' | 'start' | null = null

function onTrackPointerDown(event: PointerEvent, target: 'end' | 'playhead' | 'start'): void {
  if (event.button !== 0)
    return
  event.preventDefault()
  event.stopPropagation()
  dragging = target
  ;(event.currentTarget as HTMLElement).setPointerCapture?.(event.pointerId)
  onTrackPointerMove(event)
}

function onTrackPointerMove(event: PointerEvent): void {
  if (!dragging)
    return
  const tick = tickAtPointer(event)
  if (dragging === 'playhead')
    seek(tick)
  else
    range.value = moveRangeEdge(range.value, dragging, tick, play)
}

function onTrackPointerUp(): void {
  dragging = null
}

function setEdge(edge: 'end' | 'start', tick = playhead.value): void {
  range.value = moveRangeEdge(range.value, edge, edge === 'end' ? tick + 1 : tick, play)
}

function onHandleKey(event: KeyboardEvent, edge: 'end' | 'start'): void {
  // One second per arrow press, ten with Shift: ticks are too fine to step by hand.
  const step = (event.shiftKey ? 10 : 1) * SERVER_TICK_RATE
  const current = edge === 'start' ? range.value.startServerTick : range.value.endServerTick
  const next = event.key === 'ArrowLeft' ? current - step : event.key === 'ArrowRight' ? current + step : event.key === 'Home' ? play.startServerTick : event.key === 'End' ? play.endServerTick : null
  if (next === null)
    return
  event.preventDefault()
  event.stopPropagation()
  range.value = moveRangeEdge(range.value, edge, next, play)
}

function onKeydown(event: KeyboardEvent): void {
  if (event.key === 'Escape') {
    event.stopPropagation()
    emit('close')
    return
  }
  if (event.target instanceof HTMLInputElement)
    return
  if (event.key === 'i' || event.key === 'I')
    setEdge('start')
  else if (event.key === 'o' || event.key === 'O')
    setEdge('end')
  else
    return
  event.preventDefault()
  event.stopPropagation()
}

function relative(tick: number): string {
  return formatDurationTicks(tick - play.startServerTick)
}

/** Moves keyboard focus into the preview, onto the range start handle. */
function focus(): void {
  root.value?.querySelector<HTMLElement>('[role="slider"]')?.focus()
}

defineExpose({ focus })

watch(() => timing.value.status, () => {
  if (!playing.value)
    seek(playhead.value)
})
onMounted(() => {
  place()
  window.addEventListener('resize', place)
})
onBeforeUnmount(() => {
  window.removeEventListener('resize', place)
  video.value?.pause()
})
</script>

<template>
  <Teleport to="body">
    <section
      ref="root"
      :aria-label="`Preview of ${replay.playerName ?? 'Play'}`"
      class="fixed z-60 flex flex-col gap-2 border border-white/10 rounded-lg bg-neutral-900/97 p-2.5 text-xs text-neutral-200 shadow-[0_12px_40px_rgb(0_0_0/0.55)] backdrop-blur"
      role="dialog"
      :style="{ left: `${position.left}px`, top: `${position.top}px`, width: `${POPOVER_WIDTH}px` }"
      @keydown="onKeydown"
      @mouseenter="emit('hover', true)"
      @mouseleave="emit('hover', false)"
    >
      <header class="flex items-center gap-2">
        <PlayerAvatar :player="{ name: replay.playerName, uuid: replay.playerUuid }" :size="20" />
        <span class="min-w-0 flex-1 truncate font-medium">{{ replay.playerName ?? 'Unknown player' }}</span>
        <span class="shrink-0 text-[11px] text-neutral-500 tabular-nums">{{ formatDurationTicks(playTicks) }}</span>
        <button aria-label="Close preview" class="h-6 w-6 flex items-center justify-center border-0 rounded bg-transparent p-0 text-neutral-500 hover:bg-white/8 hover:text-neutral-200" title="Close preview (Esc)" type="button" @click="emit('close')">
          <span aria-hidden="true" class="i-mingcute-close-line" />
        </button>
      </header>

      <div class="relative aspect-video overflow-hidden rounded bg-black">
        <video
          v-if="replay.video?.url"
          ref="video"
          class="h-full w-full object-contain"
          muted
          playsinline
          preload="auto"
          :src="replay.video.url"
          @loadedmetadata="seek(playhead)"
          @pause="playing = false"
          @timeupdate="onTimeUpdate"
        />
        <p v-else class="m-0 h-full flex items-center justify-center p-4 text-center text-[11px] text-neutral-500">
          No rendered video for this Play. The range still selects its capture data.
        </p>
        <button
          v-if="replay.video?.url"
          :aria-label="playing ? 'Pause preview' : 'Play selected range'"
          class="absolute bottom-1.5 left-1.5 h-7 w-7 flex items-center justify-center border-0 rounded-full bg-black/60 p-0 text-white hover:bg-black/80"
          :title="playing ? 'Pause' : 'Play the selected range'"
          type="button"
          @click="togglePlay"
        >
          <span aria-hidden="true" :class="playing ? 'i-mingcute-pause-fill' : 'i-mingcute-play-fill'" />
        </button>
      </div>

      <!-- Range: the whole Play spans the track; the bright band is what "Add range" adds. -->
      <div
        ref="track"
        class="relative h-7 cursor-pointer select-none rounded bg-white/5"
        @pointerdown="onTrackPointerDown($event, 'playhead')"
        @pointermove="onTrackPointerMove"
        @pointerup="onTrackPointerUp"
      >
        <div class="absolute inset-y-0 rounded bg-amber-300/20 ring-1 ring-amber-300/50 ring-inset" :style="{ left: percent(range.startServerTick), right: `calc(100% - ${percent(range.endServerTick)})` }" />
        <div class="pointer-events-none absolute inset-y-0 w-px bg-white" :style="{ left: percent(playhead) }" />
        <button
          v-for="edge in (['start', 'end'] as const)"
          :key="edge"
          :aria-label="edge === 'start' ? 'Range start' : 'Range end'"
          :aria-valuemax="play.endServerTick"
          :aria-valuemin="play.startServerTick"
          :aria-valuenow="edge === 'start' ? range.startServerTick : range.endServerTick"
          :aria-valuetext="relative(edge === 'start' ? range.startServerTick : range.endServerTick)"
          class="absolute inset-y-0 w-2.5 cursor-ew-resize border-0 rounded-sm bg-amber-300 p-0 -translate-x-1/2 focus-visible:outline-2 focus-visible:outline-white"
          role="slider"
          :style="{ left: percent(edge === 'start' ? range.startServerTick : range.endServerTick) }"
          :title="`${edge === 'start' ? 'Range start' : 'Range end'}: drag, or use ← → (Shift for 10 s)`"
          type="button"
          @keydown="onHandleKey($event, edge)"
          @pointerdown="onTrackPointerDown($event, edge)"
        />
      </div>

      <div class="flex items-center gap-1.5 text-[11px] text-neutral-400 tabular-nums">
        <button class="range-button" title="Set range start at the playhead (I)" type="button" @click="setEdge('start')">
          In <kbd>I</kbd>
        </button>
        <span>{{ relative(range.startServerTick) }} – {{ relative(range.endServerTick) }}</span>
        <span class="text-neutral-200">{{ formatDurationTicks(range.endServerTick - range.startServerTick) }}</span>
        <button class="range-button ml-auto" title="Set range end at the playhead (O)" type="button" @click="setEdge('end')">
          Out <kbd>O</kbd>
        </button>
      </div>
      <p v-if="replay.video?.url && timing.status !== 'ready'" class="m-0 text-[10px] text-neutral-500">
        ≈ Video position estimated at 20 ticks/s ({{ timing.status === 'loading' ? 'timing loading' : 'no timing file' }}).
      </p>

      <footer class="flex items-center gap-2">
        <span class="min-w-0 flex-1 truncate text-[10px] text-neutral-500 tabular-nums" :title="`Server ticks ${range.startServerTick}–${range.endServerTick}`">
          ticks {{ range.startServerTick }}–{{ range.endServerTick }}
        </span>
        <span v-if="onTimeline" class="text-[11px] text-emerald-300">Already on the timeline</span>
        <button
          v-else
          class="h-7 inline-flex items-center gap-1 border-0 rounded bg-amber-400/20 px-2 text-[11px] text-amber-100 hover:bg-amber-400/30"
          type="button"
          @click="emit('add', range)"
        >
          <span aria-hidden="true" class="i-mingcute-add-line" />
          {{ range.startServerTick === play.startServerTick && range.endServerTick === play.endServerTick ? 'Add Play to timeline' : 'Add range to timeline' }}
        </button>
      </footer>
    </section>
  </Teleport>
</template>

<style scoped>
.range-button {
  align-items: center;
  background: rgb(255 255 255 / 0.06);
  border: 0;
  border-radius: 0.25rem;
  color: rgb(212 212 212);
  display: inline-flex;
  font-size: 11px;
  gap: 0.25rem;
  height: 1.5rem;
  padding: 0 0.4rem;
}

.range-button:hover {
  background: rgb(255 255 255 / 0.1);
}

.range-button kbd {
  background: rgb(255 255 255 / 0.1);
  border-radius: 3px;
  box-shadow: 0 1px 0 rgb(0 0 0 / 0.5);
  font-family: inherit;
  font-size: 10px;
  padding: 0 4px;
}
</style>
