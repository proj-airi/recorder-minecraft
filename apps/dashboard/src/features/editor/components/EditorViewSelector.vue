<script setup lang="ts">
import type { EditorViewId, EditorViewOption } from '../views'

import { computed } from 'vue'

const props = defineProps<{
  views: EditorViewOption[]
}>()

const emit = defineEmits<{
  selectView: [viewId: EditorViewId]
}>()

const activeViewId = computed(() => props.views.find(view => view.active)?.id ?? '')

function selectView(event: Event): void {
  const viewId = (event.currentTarget as HTMLSelectElement).value as EditorViewId
  if (props.views.some(view => view.id === viewId))
    emit('selectView', viewId)
}
</script>

<template>
  <label class="relative flex items-center">
    <span class="sr-only">Choose editor view</span>
    <span aria-hidden="true" class="i-mingcute-layout-4-line pointer-events-none absolute left-2 text-sm text-neutral-400" />
    <select
      aria-label="Choose editor view"
      class="h-8 min-w-30 appearance-none rounded-md bg-white/4 pl-7 pr-7 text-xs text-[#c7d0dc] outline-none transition-colors focus:border-[var(--dashboard-border-color-strong)] hover:bg-white/8 hover:text-white"
      :value="activeViewId"
      @change="selectView"
    >
      <option disabled value="">
        Views
      </option>
      <option
        v-for="view in views"
        :key="view.id"
        :value="view.id"
      >
        {{ view.label }} · {{ view.active ? 'Active' : view.open ? 'Open' : 'Closed' }}
      </option>
    </select>
    <span aria-hidden="true" class="i-mingcute-down-line pointer-events-none absolute right-2 text-xs text-neutral-500" />
  </label>
</template>
