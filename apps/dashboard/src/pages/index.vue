<script setup lang="ts">
import { storeToRefs } from 'pinia'
import { shallowRef } from 'vue'

import Button from '../features/basic/components/Button.vue'
import EditorWorkspace from '../features/editor/components/EditorWorkspace.vue'

import { useEditorKeyboardControls } from '../features/editor/composables/useEditorKeyboardControls'
import { useTimelineSession } from '../features/timeline/composables/useTimelineSession'
import { useEpisodeStore } from '../features/timeline/stores/episode'

const episodeStore = useEpisodeStore()
const { episode } = storeToRefs(episodeStore)
const session = useTimelineSession(episode, episodeStore.commitSegmentEdit)
const editorOpen = shallowRef(true)

function deleteSelectedSegment(): void {
  const selectedId = session.selectedSegmentId.value
  if (selectedId && episodeStore.deleteSegment(selectedId))
    session.selectSegment(null)
}

useEditorKeyboardControls({
  deleteSelected: deleteSelectedSegment,
  redo: episodeStore.redo,
  session,
  undo: episodeStore.undo,
})
</script>

<template>
  <main class="h-full max-h-100dvh flex flex-col bg-neutral-900 text-neutral-50">
    <nav aria-label="Global navigation" class="h-12 flex shrink-0 items-center justify-between border-b border-white/8 px-4">
      <span class="text-sm text-neutral-300 font-medium tracking-wide">Recorder</span>
      <Button
        :active="editorOpen"
        :label="editorOpen ? 'Collapse timeline editor' : 'Open timeline editor'"
        :title="editorOpen ? 'Collapse timeline editor' : 'Open timeline editor'"
        @click="editorOpen = !editorOpen"
      >
        <span
          aria-hidden="true"
          :class="editorOpen ? 'i-mingcute-layout-bottom-open-fill' : 'i-mingcute-layout-bottom-open-line'"
          class="text-base"
        />
      </Button>
    </nav>

    <div v-if="editorOpen" class="min-h-0 flex-1 overflow-hidden">
      <EditorWorkspace
        :can-redo="episodeStore.canRedo"
        :can-undo="episodeStore.canUndo"
        :episode="episode"
        :session="session"
        @close="editorOpen = false"
        @cut-segment="episodeStore.cutSegment"
        @redo="episodeStore.redo"
        @reorder-track="episodeStore.reorderTrack"
        @undo="episodeStore.undo"
      />
    </div>
  </main>
</template>
