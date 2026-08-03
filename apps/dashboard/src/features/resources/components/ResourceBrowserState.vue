<script setup lang="ts">
import { computed } from 'vue'

import Button from '../../basic/components/Button.vue'

const props = defineProps<{
  actionLabel?: string
  detail?: null | string
  kind: 'empty' | 'error' | 'filtered' | 'loading'
  message: string
  title: string
}>()

const emit = defineEmits<{
  action: []
}>()

const iconClass = computed(() => ({
  empty: 'i-mingcute-inbox-line text-neutral-600',
  error: 'i-mingcute-warning-fill text-red-400',
  filtered: 'i-mingcute-filter-line text-neutral-500',
  loading: 'i-mingcute-loading-3-line animate-spin text-neutral-500',
})[props.kind])
</script>

<template>
  <div class="min-h-48 flex flex-1 flex-col items-center justify-center px-6 py-10 text-center">
    <span aria-hidden="true" class="mb-4 text-3xl" :class="iconClass" />
    <p class="m-0 text-base text-neutral-100 font-medium">
      {{ title }}
    </p>
    <p class="m-0 mt-2 max-w-80 text-sm text-neutral-400 leading-relaxed">
      {{ message }}
    </p>
    <Button
      v-if="actionLabel"
      class="mt-5"
      :label="actionLabel"
      @click="emit('action')"
    >
      {{ actionLabel }}
    </Button>
    <details v-if="detail" class="mt-5 max-w-full text-left text-xs text-neutral-500">
      <summary class="cursor-pointer select-none hover:text-neutral-400">
        Technical details
      </summary>
      <code class="mt-2 block max-h-24 overflow-auto whitespace-pre-wrap break-all rounded bg-black/25 p-2.5 leading-relaxed">{{ detail }}</code>
    </details>
  </div>
</template>
