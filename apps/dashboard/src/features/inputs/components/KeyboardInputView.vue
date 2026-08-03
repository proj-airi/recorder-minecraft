<script setup lang="ts">
import type { ControlStateSample } from '../composables/useReplayControlState'

defineProps<{ sample: ControlStateSample | null }>()

const keys = [
  { field: 'forward', label: 'W' },
  { field: 'left', label: 'A' },
  { field: 'backward', label: 'S' },
  { field: 'right', label: 'D' },
  { field: 'jump', label: 'Space' },
  { field: 'sneak', label: 'Sneak' },
  { field: 'sprint', label: 'Sprint' },
] as const
</script>

<template>
  <section class="h-full min-h-0 flex flex-col bg-neutral-950 p-3" aria-label="Reconstructed keyboard controls">
    <header class="flex items-center justify-between gap-2">
      <h2 class="m-0 text-sm text-neutral-200 font-medium">
        Keyboard
      </h2>
      <span class="text-xs text-neutral-500">Tick {{ sample?.serverTick ?? '—' }}</span>
    </header>
    <div v-if="sample" class="grid grid-cols-2 flex-1 content-center gap-2" aria-live="polite">
      <span
        v-for="key in keys"
        :key="key.field"
        class="min-h-10 flex items-center justify-center border rounded px-2 text-xs font-medium transition-colors"
        :class="sample[key.field] ? 'border-amber-300 bg-amber-300 text-neutral-950' : 'border-white/12 bg-white/4 text-neutral-400'"
      >
        {{ key.label }}
      </span>
      <span class="min-h-10 flex items-center justify-center border border-white/12 rounded bg-white/4 px-2 text-xs text-neutral-400">
        Slot {{ sample.selectedSlot + 1 }}
      </span>
    </div>
    <p v-else class="m-auto text-center text-sm text-neutral-500">
      No control sample at this time.
    </p>
  </section>
</template>
