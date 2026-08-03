<script setup lang="ts">
import TimelineEditor from '../../timeline/components/TimelineEditor.vue'

import { useEditorWorkspaceContext } from '../composables/useEditorWorkspaceContext'

const context = useEditorWorkspaceContext()
</script>

<template>
  <TimelineEditor
    :can-redo="context.canRedo.value"
    :can-undo="context.canUndo.value"
    :episode="context.episode()"
    :session="context.session"
    @close="context.close"
    @cut-segment="context.cutSegment"
    @redo="context.redo"
    @reorder-track="context.reorderTrack"
    @undo="context.undo"
  />
</template>
