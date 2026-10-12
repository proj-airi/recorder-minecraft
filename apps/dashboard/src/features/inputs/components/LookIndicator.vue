<script setup lang="ts">
import { computed } from 'vue'

import { yawHeading } from '../itemNames'

const props = defineProps<{
  deltaPitch: number
  deltaYaw: number
  pitch: number
  yaw: number
}>()

// Seen from above with north up: Minecraft yaw 0 faces south (+Z, screen down) and grows
// clockwise, so screen angle = yaw + 90° measured from +X with y pointing down.
const needle = computed(() => {
  const radians = (props.yaw + 90) * Math.PI / 180
  return { x: 20 + Math.cos(radians) * 11.5, y: 22 + Math.sin(radians) * 11.5 }
})
// Pitch -90 (up) .. 90 (down) maps onto the 30-unit gauge; its midpoint (22) is the horizon.
const pitchY = computed(() => 7 + (Math.max(-90, Math.min(90, props.pitch)) + 90) / 180 * 30)
const turning = computed(() => Math.hypot(props.deltaYaw, props.deltaPitch) > 0.05)
const heading = computed(() => yawHeading(props.yaw))
</script>

<template>
  <figure class="m-0 flex shrink-0 flex-col items-center gap-0.5" :aria-label="`Facing ${heading}, yaw ${yaw.toFixed(1)}°, pitch ${pitch.toFixed(1)}°`">
    <svg class="h-10 w-12 shrink-0" viewBox="0 -2 48 42" aria-hidden="true">
      <circle cx="20" cy="22" r="14.5" fill="#ffffff" fill-opacity="0.04" stroke="#525252" />
      <!-- North marker: a small notch above the dial (text this small renders unreadably). -->
      <path d="M17.5 3.5 L20 7 L22.5 3.5 Z" fill="#a3a3a3" />
      <line x1="20" y1="22" :x2="needle.x" :y2="needle.y" :stroke="turning ? '#6ee7b7' : '#d4d4d4'" stroke-linecap="round" stroke-width="2.4" />
      <circle cx="20" cy="22" r="2" fill="#e5e5e5" />
      <!-- Pitch gauge: top is looking straight up, bottom straight down, the tick is the horizon. -->
      <rect x="42" y="7" width="3" height="30" rx="1.5" fill="#ffffff" fill-opacity="0.1" />
      <line x1="40.5" x2="46.5" y1="22" y2="22" stroke="#525252" />
      <circle cx="43.5" :cy="pitchY" r="2.4" :fill="turning ? '#6ee7b7' : '#d4d4d4'" />
    </svg>
    <figcaption class="whitespace-nowrap text-[10px] text-neutral-400 leading-none font-mono tabular-nums">
      <span class="text-neutral-100 font-semibold font-sans">{{ heading }}</span> {{ yaw.toFixed(0) }}°/{{ pitch.toFixed(0) }}°
    </figcaption>
  </figure>
</template>
