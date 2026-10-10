<script setup lang="ts">
import type { RecorderMinecraftApiV1Replay, RecorderMinecraftApiV1WorldSession } from '@proj-airi/recorder-minecraft-api'

import type { ResourceTreeNode } from '../resourceTree'

import { computed } from 'vue'

import CopyField from '../../basic/components/CopyField.vue'

import { formatDurationTicks, formatInstantFull, parseTick } from '../../basic/format'
import { extensionLabel, playExtensionModules } from '../../extensions/registry'
import { playNodeId, sessionNodeId } from '../resourceTree'
import { isReplayAddable, worldLinkDescription, worldLinkShort, worldSessionForReplay } from '../worldSession'

const props = defineProps<{
  node: ResourceTreeNode
  onTimeline: ReadonlySet<string>
  worldSessions: readonly RecorderMinecraftApiV1WorldSession[]
}>()

const emit = defineEmits<{
  add: []
  close: []
  reveal: [nodeId: string]
}>()

interface FileRow {
  href?: string
  label: string
}

const session = computed(() => props.node.kind === 'session' ? props.node.session : null)
const replay = computed(() => props.node.kind === 'play' ? props.node.replay : null)
const linkedSession = computed(() => replay.value ? worldSessionForReplay(props.worldSessions, replay.value) : undefined)
const plays = computed((): RecorderMinecraftApiV1Replay[] => {
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
const addable = computed(() => plays.value.some(isReplayAddable))
const onTimelineCount = computed(() => plays.value.filter(play => play.connectionId && props.onTimeline.has(play.connectionId)).length)

const title = computed(() => {
  switch (props.node.kind) {
    case 'server':
      return props.node.server.name ?? 'Server instance'
    case 'session':
      return 'World session'
    case 'capture-session':
      return 'Capture session'
    case 'unlinked':
      return 'Plays without a world session'
    case 'play':
      return `${props.node.replay.playerName ?? 'Unknown player'} · Play`
    default:
      return ''
  }
})

function tickRange(start?: string, end?: string): string {
  const startTick = parseTick(start)
  const endTick = parseTick(end)
  if (startTick === undefined)
    return '—'
  if (endTick === undefined)
    return `${startTick} – open`
  return `${startTick} – ${endTick} (${formatDurationTicks(endTick - startTick)})`
}

const sessionFiles = computed((): FileRow[] => {
  const value = session.value
  if (!value)
    return []
  return [
    { href: value.eventsUrl, label: 'world-events' },
    { href: value.metadataUrl, label: 'metadata' },
    ...(value.alignments ?? []).map(alignment => ({ href: alignment.url, label: `alignments/${alignment.name ?? '?'}` })),
  ]
})

const playFiles = computed((): FileRow[] => {
  const value = replay.value
  if (!value)
    return []
  return [
    { href: value.eventsUrl, label: 'events' },
    { href: value.perceptionUrl, label: 'perception' },
    { href: value.actionsUrl, label: 'actions' },
    { href: value.sceneUrl, label: 'scene' },
    { href: value.video?.url, label: 'video' },
    { href: value.framesIndexUrl, label: 'frames index' },
    { href: value.replayUrl, label: 'replay' },
  ]
})

const extensions = computed(() => (replay.value?.extensions ?? []).map(extension => ({
  label: extensionLabel(extension.extensionType ?? 'unknown'),
  supported: playExtensionModules.some(module => module.extensionType === extension.extensionType),
  type: extension.extensionType ?? 'unknown',
})))
</script>

<template>
  <section :aria-label="`Details: ${title}`" class="h-full min-h-0 flex flex-col bg-neutral-950/60">
    <header class="flex flex-wrap items-center gap-1 border-b border-[var(--dashboard-border-color)] px-2 py-1">
      <h2 class="m-0 min-w-24 flex-1 truncate text-xs text-neutral-200 font-medium" :title="title">
        {{ title }}
      </h2>
      <span v-if="plays.length" class="shrink-0 text-[10px] text-neutral-500" :title="`${onTimelineCount} of ${plays.length} Plays on the timeline`">
        {{ onTimelineCount }}/{{ plays.length }} on timeline
      </span>
      <button
        v-if="addable && node.kind !== 'server' && node.kind !== 'unlinked'"
        :aria-label="node.kind === 'play' ? 'Add Play to timeline' : 'Add session to timeline'"
        :title="node.kind === 'play' ? 'Add Play to timeline' : 'Add session to timeline'"
        class="h-6 inline-flex shrink-0 items-center gap-1 border-0 rounded bg-amber-400/15 px-1.5 text-[10px] text-amber-100 hover:bg-amber-400/25"
        type="button"
        @click="emit('add')"
      >
        <span aria-hidden="true" class="i-mingcute-add-line" />
        {{ node.kind === 'play' ? 'Add Play' : 'Add session' }}
      </button>
      <button
        aria-label="Close details"
        class="h-6 w-6 flex shrink-0 items-center justify-center border-0 rounded bg-transparent p-0 text-neutral-500 hover:bg-white/8 hover:text-neutral-200"
        title="Close details"
        type="button"
        @click="emit('close')"
      >
        <span aria-hidden="true" class="i-mingcute-close-line text-xs" />
      </button>
    </header>

    <div class="min-h-0 flex-1 overflow-y-auto px-2 py-1.5 text-[11px] text-neutral-300">
      <!-- World session -->
      <template v-if="session">
        <dl class="details-grid">
          <dt>Started</dt><dd>{{ formatInstantFull(session.startedAt) }}</dd>
          <dt>Ended</dt><dd>{{ session.endedAt ? formatInstantFull(session.endedAt) : 'open' }}</dd>
          <dt>Server ticks</dt><dd>{{ tickRange(session.startServerTick, session.endServerTick) }}</dd>
          <dt>Terminal</dt><dd>{{ session.terminalReason || '—' }}</dd>
        </dl>
        <p v-if="session.streamFailure" class="notice text-amber-200" role="note">
          <span aria-hidden="true" class="i-mingcute-alert-line" /> World stream failure: {{ session.streamFailure }} (coverage ends at Server tick {{ session.endServerTick ?? '?' }})
        </p>
        <p v-if="session.validationError" class="notice text-red-300" role="note">
          <span aria-hidden="true" class="i-mingcute-warning-line" /> {{ session.validationError }}
        </p>
        <div v-if="session.knownGaps?.length" class="mt-1.5">
          <h3 class="section-title">
            Known gaps
          </h3>
          <ul class="m-0 list-none p-0">
            <li v-for="gap in session.knownGaps" :key="gap" class="text-sky-200/80">
              <code>{{ gap }}</code>
            </li>
          </ul>
        </div>

        <h3 class="section-title">
          Plays
        </h3>
        <ul class="m-0 list-none p-0">
          <li v-for="play in session.plays ?? []" :key="play.connectionId" class="flex items-center gap-1">
            <button
              :aria-label="`Show Play of ${play.playerName ?? play.playerUuid}`"
              class="min-w-0 truncate border-0 bg-transparent p-0 text-left text-[11px] text-sky-200 hover:underline"
              :title="`Show Play of ${play.playerName ?? play.playerUuid}`"
              type="button"
              @click="emit('reveal', playNodeId(play.connectionId ?? ''))"
            >
              {{ play.playerName ?? play.playerUuid }}
            </button>
            <span class="shrink-0 text-[10px] text-neutral-500" :title="worldLinkDescription(play.link)">{{ worldLinkShort(play.link) }}</span>
            <span v-if="play.connectionId && onTimeline.has(play.connectionId)" class="i-mingcute-check-circle-fill shrink-0 text-[10px] text-emerald-400" aria-label="on the timeline" role="img" />
          </li>
          <li v-if="!(session.plays ?? []).length" class="text-neutral-500">
            No Plays are linked to this session.
          </li>
        </ul>

        <template v-for="alignment in session.alignments ?? []" :key="alignment.name">
          <h3 class="section-title">
            Alignment · {{ alignment.name }}
          </h3>
          <p v-if="alignment.validationError" class="notice text-red-300" role="note">
            {{ alignment.validationError }}
          </p>
          <p v-else class="m-0">
            {{ alignment.eventCount ?? 0 }} events ·
            <span :class="Number(alignment.divergenceCount ?? 0) > 0 ? 'text-amber-200' : ''">{{ alignment.divergenceCount ?? 0 }} observation divergences</span>
          </p>
          <ul class="m-0 list-none p-0 text-[10px]">
            <li v-for="participant in alignment.participants ?? []" :key="participant.connectionId" class="flex gap-1 text-neutral-400">
              <span class="min-w-0 truncate text-neutral-300">{{ participant.playerName }}</span>
              <span class="shrink-0 tabular-nums">ticks {{ participant.startServerTick }}–{{ participant.endServerTick }}</span>
              <span class="shrink-0">{{ participant.perceptionProvided ? '· perception' : '· no perception' }}</span>
            </li>
          </ul>
        </template>

        <h3 class="section-title">
          Identifiers
        </h3>
        <CopyField label="session id" :value="session.sessionId ?? '—'" />
        <CopyField label="world session" :value="session.id ?? '—'" />
        <CopyField label="server instance" :value="session.serverInstanceId ?? '—'" />

        <h3 class="section-title">
          Files
        </h3>
        <template v-for="file in sessionFiles" :key="file.label">
          <CopyField v-if="file.href" :href="file.href" :label="file.label" :value="file.href" />
          <p v-else class="m-0 text-[10px] text-neutral-600">
            {{ file.label }}: not available
          </p>
        </template>
      </template>

      <!-- Play -->
      <template v-else-if="replay">
        <dl class="details-grid">
          <dt>Player</dt><dd>{{ replay.playerName ?? '—' }}</dd>
          <dt>Server</dt><dd>{{ replay.serverName ?? '—' }}</dd>
          <dt>Started</dt><dd>{{ formatInstantFull(replay.startedAt) }}</dd>
          <dt>Ended</dt><dd>{{ replay.endedAt ? formatInstantFull(replay.endedAt) : '—' }}</dd>
          <dt>Server ticks</dt><dd>{{ tickRange(replay.startServerTick, replay.endServerTick) }}</dd>
          <dt>Terminal</dt><dd>{{ replay.terminalReason || '—' }}</dd>
          <template v-if="replay.summary">
            <dt>Summary</dt>
            <dd>{{ (replay.summary.observedPathDistanceBlocks ?? 0).toFixed(1) }} blocks · {{ (replay.summary.idlePercentage ?? 0).toFixed(1) }}% idle</dd>
          </template>
        </dl>
        <p v-if="replay.captureFailure" class="notice text-amber-200" role="note">
          <span aria-hidden="true" class="i-mingcute-alert-line" /> Capture failure: {{ replay.captureFailure }}
        </p>
        <p v-if="replay.validationError" class="notice text-red-300" role="note">
          <span aria-hidden="true" class="i-mingcute-warning-line" /> {{ replay.validationError }}
        </p>

        <h3 class="section-title">
          World truth
        </h3>
        <p class="m-0" :title="worldLinkDescription(replay.worldSessionLink)">
          {{ worldLinkDescription(replay.worldSessionLink) }}
        </p>
        <button
          v-if="linkedSession"
          aria-label="Show world session"
          title="Show world session"
          class="mt-0.5 border-0 bg-transparent p-0 text-left text-[11px] text-sky-200 hover:underline"
          type="button"
          @click="emit('reveal', sessionNodeId(linkedSession.serverInstanceId ?? '', linkedSession.id ?? ''))"
        >
          <span aria-hidden="true" class="i-mingcute-earth-2-line mr-0.5 align-[-1px]" />Show world session
        </button>

        <template v-if="extensions.length">
          <h3 class="section-title">
            Extensions
          </h3>
          <div class="flex flex-wrap gap-1">
            <span
              v-for="extension in extensions"
              :key="extension.type"
              class="rounded px-1.5 py-0.5 text-[10px]"
              :class="extension.supported ? 'bg-violet-400/10 text-violet-200' : 'bg-white/5 text-neutral-500'"
              :title="extension.supported ? `Supported Play extension: ${extension.type}` : `Unsupported Play extension: ${extension.type}`"
            >
              {{ extension.label }}
            </span>
          </div>
        </template>

        <h3 class="section-title">
          Identifiers
        </h3>
        <CopyField label="connection" :value="replay.connectionId ?? '—'" />
        <CopyField label="session id" :value="replay.sessionId ?? '—'" />
        <CopyField label="player uuid" :value="replay.playerUuid ?? '—'" />
        <CopyField v-if="replay.worldSessionId" label="world session" :value="replay.worldSessionId" />

        <h3 class="section-title">
          Files
        </h3>
        <template v-for="file in playFiles" :key="file.label">
          <CopyField v-if="file.href" :href="file.href" :label="file.label" :value="file.href" />
          <p v-else class="m-0 text-[10px] text-neutral-600">
            {{ file.label }}: not available
          </p>
        </template>
      </template>

      <!-- Capture session -->
      <template v-else-if="node.kind === 'capture-session'">
        <p class="m-0 text-neutral-400">
          These Plays share session id <code>{{ node.sessionId }}</code>, so they align by Server tick, but no world session record exists for it.
        </p>
        <h3 class="section-title">
          Plays
        </h3>
        <ul class="m-0 list-none p-0">
          <li v-for="play in node.plays" :key="play.connectionId">
            <button :aria-label="`Show Play of ${play.playerName}`" class="border-0 bg-transparent p-0 text-left text-[11px] text-sky-200 hover:underline" :title="`Show Play of ${play.playerName}`" type="button" @click="emit('reveal', playNodeId(play.connectionId ?? ''))">
              {{ play.playerName }}
            </button>
          </li>
        </ul>
        <CopyField class="mt-1.5" label="session id" :value="node.sessionId" />
      </template>

      <!-- Server / unlinked group -->
      <template v-else-if="node.kind === 'server'">
        <dl class="details-grid">
          <dt>Sessions</dt><dd>{{ node.sessionCount }}</dd>
          <dt>Plays</dt><dd>{{ node.playCount }}</dd>
        </dl>
        <CopyField class="mt-1.5" label="instance id" :value="node.server.instanceId ?? '—'" />
      </template>
      <p v-else-if="node.kind === 'unlinked'" class="m-0 text-neutral-400">
        Older captures without a world session record. Plays that share a session id are grouped as a capture session and can be added together.
      </p>
    </div>
  </section>
</template>

<style scoped>
.details-grid {
  column-gap: 0.5rem;
  display: grid;
  grid-template-columns: max-content minmax(0, 1fr);
  margin: 0;
  row-gap: 0.125rem;
}

.details-grid dt {
  color: rgb(115 115 115);
  font-size: 10px;
}

.details-grid dd {
  margin: 0;
  min-width: 0;
  overflow-wrap: anywhere;
}

.section-title {
  color: rgb(115 115 115);
  font-size: 10px;
  font-weight: 500;
  letter-spacing: 0.04em;
  margin: 0.6rem 0 0.2rem;
  text-transform: uppercase;
}

.notice {
  margin: 0.375rem 0 0;
  overflow-wrap: anywhere;
}
</style>
