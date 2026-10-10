<script setup lang="ts">
import type { ControlStateSample, HotbarSample } from '../../event-log/captureEvents'

import { computed } from 'vue'

import { shortItemName } from '../../event-log/captureEvents'

export type PlayerInputStatus = 'error' | 'idle' | 'loading' | 'no-events' | 'ready'

const props = defineProps<{
  color: string
  error?: null | string
  hotbar: HotbarSample | null
  playerName: string
  sample: ControlStateSample | null
  selected: boolean
  serverTick: null | number
  sessionLabel?: string
  status: PlayerInputStatus
}>()

const movementKeys = [
  { area: 'w', field: 'forward', label: 'W' },
  { area: 'a', field: 'left', label: 'A' },
  { area: 's', field: 'backward', label: 'S' },
  { area: 'd', field: 'right', label: 'D' },
] as const
const modifierKeys = [
  { field: 'jump', label: 'Jump' },
  { field: 'sneak', label: 'Sneak' },
  { field: 'sprint', label: 'Sprint' },
] as const

const selectedSlot = computed(() => props.hotbar?.selectedSlot ?? props.sample?.selectedSlot ?? null)
const slots = computed(() => Array.from({ length: 9 }, (_, index) => props.hotbar?.items[index] ?? null))
const pointer = computed(() => {
  const yaw = props.sample?.cameraDeltaYaw ?? 0
  const pitch = props.sample?.cameraDeltaPitch ?? 0
  const magnitude = Math.hypot(yaw, pitch)
  const length = magnitude === 0 ? 0 : Math.min(9, Math.max(3, magnitude * 0.6))
  return { x: 12 + (magnitude ? yaw / magnitude * length : 0), y: 12 + (magnitude ? pitch / magnitude * length : 0), zero: magnitude === 0 }
})
const pressedKeys = computed(() => {
  const sample = props.sample
  if (!sample)
    return 'none'
  const pressed: string[] = [...movementKeys, ...modifierKeys].filter(key => sample[key.field]).map(key => key.label)
  if (sample.leftClick)
    pressed.push('left mouse')
  if (sample.rightClick)
    pressed.push('right mouse')
  return pressed.join(', ') || 'none'
})
</script>

<template>
  <article
    :aria-current="selected ? 'true' : undefined"
    :aria-label="`Inputs of ${playerName}`"
    class="border rounded-md p-2"
    :class="selected ? 'border-amber-300/50 bg-amber-300/6' : 'border-[var(--dashboard-border-color)] bg-white/2'"
  >
    <header class="flex items-center gap-1.5">
      <span aria-hidden="true" class="h-2 w-2 shrink-0 rounded-full" :style="{ backgroundColor: color }" />
      <h3 class="m-0 min-w-0 truncate text-xs text-neutral-100 font-medium" :title="sessionLabel ? `${playerName} · ${sessionLabel}` : playerName">
        {{ playerName }}
      </h3>
      <span v-if="sessionLabel" class="min-w-0 truncate text-[10px] text-neutral-500">{{ sessionLabel }}</span>
      <span class="ml-auto shrink-0 text-[10px] text-neutral-500 font-mono tabular-nums">
        {{ serverTick === null ? 'off clip' : `tick ${serverTick}` }}
      </span>
    </header>

    <p v-if="status === 'idle'" class="m-0 mt-1.5 text-[11px] text-neutral-500">
      No clip of this player at the playhead.
    </p>
    <p v-else-if="status === 'loading'" class="m-0 mt-1.5 flex items-center gap-1 text-[11px] text-neutral-500">
      <span aria-hidden="true" class="i-mingcute-loading-3-line animate-spin" /> Loading control states…
    </p>
    <p v-else-if="status === 'error'" class="m-0 mt-1.5 text-[11px] text-red-300">
      {{ error ?? 'Events could not load.' }}
    </p>
    <p v-else-if="status === 'no-events'" class="m-0 mt-1.5 text-[11px] text-neutral-500">
      This Play has no events stream.
    </p>
    <template v-else>
      <div class="mt-1.5 flex flex-wrap items-center gap-2" :aria-label="`Pressed: ${pressedKeys}`" role="group">
        <div class="wasd grid gap-0.5" aria-hidden="true">
          <span
            v-for="key in movementKeys"
            :key="key.field"
            class="keycap"
            :class="sample?.[key.field] && 'pressed'"
            :style="{ gridArea: key.area }"
          >{{ key.label }}</span>
        </div>
        <div class="flex flex-col gap-0.5" aria-hidden="true">
          <span
            v-for="key in modifierKeys"
            :key="key.field"
            class="keycap px-1"
            :class="sample?.[key.field] && 'pressed'"
          >{{ key.label }}</span>
        </div>
        <div class="flex gap-0.5" aria-hidden="true">
          <span class="keycap mouse" :class="sample?.leftClick && 'clicked'">LMB</span>
          <span class="keycap mouse" :class="sample?.rightClick && 'clicked'">RMB</span>
        </div>
        <svg class="camera" viewBox="0 0 24 24" aria-hidden="true">
          <circle cx="12" cy="12" r="10.5" fill="none" stroke="rgb(82 82 82)" stroke-dasharray="2 2" />
          <line v-if="!pointer.zero" x1="12" y1="12" :x2="pointer.x" :y2="pointer.y" stroke="rgb(110 231 183)" stroke-linecap="round" stroke-width="2.5" />
          <circle cx="12" cy="12" r="1.5" fill="rgb(229 229 229)" />
        </svg>
      </div>

      <ol aria-label="Hotbar" class="hotbar">
        <li
          v-for="(item, index) in slots"
          :key="index"
          :aria-current="selectedSlot === index ? 'true' : undefined"
          :aria-label="`Slot ${index + 1}${item ? `: ${item.count} ${shortItemName(item.itemId)}` : ''}${selectedSlot === index ? ', selected' : ''}`"
          class="hotbar-slot"
          :class="selectedSlot === index && 'selected'"
          :title="item ? `${index + 1}: ${item.count}× ${item.itemId}` : `Slot ${index + 1}`"
        >
          <span class="text-[8px] text-neutral-500 leading-none">{{ index + 1 }}</span>
          <span v-if="item" class="max-w-full truncate text-[8px] text-neutral-200 leading-none">{{ shortItemName(item.itemId).slice(0, 4) }}</span>
        </li>
      </ol>

      <p v-if="sample" class="m-0 mt-1 truncate text-[10px] text-neutral-500 font-mono tabular-nums" :title="`Camera yaw ${sample.cameraYaw.toFixed(2)}°, pitch ${sample.cameraPitch.toFixed(2)}°, delta ${sample.cameraDeltaYaw.toFixed(2)}°, ${sample.cameraDeltaPitch.toFixed(2)}°`">
        yaw {{ sample.cameraYaw.toFixed(1) }}° · pitch {{ sample.cameraPitch.toFixed(1) }}°
      </p>
      <p v-else class="m-0 mt-1 text-[10px] text-neutral-500">
        No control sample yet at this tick.
      </p>
    </template>
  </article>
</template>

<style scoped>
.wasd {
  grid-template-areas:
    '. w .'
    'a s d';
  grid-template-columns: repeat(3, 1.25rem);
}

.keycap {
  align-items: center;
  background: rgb(255 255 255 / 4%);
  border: 1px solid var(--dashboard-border-color);
  border-radius: 0.2rem;
  color: rgb(163 163 163);
  display: inline-flex;
  font-size: 9px;
  height: 1.05rem;
  justify-content: center;
  min-width: 1.25rem;
}

.keycap.pressed {
  background: rgb(252 211 77);
  border-color: rgb(252 211 77);
  color: rgb(10 10 10);
  font-weight: 600;
}

.keycap.mouse {
  height: 2.2rem;
  width: 1.6rem;
}

.keycap.clicked {
  background: rgb(110 231 183);
  border-color: rgb(167 243 208);
  color: rgb(2 44 34);
  font-weight: 600;
}

.camera {
  flex-shrink: 0;
  height: 1.5rem;
  width: 1.5rem;
}

.hotbar {
  display: grid;
  gap: 2px;
  grid-template-columns: repeat(9, minmax(0, 1fr));
  list-style: none;
  margin: 0.375rem 0 0;
  padding: 0;
}

.hotbar-slot {
  align-items: center;
  aspect-ratio: 1;
  background: rgb(255 255 255 / 3%);
  border: 1px solid var(--dashboard-border-color);
  border-radius: 0.15rem;
  display: flex;
  flex-direction: column;
  gap: 1px;
  justify-content: center;
  min-width: 0;
  overflow: hidden;
}

.hotbar-slot.selected {
  border-color: rgb(252 211 77);
  box-shadow: inset 0 0 0 1px rgb(252 211 77);
}
</style>
