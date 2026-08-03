<script setup lang="ts">
import TimelineCanvas from './TimelineCanvas.vue'

import { useTimelineDockContext } from './timelineDockContext'

defineOptions({ inheritAttrs: false })

const context = useTimelineDockContext()
</script>

<template>
  <section aria-label="Timeline" class="h-full min-h-0" role="region">
    <TimelineCanvas
      :engine="context.session.engine.value"
      :render-revision="context.session.renderRevision.value"
      :scroll-top="context.verticalScrollTop.value"
      :selected-segment-id="context.session.selectedSegmentId.value"
      @cancel-edit="context.session.rebuild"
      @commit-edit="context.session.commitEdit"
      @seek="context.session.seekToTick"
      @select-segment="context.session.selectSegment"
    />
  </section>
</template>
