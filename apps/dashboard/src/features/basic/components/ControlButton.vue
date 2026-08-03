<script setup lang="ts">
defineProps<{
  label: string
  title: string
}>()
</script>

<template>
  <button
    :aria-label="label"
    class="h-8 min-w-8 inline-flex items-center justify-center gap-1.5 rounded-md border-none bg-transparent px-2 text-sm text-[#c7d0dc] transition-colors disabled:cursor-not-allowed hover:bg-white/8 hover:text-white disabled:opacity-38"
    :title="title"
    type="button"
  >
    <slot />
  </button>
</template>
