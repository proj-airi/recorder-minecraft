<script setup lang="ts">
withDefaults(defineProps<{
  compact?: boolean
  label: string
  title: string
}>(), {
  compact: false,
})
</script>

<template>
  <button
    :aria-label="label"
    class="inline-flex items-center justify-center rounded-md border-none bg-transparent text-[#c7d0dc] transition-colors disabled:cursor-not-allowed hover:bg-white/8 hover:text-white disabled:opacity-38"
    :class="compact ? 'h-6 min-w-6 px-1 text-xs' : 'h-8 min-w-8 gap-1.5 px-2 text-sm'"
    :title="title"
    type="button"
  >
    <slot />
  </button>
</template>
