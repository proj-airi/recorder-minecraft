<script setup lang="ts">
import type { RecorderMinecraftApiV1Replay } from '@proj-airi/recorder-minecraft-api'

defineProps<{
  active: boolean
  replay: RecorderMinecraftApiV1Replay
}>()

const emit = defineEmits<{
  select: []
}>()

function formatInstant(value?: string): string {
  if (!value)
    return 'Unknown time'
  return `${new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'medium', timeZone: 'UTC' }).format(new Date(value))} UTC`
}
</script>

<template>
  <button
    :aria-pressed="active"
    class="h-17 w-full flex items-center gap-3 border-0 border-b border-white/6 bg-transparent px-3 text-left hover:bg-white/5"
    :class="active ? 'bg-amber-400/10 text-amber-100' : 'text-neutral-200'"
    :title="`Preview replay from ${replay.playerName ?? 'unknown player'}`"
    type="button"
    @click="emit('select')"
  >
    <span aria-hidden="true" class="i-mingcute-video-line shrink-0 text-lg" />
    <span class="min-w-0 flex-1">
      <span class="block truncate text-xs font-medium">{{ replay.playerName }} · {{ replay.serverName }}</span>
      <span class="mt-1 block truncate text-[10px] text-neutral-500">{{ formatInstant(replay.startedAt) }}</span>
    </span>
    <span v-if="replay.validationError" class="i-mingcute-warning-line shrink-0 text-lg text-red-300" aria-hidden="true" />
  </button>
</template>
