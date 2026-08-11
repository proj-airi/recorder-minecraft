<script setup lang="ts">
import type { PlannerCallRecord } from './module'

import { computed, nextTick, shallowRef, useTemplateRef, watch } from 'vue'

import PlannerCallDetailsDialog from './PlannerCallDetailsDialog.vue'
import PlannerCallSummary from './PlannerCallSummary.vue'
import PlannerTranscript from './PlannerTranscript.vue'

import { useEditorWorkspaceContext } from '../../editor/composables/useEditorWorkspaceContext'
import { AIRICRAFT_PLANNER_TYPE } from './constants'

const context = useEditorWorkspaceContext()
const scroller = useTemplateRef<HTMLDivElement>('scroller')
const selection = computed(() => {
  const selected = context.selectedExtension.value
  return selected?.descriptor.extensionType === AIRICRAFT_PLANNER_TYPE
    ? selected
    : context.extensionAtPlayhead(AIRICRAFT_PLANNER_TYPE)
})
const call = computed(() => selection.value?.item.data as PlannerCallRecord | undefined)
const detailsOpen = shallowRef(false)

watch(() => call.value?.callId, async (callId, previousCallId) => {
  if (!callId)
    return

  await nextTick()
  scroller.value?.scrollTo({
    behavior: previousCallId ? 'smooth' : 'auto',
    top: scroller.value.scrollHeight,
  })
}, { flush: 'post', immediate: true })

watch(() => selection.value?.item.id, () => {
  detailsOpen.value = false
})
</script>

<template>
  <div ref="scroller" aria-label="Planner call transcript" class="h-full overflow-auto bg-[#171717] p-4 text-neutral-200">
    <div v-if="call && selection" class="mx-auto max-w-5xl flex flex-col gap-3">
      <PlannerCallSummary
        :call="call"
        :play-server-tick="selection.playServerTick"
        @show-details="detailsOpen = true"
      />

      <PlannerTranscript :call="call" />

      <PlannerCallDetailsDialog :call="call" :open="detailsOpen" @close="detailsOpen = false" />
    </div>

    <div v-else class="h-full flex flex-col items-center justify-center gap-2 text-center text-neutral-500">
      <span aria-hidden="true" class="i-mingcute-ai-line text-2xl" />
      <p class="m-0 text-xs">
        Move the playhead over a planner call or select one on the timeline.
      </p>
    </div>
  </div>
</template>
