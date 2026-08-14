<script setup lang="ts">
import type { PlannerCallRecord } from './module'

import { nextTick, useTemplateRef, watch } from 'vue'

import PlannerJsonSection from './PlannerJsonSection.vue'

const props = defineProps<{
  call: PlannerCallRecord
  open: boolean
}>()

const emit = defineEmits<{
  close: []
}>()

const dialog = useTemplateRef<HTMLDialogElement>('dialog')

watch(() => props.open, async (open) => {
  await nextTick()
  if (open && !dialog.value?.open)
    dialog.value?.showModal()
  else if (!open && dialog.value?.open)
    dialog.value.close()
}, { immediate: true })
</script>

<template>
  <dialog
    ref="dialog"
    aria-labelledby="planner-call-details-title"
    class="planner-call-details m-auto max-h-[calc(100vh-3rem)] w-[min(46rem,calc(100vw-2rem))] border border-white/12 rounded-lg bg-[#171717] p-0 text-neutral-200 shadow-2xl"
    @cancel.prevent="emit('close')"
    @click.self="emit('close')"
    @close="emit('close')"
  >
    <template v-if="open">
      <header class="sticky top-0 z-1 flex items-center justify-between gap-3 border-b border-white/8 bg-[#171717] px-4 py-3">
        <div class="min-w-0">
          <p class="m-0 text-[10px] text-violet-300 tracking-wider uppercase">
            Airicraft planner call {{ call.sequence }}
          </p>
          <h2 id="planner-call-details-title" class="m-0 mt-1 text-sm font-semibold">
            Call details
          </h2>
        </div>
        <button
          type="button"
          aria-label="Close call details"
          class="h-8 w-8 flex items-center justify-center border-0 rounded bg-transparent text-neutral-400 hover:bg-white/8 hover:text-white"
          @click="emit('close')"
        >
          <span aria-hidden="true" class="i-mingcute-close-line" />
        </button>
      </header>

      <div class="grid gap-3 overflow-auto p-4 md:grid-cols-2">
        <PlannerJsonSection class="md:col-span-2" label="Request tools" :value="call.request?.tools" />
        <PlannerJsonSection label="Usage" :value="call.outcome?.usage" />
        <PlannerJsonSection label="Timeline anchors" :value="call.timeline" />
      </div>
    </template>
  </dialog>
</template>

<style scoped>
.planner-call-details::backdrop {
  background: rgb(0 0 0 / 68%);
  backdrop-filter: blur(2px);
}
</style>
