<script setup lang="ts">
import type { RecorderMinecraftApiV1Replay } from '@proj-airi/recorder-minecraft-api'
import type { AddPanelOptions, ContextMenuItem, DockviewApi, DockviewIDisposable, DockviewReadyEvent, IDockviewPanel, VueComponent } from 'dockview-vue'

import type { TimelineSession } from '../../timeline/composables/useTimelineSession'
import type { EpisodeDraft } from '../../timeline/domain'
import type { EditorViewId, EditorViewOption } from '../views'

import { useResizeObserver } from '@vueuse/core'
import { DockviewVue, themeDark } from 'dockview-vue'
import { markRaw, onBeforeUnmount, onMounted, provide, toRef, useTemplateRef } from 'vue'

import InputMonitorPanel from '../../inputs/components/InputMonitorPanel.vue'
import MediaPreviewPanel from '../../media/components/MediaPreviewPanel.vue'
import MultiViewMonitorPanel from '../../monitor/components/MultiViewMonitorPanel.vue'
import ResourceBrowserPanel from '../../resources/components/ResourceBrowserPanel.vue'
import TimelineDockTab from '../../timeline/components/TimelineDockTab.vue'
import TimelineWorkspacePanel from './TimelineWorkspacePanel.vue'

import { useReplayPlayback } from '../../media/composables/useReplayPlayback'
import { useArtifactCatalog } from '../../resources/composables/useArtifactCatalog'
import { editorWorkspaceContextKey } from '../workspaceContext'

interface EditorPanelParams {
  tab: {
    showTitle: boolean
  }
}

interface EditorViewDefinition {
  component: string
  icon: string
  id: EditorViewId
  label: string
  showTitle: boolean
}

const props = defineProps<{
  canRedo: boolean
  canUndo: boolean
  episode: EpisodeDraft
  session: TimelineSession
}>()

const emit = defineEmits<{
  addReplay: [replay: RecorderMinecraftApiV1Replay]
  close: []
  cutSegment: [segmentId: string, atTick: number]
  redo: []
  reorderTrack: [sourceIndex: number, targetIndex: number]
  undo: []
  viewsChange: [views: EditorViewOption[]]
}>()

const viewDefinitions: EditorViewDefinition[] = [
  { component: 'resourceBrowser', icon: 'i-mingcute-folder-open-line', id: 'resources', label: 'Resources', showTitle: true },
  { component: 'mediaPreview', icon: 'i-mingcute-video-line', id: 'preview', label: 'Preview', showTitle: true },
  { component: 'multiViewMonitor', icon: 'i-mingcute-grid-line', id: 'monitor', label: 'Monitor', showTitle: true },
  { component: 'inputMonitor', icon: 'i-mingcute-keyboard-line', id: 'inputs', label: 'Inputs', showTitle: true },
  { component: 'timeline', icon: 'i-mingcute-timeline-line', id: 'timeline', label: 'Timeline', showTitle: false },
]

const catalog = useArtifactCatalog()
const replayPlayback = useReplayPlayback(catalog.selectedReplay)
const components: Record<string, VueComponent> = markRaw({
  inputMonitor: InputMonitorPanel as unknown as VueComponent,
  mediaPreview: MediaPreviewPanel as unknown as VueComponent,
  multiViewMonitor: MultiViewMonitorPanel as unknown as VueComponent,
  resourceBrowser: ResourceBrowserPanel as unknown as VueComponent,
  timeline: TimelineWorkspacePanel as unknown as VueComponent,
})
const tabComponents: Record<string, VueComponent> = markRaw({
  editorTab: TimelineDockTab as unknown as VueComponent,
})
let dockApi: DockviewApi | undefined
let workspaceListeners: DockviewIDisposable[] = []
let initialLayoutFrame = 0
let initialLayoutApplied = false
const workspaceElement = useTemplateRef<HTMLDivElement>('workspace')

provide(editorWorkspaceContextKey, {
  addReplay: (replay) => {
    emit('addReplay', replay)
  },
  canRedo: toRef(props, 'canRedo'),
  canUndo: toRef(props, 'canUndo'),
  catalog,
  close: () => emit('close'),
  cutSegment: (segmentId, atTick) => emit('cutSegment', segmentId, atTick),
  episode: () => props.episode,
  redo: () => emit('redo'),
  reorderTrack: (sourceIndex, targetIndex) => emit('reorderTrack', sourceIndex, targetIndex),
  replayPlayback,
  session: props.session,
  undo: () => emit('undo'),
})

function contextMenuItems(): ContextMenuItem[] {
  return ['close']
}

function panelOptions(definition: EditorViewDefinition): AddPanelOptions<EditorPanelParams> {
  const options: AddPanelOptions<EditorPanelParams> = {
    component: definition.component,
    id: definition.id,
    params: { tab: { showTitle: definition.showTitle } },
    renderer: 'always',
    tabComponent: 'editorTab',
    title: definition.label,
  }

  if (definition.id === 'resources') {
    options.minimumWidth = 160
  }
  else if (definition.id === 'preview') {
    options.minimumHeight = 40
    options.minimumWidth = 160
  }
  else if (definition.id === 'monitor') {
    options.minimumHeight = 220
    options.minimumWidth = 360
  }
  else if (definition.id === 'inputs') {
    options.minimumWidth = 160
  }
  else {
    options.minimumHeight = 100
    options.minimumWidth = 480
  }
  return options
}

function firstOpenPanel(api: DockviewApi, ids: EditorViewId[]): IDockviewPanel | undefined {
  for (const id of ids) {
    const panel = api.getPanel(id)
    if (panel)
      return panel
  }
}

function addView(viewId: EditorViewId, initialSize?: number): IDockviewPanel | undefined {
  const api = dockApi
  const definition = viewDefinitions.find(view => view.id === viewId)
  if (!api || !definition)
    return

  const existing = api.getPanel(viewId)
  if (existing)
    return existing

  const options = panelOptions(definition)
  if (viewId === 'resources') {
    options.initialWidth = initialSize
    const reference = firstOpenPanel(api, ['preview', 'monitor', 'inputs', 'timeline'])
    if (reference)
      options.position = { direction: 'left', referencePanel: reference }
  }
  else if (viewId === 'preview') {
    options.initialHeight = initialSize
    const resources = firstOpenPanel(api, ['resources'])
    const monitor = firstOpenPanel(api, ['monitor'])
    if (resources)
      options.position = { direction: 'below', referencePanel: resources }
    else if (monitor)
      options.position = { direction: 'left', referencePanel: monitor }
  }
  else if (viewId === 'monitor') {
    options.initialWidth = initialSize
    const resources = firstOpenPanel(api, ['resources'])
    const preview = firstOpenPanel(api, ['preview'])
    const inputs = firstOpenPanel(api, ['inputs'])
    const below = firstOpenPanel(api, ['timeline'])
    if (resources)
      options.position = { direction: 'right', referencePanel: resources }
    else if (preview)
      options.position = { direction: 'right', referencePanel: preview }
    else if (inputs)
      options.position = { direction: 'left', referencePanel: inputs }
    else if (below)
      options.position = { direction: 'above', referencePanel: below }
  }
  else if (viewId === 'inputs') {
    options.initialWidth = initialSize
    const reference = firstOpenPanel(api, ['monitor', 'timeline', 'preview', 'resources'])
    if (reference)
      options.position = { direction: 'right', referencePanel: reference }
  }
  else {
    options.initialHeight = initialSize
    const reference = firstOpenPanel(api, ['monitor', 'preview', 'resources', 'inputs'])
    if (reference)
      options.position = { direction: 'below', referencePanel: reference }
  }

  return api.addPanel(options)
}

function publishViews(): void {
  const api = dockApi
  emit('viewsChange', viewDefinitions.map(view => ({
    active: api?.activePanel?.id === view.id,
    icon: view.icon,
    id: view.id,
    label: view.label,
    open: Boolean(api?.getPanel(view.id)),
  })))
}

function activateView(viewId: EditorViewId): void {
  const panel = addView(viewId)
  panel?.api.setActive()
  publishViews()
}

function applyInitialLayout(api: DockviewApi, width: number, height: number): void {
  const layoutWidth = api.width || width
  const layoutHeight = api.height || height
  if (initialLayoutApplied || layoutHeight < 360)
    return

  initialLayoutApplied = true

  const sideWidth = Math.max(160, Math.round(layoutWidth * 0.125))
  const resources = addView('resources')
  // Adding the center at the remaining width first leaves the requested resource width on the
  // left. The input column is then carved out of the center without rebuilding Dockview's grid.
  addView('monitor', layoutWidth - sideWidth)
  const inputs = addView('inputs', sideWidth)
  const preview = addView('preview', Math.round(layoutHeight / 6))
  const timeline = addView('timeline', Math.round(layoutHeight * 0.3))

  resources?.group.api.setSize({ width: sideWidth })
  inputs?.group.api.setSize({ width: sideWidth })
  preview?.group.api.setSize({ height: Math.round(layoutHeight / 6) })
  timeline?.group.api.setSize({ height: Math.round(layoutHeight * 0.3) })
  inputs?.api.setActive()
  publishViews()
}

const initialLayoutObserver = useResizeObserver(workspaceElement, ([entry]) => {
  if (!entry || !dockApi || initialLayoutApplied)
    return

  cancelAnimationFrame(initialLayoutFrame)
  initialLayoutFrame = requestAnimationFrame(() => {
    if (dockApi)
      applyInitialLayout(dockApi, entry.contentRect.width, entry.contentRect.height)
  })
})

function onReady({ api }: DockviewReadyEvent): void {
  dockApi = api

  workspaceListeners = [
    api.onDidActivePanelChange(publishViews),
    api.onDidAddPanel(publishViews),
    api.onDidRemovePanel(publishViews),
  ]
  publishViews()

  // Dockview's ready event fires with bootstrap dimensions. Create the initial groups only after
  // the workspace receives its real DOM size so initial widths and heights are interpreted once.
  initialLayoutFrame = requestAnimationFrame(() => {
    const bounds = workspaceElement.value?.getBoundingClientRect()
    if (bounds)
      applyInitialLayout(api, bounds.width, bounds.height)
  })
}

onMounted(() => void catalog.load())
onBeforeUnmount(() => {
  cancelAnimationFrame(initialLayoutFrame)
  initialLayoutObserver.stop()
  workspaceListeners.forEach(listener => listener.dispose())
  workspaceListeners = []
  dockApi = undefined
})

defineExpose({ activateView })
</script>

<template>
  <!-- NOTICE: Dockview's Vue adapter teleports dynamic panels inside the caller's Vue app, which
       preserves the typed workspace provide/inject boundary. See
       `https://github.com/mathuo/dockview/blob/08097bd22495af8db171698355dffde93b9f5a88/packages/dockview-vue/src/dockview/dockview.vue#L73-L80`. -->
  <div ref="workspace" aria-label="Dockable editor views" class="h-full min-h-0 min-w-0 w-full">
    <DockviewVue
      class="editor-dockview h-full min-h-0 w-full"
      :components="components"
      default-renderer="always"
      dnd-strategy="pointer"
      :get-tab-context-menu-items="contextMenuItems"
      single-tab-mode="fullwidth"
      :tab-components="tabComponents"
      :theme="themeDark"
      @ready="onReady"
    />
  </div>
</template>

<style scoped>
.editor-dockview {
  --dv-tabs-and-actions-container-background-color: #18181b;
  --dv-tabs-and-actions-container-font-size: 10px;
  --dv-tabs-and-actions-container-height: 22px;
  --dv-activegroup-visiblepanel-tab-background-color: #18181b;
  --dv-inactivegroup-visiblepanel-tab-background-color: #18181b;
  --dv-paneview-header-border-color: var(--dashboard-border-color);
  --dv-separator-border: var(--dashboard-border-color);
  --dv-tab-divider-color: var(--dashboard-border-color);
}

.editor-dockview :deep(.dv-tab) {
  min-width: 0;
  padding: 0;
}
</style>
