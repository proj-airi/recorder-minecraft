<script setup lang="ts">
import { Pane, Splitpanes } from 'splitpanes'
import { computed } from 'vue'

import KeyboardInputView from './KeyboardInputView.vue'
import MouseInputView from './MouseInputView.vue'

import { useEditorWorkspaceContext } from '../../editor/composables/useEditorWorkspaceContext'
import { useReplayControlState } from '../composables/useReplayControlState'

const { episode, session } = useEditorWorkspaceContext()
const selectedSegment = computed(() => episode().segments.find(segment => segment.id === session.selectedSegmentId.value) ?? null)
const selectedReplay = computed(() => {
  const trackId = selectedSegment.value?.trackId
  return trackId ? episode().tracks.find(track => track.id === trackId)?.replay ?? null : null
})
const replayPlayheadTick = computed(() => Math.max(0, session.playheadTick.value - (selectedSegment.value?.startTick ?? 0)))
const controls = useReplayControlState(selectedReplay, replayPlayheadTick)
</script>

<template>
  <section class="h-full min-h-0 flex flex-col bg-neutral-950" aria-label="Input monitor">
    <div v-if="controls.isLoading.value" class="m-auto text-sm text-neutral-500">
      Loading control states…
    </div>
    <div v-else-if="controls.error.value" class="m-auto max-w-xs p-4 text-center text-sm text-red-300">
      {{ controls.error.value }}
    </div>
    <div v-else-if="!selectedReplay" class="m-auto max-w-xs p-4 text-center text-sm text-neutral-500">
      Select a replay track in the timeline to inspect its inputs.
    </div>
    <div v-else-if="!selectedReplay.eventsUrl" class="m-auto max-w-xs p-4 text-center text-sm text-neutral-500">
      The selected replay does not expose an events stream.
    </div>
    <template v-else>
      <Splitpanes horizontal class="input-monitor-split min-h-0 flex-1">
        <Pane :min-size="25" :size="50">
          <MouseInputView :sample="controls.current.value" />
        </Pane>
        <Pane :min-size="25" :size="50">
          <KeyboardInputView :sample="controls.current.value" />
        </Pane>
      </Splitpanes>
    </template>
  </section>
</template>

<style scoped>
/* NOTICE: Splitpanes supplies pane sizing but leaves splitter visuals to consumers. This is the
   only internal CSS override; the upstream splitter structure is at
   `https://github.com/antoniandre/splitpanes/blob/c13526b5d751ad188e19c6b6797466a7559a88d4/src/components/splitpanes/splitpanes.vue#L752-L774`. */
.input-monitor-split :deep(> .splitpanes__splitter) {
  background: #3f3f46;
  flex: 0 0 1px;
  position: relative;
}

.input-monitor-split :deep(> .splitpanes__splitter::before) {
  content: '';
  inset: -4px 0;
  position: absolute;
}
</style>
