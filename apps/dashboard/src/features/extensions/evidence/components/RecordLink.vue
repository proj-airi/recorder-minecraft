<script setup lang="ts">
import type { RecordRef } from '../formats'

import { computed } from 'vue'

import { recordRefLabel } from '../labels'

const props = defineProps<{
  /** Actor name of the record, shown before the stream. */
  actor?: string
  record: RecordRef
}>()

const emit = defineEmits<{
  seek: [record: RecordRef]
}>()

const text = computed(() => `${props.actor ? `${props.actor} · ` : ''}${recordRefLabel(props.record)}`)
</script>

<template>
  <button
    type="button"
    class="inline-flex items-center gap-1 border border-white/10 rounded bg-white/3 px-1.5 py-0.5 text-left text-[11px] text-sky-200 font-mono hover:border-sky-300/40 hover:bg-sky-400/10"
    :title="`Seek the playhead to tick ${record.serverTick}`"
    @click="emit('seek', record)"
  >
    <span aria-hidden="true" class="i-mingcute-link-line shrink-0 text-[10px]" />
    <span>{{ text }}</span>
  </button>
</template>
