<script setup lang="ts">
import type { RecorderMinecraftApiV1Replay } from '@proj-airi/recorder-minecraft-api'

import type { ServerTickRange } from '../../timeline/replay'

import { PopoverAnchor, PopoverContent, PopoverPortal, PopoverRoot, SliderRange, SliderRoot, SliderThumb, SliderTrack } from 'reka-ui'
import { computed, onBeforeUnmount, shallowRef, useTemplateRef, watch } from 'vue'

import ControlButton from '../../basic/components/ControlButton.vue'
import Kbd from '../../basic/components/Kbd.vue'
import PlayerAvatar from '../../players/components/PlayerAvatar.vue'

import { formatDurationTicks, parseTick } from '../../basic/format'
import { useVideoTiming, videoPositionForServerTick } from '../../monitor/videoTime'
import { SERVER_TICK_RATE } from '../../timeline/domain'
import { replaySource } from '../../timeline/replay'
import { assumedServerTick, moveRangeEdge, rangeFromSteps, rangeToSteps, serverTickForVideoTime } from '../playRange'

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

const play: ServerTickRange = {
  endServerTick: parseTick(props.replay.endServerTick) ?? 0,
  startServerTick: parseTick(props.replay.startServerTick) ?? 0,
}
const range = shallowRef<ServerTickRange>({ ...play })
const playhead = shallowRef(play.startServerTick)
const playing = shallowRef(false)
const video = useTemplateRef<HTMLVideoElement>('video')
const root = useTemplateRef<HTMLElement>('root')
const timing = useVideoTiming(shallowRef(replaySource(props.replay)))
const playTicks = Math.max(1, play.endServerTick - play.startServerTick)

// The range slider moves in one-second steps (Shift: ten, the slider's built-in skip), the same
// granularity the hand-written handles had; ticks are too fine to step through by hand. I and O
// still set either edge to the exact playhead tick, which the step mapping preserves.
const rangeSteps = computed({
  get: () => rangeToSteps(range.value, play, SERVER_TICK_RATE),
  set: (steps: number[] | undefined) => {
    if (steps?.length === 2)
      range.value = rangeFromSteps(range.value, [steps[0]!, steps[1]!], play, SERVER_TICK_RATE)
  },
})
const stepCount = Math.ceil(playTicks / SERVER_TICK_RATE)
const playheadValue = computed({
  get: () => [playhead.value],
  set: (value: number[] | undefined) => {
    if (value?.[0] !== undefined)
      seek(value[0])
  },
})

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

function setEdge(edge: 'end' | 'start', tick = playhead.value): void {
  range.value = moveRangeEdge(range.value, edge, edge === 'end' ? tick + 1 : tick, play)
}

function onKeydown(event: KeyboardEvent): void {
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

/** Moves keyboard focus into the preview, onto the range start thumb. */
function focus(): void {
  root.value?.querySelector<HTMLElement>('[aria-label="Range start"]')?.focus()
}

defineExpose({ focus })

watch(() => timing.value.status, () => {
  if (!playing.value)
    seek(playhead.value)
})
onBeforeUnmount(() => video.value?.pause())
</script>

<template>
  <!--
    The popover is always open while mounted; the parent mounts and unmounts it. Reka places it
    beside the row and keeps it in the window, and closes it on Escape or an outside click. Focus is
    not moved on open (a hover must not steal the tree's focus) or on close (the parent returns
    focus to the tree).
  -->
  <PopoverRoot :open="true" @update:open="open => !open && emit('close')">
    <PopoverAnchor :reference="anchor" />
    <PopoverPortal>
      <PopoverContent
        :align-offset="-24"
        align="start"
        :aria-label="`Preview of ${replay.playerName ?? 'Play'}`"
        class="z-60 w-[380px] flex flex-col gap-2 border border-white/10 rounded-lg bg-neutral-900/97 p-2.5 text-xs text-neutral-200 shadow-[0_12px_40px_rgb(0_0_0/0.55)] backdrop-blur"
        :collision-padding="8"
        side="right"
        :side-offset="8"
        @close-auto-focus.prevent
        @keydown="onKeydown"
        @mouseenter="emit('hover', true)"
        @mouseleave="emit('hover', false)"
        @open-auto-focus.prevent
      >
        <div ref="root" class="contents">
          <header class="flex items-center gap-2">
            <PlayerAvatar :player="{ name: replay.playerName, uuid: replay.playerUuid }" :size="20" />
            <span class="min-w-0 flex-1 truncate font-medium">{{ replay.playerName ?? 'Unknown player' }}</span>
            <span class="shrink-0 text-[11px] text-neutral-500 tabular-nums">{{ formatDurationTicks(playTicks) }}</span>
            <ControlButton compact label="Close preview" title="Close preview (Esc)" @click="emit('close')">
              <span aria-hidden="true" class="i-mingcute-close-line" />
            </ControlButton>
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

          <!-- Playhead scrubber. A separate slider because a click on the range track moves the
               nearest range thumb, so the range slider cannot also own the playhead. -->
          <SliderRoot
            v-model="playheadValue"
            aria-label="Preview position"
            class="relative h-2.5 flex touch-none select-none items-center"
            :max="play.endServerTick"
            :min="play.startServerTick"
            thumb-alignment="overflow"
          >
            <SliderTrack class="relative h-1 grow rounded-full bg-white/8" />
            <SliderThumb
              aria-label="Preview position"
              :aria-valuetext="relative(playhead)"
              class="block h-2.5 w-1 rounded-full bg-white focus-visible:outline-2 focus-visible:outline-white/60"
            />
          </SliderRoot>

          <!-- Range: the whole Play spans the track; the bright band is what "Add range" adds. -->
          <SliderRoot
            v-model="rangeSteps"
            class="relative h-7 flex touch-none select-none items-center"
            :max="stepCount"
            :min="0"
            :min-steps-between-thumbs="1"
            thumb-alignment="overflow"
          >
            <SliderTrack class="relative h-full grow rounded bg-white/5">
              <SliderRange class="absolute inset-y-0 rounded bg-amber-300/20 ring-1 ring-amber-300/50 ring-inset" />
              <div class="pointer-events-none absolute inset-y-0 w-px bg-white" :style="{ left: percent(playhead) }" />
            </SliderTrack>
            <SliderThumb
              v-for="edge in (['start', 'end'] as const)"
              :key="edge"
              :aria-label="edge === 'start' ? 'Range start' : 'Range end'"
              :aria-valuetext="relative(edge === 'start' ? range.startServerTick : range.endServerTick)"
              class="block h-7 w-2.5 cursor-ew-resize rounded-sm bg-amber-300 focus-visible:outline-2 focus-visible:outline-white"
              :title="`${edge === 'start' ? 'Range start' : 'Range end'}: drag, or use ← → (Shift for 10 s)`"
            />
          </SliderRoot>

          <div class="flex items-center gap-1.5 text-[11px] text-neutral-400 tabular-nums">
            <ControlButton class="gap-1" compact label="Set range start at the playhead" title="Set range start at the playhead (I)" @click="setEdge('start')">
              In <Kbd>I</Kbd>
            </ControlButton>
            <span>{{ relative(range.startServerTick) }} – {{ relative(range.endServerTick) }}</span>
            <span class="text-neutral-200">{{ formatDurationTicks(range.endServerTick - range.startServerTick) }}</span>
            <ControlButton class="ml-auto gap-1" compact label="Set range end at the playhead" title="Set range end at the playhead (O)" @click="setEdge('end')">
              Out <Kbd>O</Kbd>
            </ControlButton>
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
        </div>
      </PopoverContent>
    </PopoverPortal>
  </PopoverRoot>
</template>
