<script setup lang="ts">
import type { ContextMenuItem, DockviewIDisposable, DockviewReadyEvent, VueComponent } from 'dockview-vue'

import type { TimelineSession } from '../../timeline/composables/useTimelineSession'
import type { EpisodeDraft } from '../../timeline/domain'

import { DockviewVue, themeDark } from 'dockview-vue'
import { markRaw, onBeforeUnmount, onMounted, provide, toRef } from 'vue'

import InputMonitorPanel from '../../inputs/components/InputMonitorPanel.vue'
import MediaPreviewPanel from '../../media/components/MediaPreviewPanel.vue'
import ResourceBrowserPanel from '../../resources/components/ResourceBrowserPanel.vue'
import TimelineDockTab from '../../timeline/components/TimelineDockTab.vue'
import TimelineWorkspacePanel from './TimelineWorkspacePanel.vue'

import { useArtifactCatalog } from '../../resources/composables/useArtifactCatalog'
import { editorWorkspaceContextKey } from '../workspaceContext'

const props = defineProps<{
  canRedo: boolean
  canUndo: boolean
  episode: EpisodeDraft
  session: TimelineSession
}>()

const emit = defineEmits<{
  close: []
  cutSegment: [segmentId: string, atTick: number]
  redo: []
  reorderTrack: [sourceIndex: number, targetIndex: number]
  undo: []
}>()

const catalog = useArtifactCatalog()
const components: Record<string, VueComponent> = markRaw({
  inputMonitor: InputMonitorPanel as unknown as VueComponent,
  mediaPreview: MediaPreviewPanel as unknown as VueComponent,
  resourceBrowser: ResourceBrowserPanel as unknown as VueComponent,
  timeline: TimelineWorkspacePanel as unknown as VueComponent,
})
const tabComponents: Record<string, VueComponent> = markRaw({
  editorTab: TimelineDockTab as unknown as VueComponent,
})
let removalListener: DockviewIDisposable | undefined

provide(editorWorkspaceContextKey, {
  canRedo: toRef(props, 'canRedo'),
  canUndo: toRef(props, 'canUndo'),
  catalog,
  close: () => emit('close'),
  cutSegment: (segmentId, atTick) => emit('cutSegment', segmentId, atTick),
  episode: () => props.episode,
  redo: () => emit('redo'),
  reorderTrack: (sourceIndex, targetIndex) => emit('reorderTrack', sourceIndex, targetIndex),
  session: props.session,
  undo: () => emit('undo'),
})

function contextMenuItems(): ContextMenuItem[] {
  return [{ action: () => emit('close'), label: 'Close editor workspace' }]
}

function onReady({ api }: DockviewReadyEvent): void {
  // Preserve the requested proportions at ordinary editor sizes while keeping each pane usable
  // when the dashboard is embedded in a smaller parent container.
  const resourceWidth = Math.max(160, Math.round(api.width * 0.125))
  const inputWidth = Math.max(160, Math.round(api.width * 0.125))
  const timelineHeight = Math.max(140, Math.round(api.height * 0.3))

  const resources = api.addPanel({ component: 'resourceBrowser', id: 'resources', initialWidth: resourceWidth, minimumWidth: 160, renderer: 'always', tabComponent: 'editorTab', title: 'Resources' })
  api.addPanel({
    component: 'mediaPreview',
    id: 'preview',
    minimumHeight: 220,
    minimumWidth: 360,
    position: { direction: 'right', referencePanel: 'resources' },
    renderer: 'always',
    tabComponent: 'editorTab',
    title: 'Preview',
  })
  const inputs = api.addPanel({
    component: 'inputMonitor',
    id: 'inputs',
    initialWidth: inputWidth,
    minimumWidth: 160,
    position: { direction: 'right', referencePanel: 'preview' },
    renderer: 'always',
    tabComponent: 'editorTab',
    title: 'Inputs',
  })
  const timeline = api.addPanel({
    component: 'timeline',
    id: 'timeline',
    initialHeight: timelineHeight,
    minimumHeight: 140,
    minimumWidth: 480,
    position: { direction: 'below', referencePanel: 'preview' },
    renderer: 'always',
    tabComponent: 'editorTab',
    title: 'Timeline',
  })

  // Adding a neighboring group can redistribute an earlier group's initial size. Reapply the edge
  // sizes after the complete grid exists so Resources and Inputs each occupy 12.5% horizontally.
  resources.group.api.setSize({ width: resourceWidth })
  inputs.group.api.setSize({ width: inputWidth })
  timeline.group.api.setSize({ height: timelineHeight })

  removalListener = api.onDidRemovePanel((event) => {
    if (event.id === 'timeline')
      emit('close')
  })
}

onMounted(() => void catalog.load())
onBeforeUnmount(() => removalListener?.dispose())
</script>

<template>
  <!-- NOTICE: Dockview's Vue adapter teleports dynamic panels inside the caller's Vue app, which
       preserves the typed workspace provide/inject boundary. See
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
