<script setup lang="ts">
import { computed, onBeforeUnmount, shallowRef } from 'vue'

const props = defineProps<{
  /** Opens the value in a new tab when set. Relative URLs resolve against the page. */
  href?: string
  label: string
  /** Copied text; defaults to the absolute `href`, or `value`. */
  copyText?: string
  value: string
}>()

const copied = shallowRef(false)
let resetTimer: ReturnType<typeof setTimeout> | undefined
const absoluteHref = computed(() => props.href ? new URL(props.href, window.location.href).href : undefined)

async function copy(): Promise<void> {
  try {
    await navigator.clipboard.writeText(props.copyText ?? absoluteHref.value ?? props.value)
    copied.value = true
    clearTimeout(resetTimer)
    resetTimer = setTimeout(() => {
      copied.value = false
    }, 1500)
  }
  catch {
    copied.value = false
  }
}

onBeforeUnmount(() => clearTimeout(resetTimer))
</script>

<template>
  <div class="group min-w-0 flex items-center gap-1">
    <span class="w-20 shrink-0 truncate text-[10px] text-neutral-500" :title="label">{{ label }}</span>
    <code class="min-w-0 flex-1 select-all truncate text-[10px] text-neutral-300" :title="value">{{ value }}</code>
    <a
      v-if="absoluteHref"
      :aria-label="`Open ${label}`"
      class="h-5 w-5 flex shrink-0 items-center justify-center rounded text-neutral-500 hover:bg-white/8 hover:text-neutral-100"
      :href="absoluteHref"
      rel="noopener"
      target="_blank"
      :title="`Open ${label}`"
    >
      <span aria-hidden="true" class="i-mingcute-external-link-line text-xs" />
    </a>
    <button
      :aria-label="`Copy ${label}`"
      class="h-5 w-5 flex shrink-0 items-center justify-center border-0 rounded bg-transparent p-0 text-neutral-500 hover:bg-white/8 hover:text-neutral-100"
      :title="copied ? 'Copied' : `Copy ${label}`"
      type="button"
      @click="copy"
    >
      <span aria-hidden="true" class="text-xs" :class="copied ? 'i-mingcute-check-line text-emerald-300' : 'i-mingcute-copy-2-line'" />
    </button>
  </div>
</template>
