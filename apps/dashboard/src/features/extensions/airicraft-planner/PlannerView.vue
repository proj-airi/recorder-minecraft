<script setup lang="ts">
import type { PlannerCallRecord } from './module'

import { computed, shallowRef, watch } from 'vue'

import PlannerCallDetailsDialog from './PlannerCallDetailsDialog.vue'
import PlannerCallSummary from './PlannerCallSummary.vue'
import PlannerTranscript from './PlannerTranscript.vue'

import { useEditorWorkspaceContext } from '../../editor/composables/useEditorWorkspaceContext'

const context = useEditorWorkspaceContext()
const selection = computed(() => context.selectedExtension.value)
const call = computed(() => selection.value?.item.data as PlannerCallRecord | undefined)
const detailsOpen = shallowRef(false)

watch(() => selection.value?.item.id, () => {
  detailsOpen.value = false
})
</script>

<template>
  <div class="h-full overflow-auto bg-[#171717] p-4 text-neutral-200">
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
        Select a planner call on the timeline.
      </p>
    </div>
  </div>
</template>
