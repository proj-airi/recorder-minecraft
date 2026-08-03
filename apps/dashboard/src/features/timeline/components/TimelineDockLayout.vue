<script setup lang="ts">
import type { ContextMenuItem, DockviewIDisposable, DockviewReadyEvent, VueComponent } from 'dockview-vue'

import { DockviewVue, themeDark } from 'dockview-vue'
import { markRaw, onBeforeUnmount } from 'vue'

import TimelineDockTab from './TimelineDockTab.vue'
import TimelineEditorPanel from './TimelineEditorPanel.vue'

const emit = defineEmits<{
  close: []
}>()

const components: Record<string, VueComponent> = markRaw({
  timelineEditor: TimelineEditorPanel as unknown as VueComponent,
})
const tabComponents: Record<string, VueComponent> = markRaw({
  timelineDockTab: TimelineDockTab as unknown as VueComponent,
})
let removalListener: DockviewIDisposable | undefined

function contextMenuItems(): ContextMenuItem[] {
  // NOTICE: Dockview supports custom tab context-menu actions in place of its built-in per-panel
  // close commands. This action closes the owning editor so Tracks and Timeline share a lifecycle.
  // See `https://github.com/mathuo/dockview/blob/08097bd22495af8db171698355dffde93b9f5a88/packages/dockview-core/src/dockview/options.ts#L263-L273`.
  return [{ action: () => emit('close'), label: 'Close timeline editor' }]
}

function onReady({ api }: DockviewReadyEvent): void {
  api.addPanel({
    component: 'timelineEditor',
    id: 'timeline-editor',
    minimumHeight: 240,
    minimumWidth: 480,
    renderer: 'always',
    tabComponent: 'timelineDockTab',
    title: 'Timeline editor',
  })

  removalListener = api.onDidRemovePanel(() => emit('close'))
}

onBeforeUnmount(() => removalListener?.dispose())
</script>

<template>
  <!-- NOTICE: Dockview's Vue adapter keeps dynamic panel components under the caller's Vue app
       with Teleport, so TimelineEditor provide/inject state remains available after docking. See
       `https://github.com/mathuo/dockview/blob/08097bd22495af8db171698355dffde93b9f5a88/packages/dockview-vue/src/dockview/dockview.vue#L73-L80`. -->
  <DockviewVue
    class="h-full min-h-0 w-full"
    :components="components"
    default-renderer="always"
    dnd-strategy="pointer"
    :get-tab-context-menu-items="contextMenuItems"
    :tab-components="tabComponents"
    :theme="themeDark"
    @ready="onReady"
  />
</template>

<style>
*:focus-visible {
  outline: none;
}
</style>
