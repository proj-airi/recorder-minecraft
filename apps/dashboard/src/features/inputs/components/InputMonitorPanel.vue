<script setup lang="ts">
import { Pane, Splitpanes } from 'splitpanes'

import KeyboardInputView from './KeyboardInputView.vue'
import MouseInputView from './MouseInputView.vue'

import { useEditorWorkspaceContext } from '../../editor/composables/useEditorWorkspaceContext'
import { useReplayControlState } from '../composables/useReplayControlState'

const { catalog, session } = useEditorWorkspaceContext()
const controls = useReplayControlState(catalog.selectedReplay, session.playheadTick)
</script>

<template>
  <section class="h-full min-h-0 flex flex-col bg-neutral-950" aria-label="Input monitor">
    <div v-if="controls.isLoading.value" class="m-auto text-sm text-neutral-500">
      Loading control states…
    </div>
    <div v-else-if="controls.error.value" class="m-auto max-w-xs p-4 text-center text-sm text-red-300">
      {{ controls.error.value }}
    </div>
    <div v-else-if="!catalog.selectedReplay.value?.eventsUrl" class="m-auto max-w-xs p-4 text-center text-sm text-neutral-500">
      This replay does not expose an events stream.
    </div>
    <template v-else>
      <p class="m-0 border-b border-white/8 px-3 py-2 text-xs text-neutral-500 leading-5">
        Reconstructed controls · {{ controls.sampleCount.value.toLocaleString() }} samples<br>
        Not raw keyboard or mouse telemetry.
      </p>
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
