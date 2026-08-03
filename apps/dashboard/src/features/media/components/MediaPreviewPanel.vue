<script setup lang="ts">
import { useResizeObserver } from '@vueuse/core'
import { computed, useTemplateRef } from 'vue'

import { useEditorWorkspaceContext } from '../../editor/composables/useEditorWorkspaceContext'
import { useMediaPreview } from '../composables/useMediaPreview'

const { catalog, session } = useEditorWorkspaceContext()
const preview = useTemplateRef<HTMLCanvasElement>('preview')
const previewHost = useTemplateRef<HTMLElement>('previewHost')
const sourceUrl = computed(() => {
  const path = catalog.selectedReplay.value?.video?.url
  return path ? new URL(path, window.location.href).toString() : null
})
const media = useMediaPreview(sourceUrl, session.playheadTick, preview)
const previewTime = computed(() => `${formatSeconds(media.currentTimeSeconds.value)} / ${formatSeconds(media.durationSeconds.value)}`)

function formatSeconds(value: number): string {
  const minutes = Math.floor(value / 60)
  const seconds = value - minutes * 60
  return `${String(minutes).padStart(2, '0')}:${seconds.toFixed(3).padStart(6, '0')}`
}

useResizeObserver(previewHost, () => void media.redraw())
</script>

<template>
  <section class="h-full min-h-0 flex flex-col bg-neutral-950" aria-label="Video preview">
    <header class="h-9 flex shrink-0 items-center justify-between border-b border-white/8 px-3 text-xs text-neutral-400">
      <span class="truncate">{{ catalog.selectedReplay.value?.playerName ?? 'No replay selected' }}</span>
      <span v-if="catalog.selectedReplay.value?.video" class="flex items-center gap-2 text-xs text-neutral-500">
        <output aria-label="Decoded preview time" class="[font-variant-numeric:tabular-nums] font-mono">{{ previewTime }}</output>
        <span>{{ catalog.selectedReplay.value.video.width || '—' }}×{{ catalog.selectedReplay.value.video.height || '—' }}</span>
      </span>
    </header>
    <div ref="previewHost" class="relative min-h-0 flex flex-1 items-center justify-center overflow-hidden">
      <canvas v-show="sourceUrl && !media.error.value" ref="preview" class="h-full w-full" aria-label="Decoded video preview" />
      <p v-if="media.isLoading.value" class="absolute m-0 text-xs text-neutral-500">
        Opening video…
      </p>
      <p v-else-if="media.error.value" class="m-0 max-w-sm p-4 text-center text-xs text-red-300">
        {{ media.error.value }}
      </p>
      <p v-else-if="catalog.selectedReplay.value?.validationError" class="m-0 max-w-sm p-4 text-center text-sm text-red-300">
        {{ catalog.selectedReplay.value.validationError }}
      </p>
      <p v-else-if="!sourceUrl" class="m-0 max-w-sm p-4 text-center text-sm text-neutral-500">
        Select a replay with a composed video to preview it.
      </p>
    </div>
  </section>
</template>
