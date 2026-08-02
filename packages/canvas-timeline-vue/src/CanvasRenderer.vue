<!-- SPDX-License-Identifier: MPL-2.0 -->
<!-- NOTICE: Vue SFC port of the public component and default props from
`https://github.com/techsquidtv/canvas-timeline/blob/1536a2dbc54e3a333ace360894a2e4508b295cf1/packages/renderer/src/CanvasRenderer.tsx#L132-L180`. -->

<script setup lang="ts">
import type { CanvasRendererStats } from '@proj-airi/canvas-timeline-renderer'

import type { CanvasRendererError, CanvasRendererProps } from './types'

import { useTemplateRef } from 'vue'

import { useCanvasRenderer } from './useCanvasRenderer'

const props = withDefaults(defineProps<CanvasRendererProps>(), {
  className: '',
  showClipDropFeedback: true,
  showClipLabels: true,
  showClips: true,
  showInOutBoundaryLines: false,
  showInOutPoints: true,
  showRulerLabels: true,
  showSnapLines: true,
})

const emit = defineEmits<{
  renderError: [error: CanvasRendererError]
  renderStats: [stats: CanvasRendererStats]
}>()

const container = useTemplateRef<HTMLDivElement>('container')
useCanvasRenderer(container, props, {
  renderError: error => emit('renderError', error),
  renderStats: stats => emit('renderStats', stats),
})
</script>

<template>
  <div ref="container" class="timeline-canvas-layer" />
</template>

<style scoped>
.timeline-canvas-layer {
  height: 100%;
  inset: 0;
  pointer-events: none;
  position: absolute;
  width: 100%;
}
</style>
