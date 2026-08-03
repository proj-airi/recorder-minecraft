<script setup lang="ts">
import type { ControlStateSample } from '../composables/useReplayControlState'

import { computed } from 'vue'

const props = defineProps<{ sample: ControlStateSample | null }>()
const heading = computed(() => `rotate(${props.sample?.cameraYaw ?? 0} 50 50)`)
</script>

<template>
  <section class="h-full min-h-0 flex flex-col bg-neutral-950 p-3" aria-label="Reconstructed mouse camera motion">
    <header class="flex items-center justify-between gap-2">
      <h2 class="m-0 text-sm text-neutral-200 font-medium">
        Mouse / camera
      </h2>
      <span class="text-xs text-neutral-500">Server-visible</span>
    </header>
    <div v-if="sample" class="min-h-0 flex flex-1 flex-col items-center justify-center gap-2">
      <svg class="h-16 w-16 shrink-0" viewBox="0 0 100 100" role="img" aria-label="Camera yaw compass">
        <circle cx="50" cy="50" r="44" fill="#171717" stroke="#404040" />
        <g :transform="heading">
          <line x1="50" y1="50" x2="50" y2="12" stroke="#fcd34d" stroke-width="4" stroke-linecap="round" />
          <circle cx="50" cy="50" r="5" fill="#fcd34d" />
        </g>
      </svg>
      <dl class="grid grid-cols-[auto_1fr] m-0 min-w-0 w-full gap-x-3 gap-y-1 text-xs">
        <dt class="text-neutral-500">
          Yaw
        </dt><dd class="m-0 text-right text-neutral-200 font-mono">
          {{ sample.cameraYaw.toFixed(2) }}°
        </dd>
        <dt class="text-neutral-500">
          Pitch
        </dt><dd class="m-0 text-right text-neutral-200 font-mono">
          {{ sample.cameraPitch.toFixed(2) }}°
        </dd>
        <dt class="text-neutral-500">
          Δ yaw
        </dt><dd class="m-0 text-right text-neutral-200 font-mono">
          {{ sample.cameraDeltaYaw.toFixed(3) }}°
        </dd>
        <dt class="text-neutral-500">
          Δ pitch
        </dt><dd class="m-0 text-right text-neutral-200 font-mono">
          {{ sample.cameraDeltaPitch.toFixed(3) }}°
        </dd>
      </dl>
    </div>
    <p v-else class="m-auto text-center text-sm text-neutral-500">
      No camera sample at this time.
    </p>
  </section>
</template>
