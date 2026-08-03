<script setup lang="ts">
defineProps<{
  active?: boolean
  label: string
  title?: string
}>()
</script>

<template>
  <button
    :aria-label="label"
    :aria-pressed="active === undefined ? undefined : active"
    class="h-8 min-w-8 inline-flex items-center justify-center gap-1.5 rounded-md border-none bg-white/4 px-3 text-sm text-[#c7d0dc] transition-colors disabled:cursor-not-allowed hover:border-white/20 hover:bg-white/8 hover:text-white disabled:opacity-38"
    :class="active && 'border-[#70d7bd]/45 bg-[#70d7bd]/14 text-[#9ce6d2]'"
    :title="title ?? label"
    type="button"
  >
    <slot />
  </button>
</template>
