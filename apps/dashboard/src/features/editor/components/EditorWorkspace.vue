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

type DockviewLayoutNode = ReturnType<DockviewApi['toJSON']>['grid']['root']

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
  { component: 'timeline', icon: 'i-mingcute-timeline-line', id: 'timeline', label: 'Timeline', showTitle: false },
]

const catalog = useArtifactCatalog()
const replayPlayback = useReplayPlayback(catalog.selectedReplay)
const components: Record<string, VueComponent> = markRaw({
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
    options.minimumHeight = 72
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
    return
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

function containsView(node: DockviewLayoutNode, viewId: EditorViewId): boolean {
  if (!Array.isArray(node.data))
    return node.data.views.includes(viewId)
  return node.data.some(child => containsView(child, viewId))
}

function setSiblingRatio(node: DockviewLayoutNode, firstId: EditorViewId, secondId: EditorViewId, firstRatio: number, availableSize: number): boolean {
  if (!Array.isArray(node.data))
    return false

  const first = node.data.find(child => containsView(child, firstId))
  const second = node.data.find(child => containsView(child, secondId))
  if (first && second && first !== second) {
    first.size = Math.round(availableSize * firstRatio)
    second.size = availableSize - first.size
    return true
  }

  return node.data.some(child => setSiblingRatio(child, firstId, secondId, firstRatio, availableSize))
}

function applyInitialLayout(api: DockviewApi, width: number, height: number): void {
  if (initialLayoutApplied || height < 360)
    return

  initialLayoutApplied = true
  const layout = api.toJSON()
  const editorWidth = workspaceElement.value?.parentElement?.getBoundingClientRect().width ?? width
  const resourceWidth = Math.min(width, Math.max(160, Math.round(editorWidth * 0.125)))
  layout.grid.width = width
  layout.grid.height = height
  setSiblingRatio(layout.grid.root, 'resources', 'monitor', resourceWidth / width, width)
  setSiblingRatio(layout.grid.root, 'resources', 'preview', 5 / 6, height)
  setSiblingRatio(layout.grid.root, 'monitor', 'timeline', 0.7, height)
  api.fromJSON(layout)
}

const initialLayoutObserver = useResizeObserver(workspaceElement, ([entry]) => {
  if (entry && dockApi)
    applyInitialLayout(dockApi, entry.contentRect.width, entry.contentRect.height)
})

function onReady({ api }: DockviewReadyEvent): void {
  dockApi = api
  addView('resources', 160)
  addView('monitor')
  addView('preview', 72)
  addView('timeline', 100)

  workspaceListeners = [
    api.onDidActivePanelChange(publishViews),
    api.onDidAddPanel(publishViews),
    api.onDidRemovePanel(publishViews),
  ]
  publishViews()

  // Dockview's ready event fires with bootstrap dimensions. Correct the serialized branch sizes
  // after the workspace receives its real DOM size, then keep later user resizing untouched.
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
  <div class="editor-workspace-grid grid h-full min-h-0 w-full">
    <div ref="workspace" aria-label="Dockable editor views" class="h-full min-h-0 min-w-0">
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
    <aside class="h-full min-h-0 flex flex-col border-l border-[var(--dashboard-border-color)] bg-neutral-950" aria-label="Inputs pane">
      <div class="h-[22px] flex shrink-0 items-center border-b border-[var(--dashboard-border-color)] bg-neutral-900 px-2 text-[10px] text-neutral-300">
        Inputs
      </div>
      <InputMonitorPanel class="min-h-0 flex-1" />
    </aside>
  </div>
</template>

<style scoped>
.editor-workspace-grid {
  display: grid;
  grid-template-columns: minmax(0, 7fr) minmax(160px, 1fr);
}

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
