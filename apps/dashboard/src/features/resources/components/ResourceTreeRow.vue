<script setup lang="ts">
import type { RecorderMinecraftApiV1Replay } from '@proj-airi/recorder-minecraft-api'

import type { PlayerIdentity } from '../../players/avatar'
import type { ResourceTreeNode } from '../resourceTree'

import { computed } from 'vue'

import PlayerAvatar from '../../players/components/PlayerAvatar.vue'
import PlayerAvatarGroup from '../../players/components/PlayerAvatarGroup.vue'

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
  /** Pointer entered (element) or left (null) a Play row, for the hover preview. */
  preview: [anchor: HTMLElement | null]
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
const participants = computed((): PlayerIdentity[] => {
  const people = props.node.kind === 'session'
    ? [
        ...(props.node.session.plays ?? []),
        ...(props.node.session.alignments ?? []).flatMap(alignment => alignment.participants ?? []),
      ]
    : props.node.kind === 'capture-session' ? props.node.plays : []
  const byKey = new Map<string, PlayerIdentity>()
  for (const person of people) {
    const key = person.playerUuid ?? person.playerName
    if (key && !byKey.has(key))
      byKey.set(key, { name: person.playerName, uuid: person.playerUuid })
  }
  return [...byKey.values()]
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
// Movement summary is secondary: it stays in the tooltip and the details pane, not on the row.
const playTitle = computed(() => {
  const value = replay.value
  if (!value)
    return ''
  const summary = value.summary
  return [
    value.playerName ?? 'Unknown player',
    formatInstantShort(value.startedAt),
    playDuration.value,
    summary ? `${(summary.observedPathDistanceBlocks ?? 0).toFixed(1)} blocks moved` : null,
    summary ? `${(summary.idlePercentage ?? 0).toFixed(0)}% idle` : null,
    value.video?.url ? null : 'no rendered video',
  ].filter(Boolean).join(' · ')
})
const sessionTitle = computed(() => {
  const value = session.value
  if (!value)
    return ''
  return [
    `World session ${value.id}`,
    sessionDuration.value,
    `${participants.value.length} ${participants.value.length === 1 ? 'player' : 'players'}`,
    value.terminalReason ? `ended by ${value.terminalReason.replaceAll('_', ' ')}` : null,
    value.knownGaps?.length ? `known gaps: ${value.knownGaps.join(', ')}` : null,
  ].filter(Boolean).join(' · ')
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
    @mouseenter="node.kind === 'play' && emit('preview', $event.currentTarget as HTMLElement)"
    @mouseleave="node.kind === 'play' && emit('preview', null)"
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

    <!-- Server: the name is enough; counts live in the tooltip and the details pane. -->
    <template v-if="node.kind === 'server'">
      <span aria-hidden="true" class="i-mingcute-server-line shrink-0 text-sm text-neutral-500" />
      <span
        class="min-w-0 flex-1 truncate text-xs text-neutral-200 font-medium"
        :title="`${label} · ${node.sessionCount} world sessions · ${node.playCount} Plays · ${node.server.instanceId}`"
      >{{ label }}</span>
    </template>

    <!-- World session: when, who, and the one finding worth seeing before opening it. -->
    <template v-else-if="node.kind === 'session'">
      <span class="min-w-0 flex flex-1 flex-col gap-0.5">
        <span class="truncate text-xs text-neutral-100" :title="sessionTitle">{{ label }}</span>
        <span class="min-w-0 flex items-center gap-1.5 text-[11px] text-neutral-500">
          <PlayerAvatarGroup v-if="participants.length" :players="participants" :size="16" />
          <span class="shrink-0 tabular-nums">{{ sessionDuration }}</span>
          <span
            v-if="alignment && (alignment.divergences > 0 || alignment.invalid > 0)"
            class="min-w-0 truncate"
            :class="alignment.invalid ? 'text-red-300' : 'text-amber-200'"
            :title="alignment.title"
          >
            · {{ alignment.invalid ? 'alignment unreadable' : `${alignment.divergences} ${alignment.divergences === 1 ? 'divergence' : 'divergences'}` }}
          </span>
        </span>
      </span>
      <span v-if="node.session.validationError" class="i-mingcute-warning-line shrink-0 text-xs text-red-300" aria-label="Validation error" role="img" :title="`Validation error: ${node.session.validationError}`" />
      <span v-else-if="node.session.streamFailure" class="i-mingcute-alert-line shrink-0 text-xs text-amber-300" aria-label="World stream failure" role="img" :title="`World stream failure: ${node.session.streamFailure}`" />
    </template>

    <!-- Capture session without a world record -->
    <template v-else-if="node.kind === 'capture-session'">
      <span class="min-w-0 flex flex-1 flex-col gap-0.5">
        <span class="truncate text-xs text-neutral-200" :title="`Capture session ${node.sessionId} (no world session record) · ${node.plays.length} Plays`">{{ formatInstantShort(node.plays[0]?.startedAt) }}</span>
        <span class="min-w-0 flex items-center gap-1.5 text-[11px] text-neutral-500">
          <PlayerAvatarGroup v-if="participants.length" :players="participants" :size="16" />
          <span class="truncate">no world record</span>
        </span>
      </span>
    </template>

    <!-- Unlinked group -->
    <template v-else-if="node.kind === 'unlinked'">
      <span class="min-w-0 flex-1 truncate text-xs text-neutral-500" :title="`${label} · ${node.playCount} Plays`">{{ label }}</span>
    </template>

    <!-- Play: who and how long. A problem replaces the duration so it cannot be missed. -->
    <template v-else-if="node.kind === 'play'">
      <PlayerAvatar :player="{ name: node.replay.playerName, uuid: node.replay.playerUuid }" :size="node.underSession ? 18 : 22" />
      <span class="min-w-0 flex flex-1 flex-col gap-0.5" :title="playTitle">
        <span class="truncate text-xs text-neutral-100">{{ label }}</span>
        <span v-if="node.replay.validationError" class="truncate text-[11px] text-red-300" :title="node.replay.validationError">
          <span aria-hidden="true" class="i-mingcute-warning-line mr-0.5 align-[-1px]" />{{ node.replay.validationError }}
        </span>
        <span v-else class="truncate text-[11px] text-neutral-500 tabular-nums">
          <template v-if="!node.underSession">{{ formatInstantShort(node.replay.startedAt) }} · </template>{{ playDuration ?? '—' }}<template v-if="!node.replay.video?.url"> · no video</template>
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
