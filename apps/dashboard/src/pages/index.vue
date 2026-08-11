<script setup lang="ts">
import type { EditorViewId, EditorViewOption } from '../features/editor/views'

import { storeToRefs } from 'pinia'
import { nextTick, shallowRef, useTemplateRef } from 'vue'

import EditorViewSelector from '../features/editor/components/EditorViewSelector.vue'
import EditorWorkspace from '../features/editor/components/EditorWorkspace.vue'

import { useEditorKeyboardControls } from '../features/editor/composables/useEditorKeyboardControls'
import { useTimelineSession } from '../features/timeline/composables/useTimelineSession'
import { useEpisodeStore } from '../features/timeline/stores/episode'

const episodeStore = useEpisodeStore()
const { episode } = storeToRefs(episodeStore)
const session = useTimelineSession(episode, episodeStore.commitSegmentEdit)
const editorOpen = shallowRef(true)
const editorViews = shallowRef<EditorViewOption[]>([])
const editorWorkspace = useTemplateRef<InstanceType<typeof EditorWorkspace>>('editorWorkspace')

function deleteSelectedSegment(): void {
  const selectedId = session.selectedSegmentId.value
  if (selectedId && episodeStore.deleteSegment(selectedId))
    session.selectSegment(null)
}

async function activateEditorView(viewId: EditorViewId): Promise<void> {
  if (!editorOpen.value) {
    editorOpen.value = true
    await nextTick()
  }
  editorWorkspace.value?.activateView(viewId)
}

useEditorKeyboardControls({
  deleteSelected: deleteSelectedSegment,
  editable: () => session.editable,
  redo: episodeStore.redo,
  session,
  undo: episodeStore.undo,
})
</script>

<template>
  <main class="h-full max-h-100dvh flex flex-col bg-neutral-900 text-neutral-50">
    <nav aria-label="Global navigation" class="h-12 flex shrink-0 items-center justify-between border-b border-[var(--dashboard-border-color)] px-4">
      <span class="text-sm text-neutral-300 font-medium tracking-wide">Recorder</span>
      <div class="flex items-center gap-2">
        <EditorViewSelector
          v-if="editorOpen && editorViews.length"
          :views="editorViews"
          @select-view="activateEditorView"
        />
      </div>
    </nav>

    <div v-if="editorOpen" class="min-h-0 flex-1 overflow-hidden">
      <EditorWorkspace
        ref="editorWorkspace"
        :can-redo="episodeStore.canRedo"
        :can-undo="episodeStore.canUndo"
        :episode="episode"
        :session="session"
        @add-replay="episodeStore.addReplay"
        @close="editorOpen = false"
        @cut-segment="episodeStore.cutSegment"
        @redo="episodeStore.redo"
        @reorder-track="episodeStore.reorderTrack"
        @undo="episodeStore.undo"
        @views-change="editorViews = $event"
      />
    </div>
  </main>
</template>
