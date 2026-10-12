<script setup lang="ts">
import { useId } from 'vue'

// Drawn rather than taken from an icon set so each button lights up on its own. Wheel input is not
// captured, so the wheel is drawn but never lit.
defineProps<{
  left: boolean
  right: boolean
}>()

// Gradient ids are document-global; one per instance keeps cards independent.
const shellId = `mouse-shell-${useId()}`
</script>

<template>
  <svg class="block h-12 w-auto shrink-0" viewBox="0 0 40 60" aria-hidden="true">
    <defs>
      <linearGradient :id="shellId" x1="0" x2="0" y1="0" y2="1">
        <stop offset="0" stop-color="#404040" />
        <stop offset="1" stop-color="#262626" />
      </linearGradient>
    </defs>
    <!-- Shell: a rounded capsule, slightly narrower at the top like a real mouse. -->
    <path d="M20 2 C9 2 3 10 3 22 V40 C3 51 10 58 20 58 C30 58 37 51 37 40 V22 C37 10 31 2 20 2 Z" :fill="`url(#${shellId})`" stroke="#737373" stroke-opacity="0.55" stroke-width="1.2" />
    <!-- Buttons split at the wheel and end at the 24-unit seam. -->
    <path class="transition-[fill] duration-80 ease-out" :class="left ? 'fill-emerald-300 drop-shadow-[0_0_3px_rgb(110_231_183/0.6)]' : 'fill-white/3'" d="M19 3.2 C10 3.6 4.4 10.6 4.4 22 V24 H19 Z" />
    <path class="transition-[fill] duration-80 ease-out" :class="right ? 'fill-emerald-300 drop-shadow-[0_0_3px_rgb(110_231_183/0.6)]' : 'fill-white/3'" d="M21 3.2 C30 3.6 35.6 10.6 35.6 22 V24 H21 Z" />
    <line x1="4" x2="36" y1="24.5" y2="24.5" stroke="#171717" stroke-width="1" />
    <rect x="17.5" y="8" width="5" height="10" rx="2.5" fill="#171717" stroke="#737373" stroke-opacity="0.6" stroke-width="0.8" />
    <line x1="20" x2="20" y1="10.5" y2="15.5" stroke="#737373" stroke-opacity="0.7" stroke-dasharray="1 1.2" stroke-width="1.6" />
  </svg>
</template>
