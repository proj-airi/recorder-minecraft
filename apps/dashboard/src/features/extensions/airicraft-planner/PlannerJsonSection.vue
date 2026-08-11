<script setup lang="ts">
defineProps<{
  label: string
  value: unknown
}>()
</script>

<template>
  <section class="min-w-0 border border-white/8 rounded bg-black/20 p-3">
    <h3 class="m-0 mb-2 text-[10px] text-neutral-500 font-semibold tracking-wider uppercase">
      {{ label }}
    </h3>
    <pre class="m-0 overflow-auto whitespace-pre-wrap break-words text-[11px] text-neutral-300 leading-5 font-mono">{{ JSON.stringify(value ?? null, null, 2) }}</pre>
  </section>
</template>
