<script setup lang="ts">
import TimelineCanvas from './TimelineCanvas.vue'

import { useTimelineDockContext } from './timelineDockContext'

defineOptions({ inheritAttrs: false })

const context = useTimelineDockContext()
</script>

<template>
  <section aria-label="Timeline" class="relative h-full min-h-0" role="region">
    <TimelineCanvas
      :engine="context.session.engine.value"
      :editable="context.editable.value"
      :render-revision="context.session.renderRevision.value"
      :scroll-top="context.verticalScrollTop.value"
      :selected-segment-id="context.session.selectedSegmentId.value"
      @cancel-edit="context.session.rebuild"
      @commit-edit="context.session.commitEdit"
      @seek="context.session.seekToTick"
      @select-segment="context.session.selectSegment"
    />
    <div
      v-if="context.episode.value.tracks.length === 0"
      class="pointer-events-none absolute inset-0 flex flex-col items-center justify-center gap-2 p-6 text-center text-neutral-500"
    >
      <span aria-hidden="true" class="i-mingcute-download-2-line text-2xl" />
      <p class="m-0 text-xs">
        Drag replay resources here to create synchronized camera tracks.
      </p>
    </div>
  </section>
</template>
