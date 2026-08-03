<script setup lang="ts">
import type { IDockviewPanelHeaderProps } from 'dockview-vue'

import { computed, onBeforeUnmount, onMounted, useTemplateRef } from 'vue'

interface EditorDockTabParams {
  tab?: {
    showTitle?: boolean
  }
}

const props = defineProps<{
  params: IDockviewPanelHeaderProps
}>()

const showTitle = computed(() => (props.params.params as EditorDockTabParams).tab?.showTitle !== false)
const tabContent = useTemplateRef<HTMLElement>('tabContent')
let titleListener: { dispose: () => void } | undefined

function setAccessibleName(title = props.params.api.title): void {
  tabContent.value?.closest('[role="tab"]')?.setAttribute('aria-label', title ?? 'Editor view')
}

function activate(): void {
  props.params.api.setActive()
}

function close(): void {
  props.params.api.close()
}

function limitDragging(event: PointerEvent): void {
  const target = event.target
  if (!(target instanceof Element) || !target.closest('[data-dockview-drag-handle]'))
    event.stopPropagation()
}

onMounted(() => {
  setAccessibleName()
  titleListener = props.params.api.onDidTitleChange(event => setAccessibleName(event.title))
})
onBeforeUnmount(() => titleListener?.dispose())
</script>

<template>
  <!-- NOTICE: The Dockview Vue renderer passes header API values inside a `params` prop. Preserve
       that adapter contract while replacing the default tab with compact pane chrome. See
       `https://github.com/mathuo/dockview/blob/08097bd22495af8db171698355dffde93b9f5a88/packages/dockview-vue/src/utils.ts#L242-L260`. -->
  <div
    ref="tabContent"
    class="group bg-neutral-850 relative h-full min-w-0 w-full flex items-center pl-1 pr-5 text-[10px] text-neutral-400"
    @click="activate"
    @pointerdown="limitDragging"
  >
    <span v-if="showTitle" class="min-w-0 truncate px-1">
      {{ params.api.title }}
    </span>

    <span
      data-dockview-drag-handle
      aria-hidden="true"
      class="i-mingcute-more-1-fill absolute left-1/2 shrink-0 cursor-grab text-sm text-neutral-500 opacity-0 transition-opacity -translate-x-1/2 active:cursor-grabbing group-hover:opacity-100"
    />

    <button
      :aria-label="`Close ${params.api.title ?? 'view'}`"
      class="absolute right-0.5 h-4 w-4 flex items-center justify-center border-0 rounded-sm bg-transparent p-0 text-neutral-600 hover:bg-white/8 hover:text-neutral-200"
      :title="`Close ${params.api.title ?? 'view'}`"
      type="button"
      @click.stop="close"
      @pointerdown.stop.prevent
    >
      <span aria-hidden="true" class="i-mingcute-close-line text-[10px]" />
    </button>
  </div>
</template>

<style scoped>
@media (pointer: coarse) {
  [data-dockview-drag-handle] {
    opacity: 1;
  }
}
</style>
