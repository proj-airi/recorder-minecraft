<script setup lang="ts">
import type { RecorderMinecraftApiV1Replay } from '@proj-airi/recorder-minecraft-api'

import { computed } from 'vue'

import { extensionLabel, playExtensionModules } from '../../extensions/registry'
import { writeDraggedReplayId } from '../replayDrag'

const props = defineProps<{
  active: boolean
  replay: RecorderMinecraftApiV1Replay
}>()

const emit = defineEmits<{
  select: []
}>()

const summaryText = computed(() => formatSummary(props.replay))
const extensions = computed(() => (props.replay.extensions ?? []).map(extension => ({
  label: extensionLabel(extension.extensionType ?? 'unknown'),
  supported: playExtensionModules.some(module => module.extensionType === extension.extensionType),
  type: extension.extensionType ?? 'unknown',
})))

function formatInstant(value?: string): string {
  if (!value)
    return 'Unknown time'
  return `${new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'medium', timeZone: 'UTC' }).format(new Date(value))} UTC`
}

function formatSummary(replay: RecorderMinecraftApiV1Replay): null | string {
  if (!replay.summary)
    return null
  const ticks = BigInt(replay.summary.durationTicks ?? '0')
  const seconds = ticks / 20n
  const duration = `${(seconds / 60n).toString().padStart(2, '0')}:${(seconds % 60n).toString().padStart(2, '0')}`
  const distance = (replay.summary.observedPathDistanceBlocks ?? 0).toFixed(1)
  const idle = (replay.summary.idlePercentage ?? 0).toFixed(1)
  return `${duration} · ${distance} blocks · ${idle}% idle`
}

function onDragStart(event: DragEvent): void {
  if (!event.dataTransfer || !props.replay.connectionId || (!props.replay.video?.url && !props.replay.eventsUrl) || props.replay.validationError) {
    event.preventDefault()
    return
  }
  writeDraggedReplayId(event.dataTransfer, props.replay.connectionId)
}
</script>

<template>
  <button
    :aria-pressed="active"
    class="min-h-20 w-full flex items-center gap-3 border-0 border-b border-[var(--dashboard-border-color)] bg-transparent px-3 py-3 text-left hover:bg-white/5"
    :class="active ? 'bg-amber-400/10 text-amber-100' : 'text-neutral-200'"
    :draggable="Boolean(replay.connectionId && (replay.video?.url || replay.eventsUrl) && !replay.validationError)"
    :title="`Preview replay from ${replay.playerName ?? 'unknown player'}`"
    type="button"
    @dragstart="onDragStart"
    @click="emit('select')"
  >
    <span aria-hidden="true" class="i-mingcute-video-line shrink-0 text-sm text-neutral-600" />
    <span class="min-w-0 flex-1">
      <span class="block truncate text-xs font-medium">{{ replay.playerName }} · {{ replay.serverName }}</span>
      <span class="mt-1 block truncate text-[10px] text-neutral-500">{{ formatInstant(replay.startedAt) }}</span>
      <span v-if="summaryText" class="mt-1 block truncate text-[10px] text-neutral-400">{{ summaryText }}</span>
      <span v-if="extensions.length" class="mt-1 flex flex-wrap gap-1">
        <span
          v-for="extension in extensions"
          :key="extension.type"
          class="rounded px-1.5 py-0.5 text-[9px]"
          :class="extension.supported ? 'bg-violet-400/10 text-violet-200' : 'bg-white/5 text-neutral-500'"
          :title="extension.supported ? `Supported Play extension: ${extension.type}` : `Unsupported Play extension: ${extension.type}`"
        >
          {{ extension.label }}
        </span>
      </span>
    </span>
    <span v-if="replay.validationError" class="i-mingcute-warning-line shrink-0 text-lg text-red-300" aria-hidden="true" />
    <span v-else-if="replay.video?.url || replay.eventsUrl" aria-hidden="true" class="i-mingcute-dots-line shrink-0 cursor-grab text-base text-neutral-600" />
  </button>
</template>
