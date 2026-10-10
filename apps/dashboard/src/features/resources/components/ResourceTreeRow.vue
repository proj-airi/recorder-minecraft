<script setup lang="ts">
import type { RecorderMinecraftApiV1Replay } from '@proj-airi/recorder-minecraft-api'

import type { ResourceTreeNode } from '../resourceTree'

import { computed } from 'vue'

import { formatDurationSeconds, formatDurationTicks, formatInstantShort, parseTick } from '../../basic/format'
import { writeDraggedReplayId } from '../replayDrag'
import { isReplayAddable } from '../worldSession'

const props = defineProps<{
  active: boolean
  node: ResourceTreeNode
  /** Connection ids of the Plays on the timeline. */
  onTimeline: ReadonlySet<string>
  setSize: number
  setPosition: number
}>()

const emit = defineEmits<{
  add: []
  select: []
  toggle: []
}>()

const expandable = computed(() => props.node.kind !== 'play')
const addLabel = computed(() => {
  switch (props.node.kind) {
    case 'session':
    case 'capture-session':
      return 'Add session to timeline'
    case 'play':
      return 'Add Play to timeline'
    default:
      return null
  }
})
const playsOfNode = computed((): RecorderMinecraftApiV1Replay[] => {
  switch (props.node.kind) {
    case 'session':
      return props.node.sessionPlays
    case 'capture-session':
      return props.node.plays
    case 'play':
      return [props.node.replay]
    default:
      return []
  }
})
const addable = computed(() => playsOfNode.value.some(isReplayAddable))
const timelineCount = computed(() => playsOfNode.value.filter(replay => replay.connectionId && props.onTimeline.has(replay.connectionId)).length)
const timelineState = computed(() => {
  const total = playsOfNode.value.length
  if (timelineCount.value === 0 || total === 0)
    return null
  return timelineCount.value >= total ? 'all' : 'some'
})
const timelineTitle = computed(() => timelineState.value === 'all'
  ? 'On the timeline'
  : `${timelineCount.value} of ${playsOfNode.value.length} Plays on the timeline`)

const session = computed(() => props.node.kind === 'session' ? props.node.session : null)
const sessionDuration = computed(() => {
  const value = session.value
  if (!value)
    return null
  const start = parseTick(value.startServerTick)
  const end = parseTick(value.endServerTick)
  if (start !== undefined && end !== undefined && end >= start)
    return formatDurationTicks(end - start)
  const startedAt = value.startedAt ? Date.parse(value.startedAt) : Number.NaN
  const endedAt = value.endedAt ? Date.parse(value.endedAt) : Number.NaN
  return Number.isFinite(startedAt) && Number.isFinite(endedAt) ? formatDurationSeconds((endedAt - startedAt) / 1000) : 'open'
})
const participantCount = computed(() => {
  if (!session.value)
    return 0
  const players = new Set([
    ...(session.value.plays ?? []).map(play => play.playerUuid ?? play.playerName),
    ...(session.value.alignments ?? []).flatMap(alignment => (alignment.participants ?? []).map(participant => participant.playerUuid ?? participant.playerName)),
  ].filter(Boolean))
  return players.size
})
const alignment = computed(() => {
  const alignments = session.value?.alignments ?? []
  if (alignments.length === 0)
    return null
  const invalid = alignments.filter(item => item.validationError).length
  const divergences = alignments.reduce((total, item) => total + Number(item.divergenceCount ?? 0), 0)
  const events = alignments.reduce((total, item) => total + Number(item.eventCount ?? 0), 0)
  return {
    divergences,
    invalid,
    title: `Session alignment: ${events} events, ${divergences} observation ${divergences === 1 ? 'divergence' : 'divergences'}${invalid ? `, ${invalid} unreadable` : ''}`,
  }
})

const replay = computed(() => props.node.kind === 'play' ? props.node.replay : null)
const playDuration = computed(() => {
  const value = replay.value
  if (!value)
    return null
  const fromSummary = Number(value.summary?.durationTicks)
  if (Number.isFinite(fromSummary) && fromSummary > 0)
    return formatDurationTicks(fromSummary)
  const start = parseTick(value.startServerTick)
  const end = parseTick(value.endServerTick)
  return start !== undefined && end !== undefined && end >= start ? formatDurationTicks(end - start) : null
})
const playSummary = computed(() => {
  const summary = replay.value?.summary
  if (!summary)
    return null
  return `${(summary.observedPathDistanceBlocks ?? 0).toFixed(1)} blocks · ${(summary.idlePercentage ?? 0).toFixed(0)}% idle`
})
const draggable = computed(() => Boolean(replay.value && isReplayAddable(replay.value)))

const label = computed(() => {
  switch (props.node.kind) {
    case 'server':
      return props.node.server.name ?? 'Unnamed server'
    case 'session':
      return formatInstantShort(props.node.session.startedAt)
    case 'capture-session':
      return `Capture session ${props.node.sessionId.slice(0, 8)}`
    case 'unlinked':
      return 'Plays without a world session'
    case 'play':
      return props.node.replay.playerName ?? 'Unknown player'
    default:
      return ''
  }
})

function onDragStart(event: DragEvent): void {
  const value = replay.value
  if (!event.dataTransfer || !value?.connectionId || !draggable.value) {
    event.preventDefault()
    return
  }
  writeDraggedReplayId(event.dataTransfer, value.connectionId)
}
</script>

<template>
  <div
    :id="`resource-node-${node.id}`"
    :aria-expanded="expandable ? node.expanded : undefined"
    :aria-label="node.kind === 'play' ? `${label}, Play${replay?.video?.url ? ' with video' : ''}` : label"
    :aria-level="node.depth + 1"
    :aria-posinset="setPosition"
    :aria-selected="active"
    :aria-setsize="setSize"
    class="group relative flex cursor-default select-none items-center gap-1 border-l-2 py-1 pr-1"
    :class="[
      active ? 'border-amber-300 bg-amber-300/10' : 'border-transparent hover:bg-white/4',
      node.kind === 'server' && 'bg-white/3',
    ]"
    :draggable="draggable"
    role="treeitem"
    :style="{ paddingLeft: `${4 + node.depth * 12}px` }"
    @click="emit('select')"
    @dblclick="expandable ? emit('toggle') : emit('add')"
    @dragstart="onDragStart"
  >
    <button
      v-if="expandable"
      :aria-label="node.expanded ? 'Collapse' : 'Expand'"
      :title="node.expanded ? 'Collapse' : 'Expand'"
      class="h-4 w-4 flex shrink-0 items-center justify-center border-0 rounded bg-transparent p-0 text-neutral-500 hover:text-neutral-200"
      tabindex="-1"
      type="button"
      @click.stop="emit('toggle')"
    >
      <span aria-hidden="true" class="i-mingcute-right-line text-xs transition-transform" :class="node.expanded && 'rotate-90'" />
    </button>
    <span v-else class="w-4 shrink-0" aria-hidden="true" />

    <!-- Server -->
    <template v-if="node.kind === 'server'">
      <span aria-hidden="true" class="i-mingcute-server-line shrink-0 text-sm text-neutral-400" />
      <span class="min-w-0 flex-1 truncate text-xs text-neutral-200 font-medium" :title="`${label} · ${node.server.instanceId}`">{{ label }}</span>
      <span class="shrink-0 text-[10px] text-neutral-500 tabular-nums" :title="`${node.sessionCount} world sessions, ${node.playCount} Plays`">
        {{ node.sessionCount }}<span class="i-mingcute-earth-2-line mx-0.5 inline-block align-[-1px] text-[9px]" aria-hidden="true" />{{ node.playCount }}<span class="i-mingcute-user-3-line ml-0.5 inline-block align-[-1px] text-[9px]" aria-hidden="true" />
      </span>
    </template>

    <!-- World session -->
    <template v-else-if="node.kind === 'session'">
      <span aria-hidden="true" class="i-mingcute-earth-2-line shrink-0 text-sm" :class="node.session.validationError ? 'text-red-300' : 'text-sky-300'" />
      <span class="min-w-0 flex flex-1 flex-col">
        <span class="truncate text-xs text-neutral-100" :title="`World session ${node.session.id}`">{{ label }}</span>
        <span class="min-w-0 flex items-center gap-1 text-[10px] text-neutral-500">
          <span class="shrink-0 tabular-nums">{{ sessionDuration }}</span>
          <span aria-hidden="true">·</span>
          <span class="shrink-0" :title="`${participantCount} participants`">{{ participantCount }}<span class="i-mingcute-user-3-line ml-0.5 inline-block align-[-1px] text-[9px]" aria-label="participants" /></span>
          <span
            v-if="alignment"
            class="shrink-0 rounded px-1 text-[9px] leading-tight"
            :class="alignment.invalid ? 'bg-red-400/12 text-red-200' : alignment.divergences ? 'bg-amber-400/15 text-amber-200' : 'bg-emerald-400/10 text-emerald-200'"
            :title="alignment.title"
          >
            <span class="i-mingcute-git-compare-line mr-0.5 align-[-1px]" aria-hidden="true" />{{ alignment.divergences }}<span class="sr-only"> observation divergences</span>
          </span>
          <span v-if="node.session.terminalReason" class="min-w-0 truncate" :title="`Terminal reason: ${node.session.terminalReason}`">{{ node.session.terminalReason.replaceAll('_', ' ') }}</span>
        </span>
      </span>
      <span v-if="node.session.knownGaps?.length" class="i-mingcute-information-line shrink-0 text-xs text-sky-300/80" :aria-label="`${node.session.knownGaps.length} known gaps`" role="img" :title="`Known gaps: ${node.session.knownGaps.join(', ')}`" />
      <span v-if="node.session.streamFailure" class="i-mingcute-alert-line shrink-0 text-xs text-amber-300" aria-label="World stream failure" role="img" :title="`World stream failure: ${node.session.streamFailure}`" />
      <span v-if="node.session.validationError" class="i-mingcute-warning-line shrink-0 text-xs text-red-300" aria-label="Validation error" role="img" :title="`Validation error: ${node.session.validationError}`" />
    </template>

    <!-- Capture session without a world record -->
    <template v-else-if="node.kind === 'capture-session'">
      <span aria-hidden="true" class="i-mingcute-group-line shrink-0 text-sm text-neutral-400" />
      <span class="min-w-0 flex flex-1 flex-col">
        <span class="truncate text-xs text-neutral-200" :title="`Capture session ${node.sessionId} (no world session record)`">{{ label }}</span>
        <span class="truncate text-[10px] text-neutral-500">{{ node.plays.length }} Plays · {{ formatInstantShort(node.plays[0]?.startedAt) }}</span>
      </span>
    </template>

    <!-- Unlinked group -->
    <template v-else-if="node.kind === 'unlinked'">
      <span aria-hidden="true" class="i-mingcute-folder-line shrink-0 text-sm text-neutral-500" />
      <span class="min-w-0 flex-1 truncate text-xs text-neutral-400" :title="label">{{ label }}</span>
      <span class="shrink-0 text-[10px] text-neutral-500 tabular-nums">{{ node.playCount }}</span>
    </template>

    <!-- Play -->
    <template v-else-if="node.kind === 'play'">
      <span
        v-if="node.replay.validationError"
        class="i-mingcute-warning-line shrink-0 text-sm text-red-300"
        aria-hidden="true"
      />
      <span
        v-else-if="node.replay.video?.url"
        class="i-mingcute-video-line shrink-0 text-sm text-violet-300"
        aria-hidden="true"
        title="Rendered video available"
      />
      <span
        v-else
        class="i-mingcute-user-3-line shrink-0 text-sm text-neutral-400"
        aria-hidden="true"
        title="Capture data only (no rendered video)"
      />
      <span class="min-w-0 flex flex-1 flex-col">
        <span class="truncate text-xs text-neutral-100" :title="`${label} · ${node.replay.connectionId}`">{{ label }}</span>
        <span class="truncate text-[10px] text-neutral-500" :title="node.replay.validationError ?? undefined">
          <template v-if="node.replay.validationError">{{ node.replay.validationError }}</template>
          <template v-else>
            <template v-if="!node.underSession">{{ formatInstantShort(node.replay.startedAt) }} · </template>{{ playDuration ?? '—' }}<template v-if="playSummary"> · {{ playSummary }}</template>
          </template>
        </span>
      </span>
    </template>

    <span
      v-if="timelineState"
      class="shrink-0 text-xs"
      :class="timelineState === 'all' ? 'i-mingcute-check-circle-fill text-emerald-400' : 'i-mingcute-check-circle-line text-emerald-400/70'"
      :aria-label="timelineTitle"
      role="img"
      :title="timelineTitle"
    />
    <button
      v-if="addLabel && addable"
      :aria-label="addLabel"
      class="h-5 w-5 flex shrink-0 items-center justify-center border-0 rounded bg-transparent p-0 text-neutral-500 opacity-0 transition-opacity hover:bg-white/10 hover:text-white focus-visible:opacity-100 group-hover:opacity-100"
      :class="active && 'opacity-100'"
      tabindex="-1"
      :title="`${addLabel} (Enter)`"
      type="button"
      @click.stop="emit('add')"
    >
      <span aria-hidden="true" class="i-mingcute-add-line text-sm" />
    </button>
  </div>
</template>
