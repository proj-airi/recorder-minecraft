<script setup lang="ts">
import type { ControlStateSample } from '../composables/useReplayControlState'

import { computed } from 'vue'

const props = defineProps<{ sample: ControlStateSample | null }>()

const pointer = computed(() => {
  const yaw = props.sample?.cameraDeltaYaw ?? 0
  const pitch = props.sample?.cameraDeltaPitch ?? 0
  const magnitude = Math.hypot(yaw, pitch)
  const displayLength = magnitude === 0 ? 0 : Math.min(45, Math.max(12, magnitude * 2.4))
  const scale = magnitude === 0 ? 0 : displayLength / magnitude
  return {
    x: 60 + yaw * scale,
    y: 60 + pitch * scale,
    zero: magnitude === 0,
  }
})
</script>

<template>
  <section class="h-full min-h-0 flex flex-col bg-neutral-950 p-2" aria-label="Reconstructed mouse and camera motion">
    <header class="flex items-center justify-between gap-2">
      <h2 class="m-0 text-sm text-neutral-200 font-medium">
        Mouse / camera
      </h2>
      <span class="text-xs text-neutral-500">Tick {{ sample?.serverTick ?? '—' }}</span>
    </header>

    <div v-if="sample" class="min-h-0 flex flex-1 flex-col justify-center gap-2">
      <div class="grid grid-cols-[2.75rem_minmax(0,1fr)] items-stretch gap-2">
        <div class="mouse-shell" aria-label="Observed mouse click actions">
          <span class="mouse-button mouse-button-left" :class="{ active: sample.leftClick }">
            <kbd>LMB</kbd>
          </span>
          <span class="mouse-button mouse-button-right" :class="{ active: sample.rightClick }">
            <kbd>RMB</kbd>
          </span>
          <span class="mouse-wheel" aria-hidden="true" />
          <span class="mouse-palm" aria-hidden="true" />
        </div>

        <figure class="m-0 min-w-0 border border-[var(--dashboard-border-color)] rounded-lg bg-black/25 p-1.5" :aria-label="`Camera delta: yaw ${sample.cameraDeltaYaw.toFixed(2)} degrees, pitch ${sample.cameraDeltaPitch.toFixed(2)} degrees`">
          <figcaption class="text-[9px] text-neutral-400 font-mono">
            <output>{{ sample.cameraDeltaYaw.toFixed(1) }}°, {{ sample.cameraDeltaPitch.toFixed(1) }}°</output>
          </figcaption>
          <svg class="mx-auto block h-16 w-full" viewBox="0 0 120 120" aria-hidden="true">
            <defs>
              <marker id="mouse-arrowhead" marker-height="5" marker-width="5" orient="auto-start-reverse" ref-x="4.5" ref-y="2.5">
                <path d="M 0 0 L 5 2.5 L 0 5 z" fill="#6ee7b7" />
              </marker>
            </defs>
            <line class="mouse-axis" x1="8" y1="60" x2="112" y2="60" />
            <line class="mouse-axis" x1="60" y1="8" x2="60" y2="112" />
            <text x="96" y="54">yaw</text>
            <text x="65" y="13">pitch</text>
            <circle cx="60" cy="60" r="3" fill="#e5e5e5" />
            <line
              class="mouse-vector"
              :class="{ zero: pointer.zero }"
              marker-end="url(#mouse-arrowhead)"
              x1="60"
              :x2="pointer.x"
              y1="60"
              :y2="pointer.y"
            />
          </svg>
        </figure>
      </div>

      <p class="m-0 truncate text-[10px] text-neutral-500 font-mono" :title="`Yaw ${sample.cameraYaw.toFixed(2)}°, pitch ${sample.cameraPitch.toFixed(2)}°. Clicks are momentary server-applied actions.`">
        Yaw {{ sample.cameraYaw.toFixed(1) }}° · Pitch {{ sample.cameraPitch.toFixed(1) }}°
      </p>
    </div>
    <p v-else class="m-auto text-center text-sm text-neutral-500">
      No camera sample at this time.
    </p>
  </section>
</template>

<style scoped>
.mouse-shell {
  display: grid;
  grid-template: "left right" 1.75rem "palm palm" 3.25rem / 1fr 1fr;
  min-width: 0;
  position: relative;
}

.mouse-button,
.mouse-palm {
  background: rgb(23 23 23);
  border: 1px solid var(--dashboard-border-color);
}

.mouse-button {
  align-items: center;
  color: rgb(115 115 115);
  display: flex;
  font-size: 0.5rem;
  justify-content: center;
  transition: background-color 80ms ease, border-color 80ms ease, color 80ms ease, box-shadow 80ms ease;
}

.mouse-button-left {
  border-radius: 2rem 0.35rem 0 0;
  grid-area: left;
}

.mouse-button-right {
  border-radius: 0.35rem 2rem 0 0;
  grid-area: right;
}

.mouse-button.active {
  background: rgb(110 231 183);
  border-color: rgb(167 243 208);
  box-shadow: 0 0 1rem rgb(110 231 183 / 35%);
  color: rgb(2 44 34);
}

.mouse-wheel {
  background: rgb(64 64 64);
  border: 1px solid var(--dashboard-border-color);
  border-radius: 999px;
  height: 1rem;
  left: 50%;
  position: absolute;
  top: 0.42rem;
  transform: translateX(-50%);
  width: 0.32rem;
}

.mouse-palm {
  border-radius: 0 0 2.5rem 2.5rem;
  border-top: 0;
  grid-area: palm;
}

.mouse-axis {
  stroke: rgb(82 82 82);
  stroke-dasharray: 3 4;
  stroke-width: 1;
}

.mouse-vector {
  filter: drop-shadow(0 0 4px rgb(110 231 183 / 55%));
  stroke: rgb(110 231 183);
  stroke-linecap: round;
  stroke-width: 4;
}

.mouse-vector.zero {
  opacity: 0;
}

text {
  fill: rgb(115 115 115);
  font: 8px ui-monospace, monospace;
}
</style>
