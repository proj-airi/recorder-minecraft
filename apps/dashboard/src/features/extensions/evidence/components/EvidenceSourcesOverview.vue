<script setup lang="ts">
import type { EpisodeDraft } from '../../../timeline/domain'
import type { EvidenceSources } from '../sources'

import { computed } from 'vue'

import { worldSessionRefOf } from '../providers'

const props = defineProps<{
  episode: EpisodeDraft
  sources: EvidenceSources
}>()

interface Availability {
  detail: string
  ok: boolean | null
}

const sessions = computed(() => props.episode.sessions.map((session) => {
  const plays = new Map(props.episode.placements
    .filter(placement => placement.sessionKey === session.key)
    .map(placement => [placement.connectionId, placement.source]))
  const ref = worldSessionRefOf([...plays.values()])
  const world = ref ? props.sources.worldSessionState(ref) : null

  let worldEvents: Availability
  let alignment: Availability
  if (session.world?.eventsUrl || (world?.status === 'ready' && world.value.eventsUrl)) {
    worldEvents = { detail: 'world-events.jsonl', ok: true }
  }
  else if (!ref && !session.world) {
    worldEvents = { detail: 'no world session recorded for these Plays', ok: false }
  }
  else {
    worldEvents = world?.status === 'loading' ? { detail: 'checking…', ok: null } : { detail: 'no world-events.jsonl', ok: false }
  }

  const alignments = session.world?.alignments ?? (world?.status === 'ready' ? world.value.alignments : undefined)
  if (alignments && alignments.length > 0)
    alignment = { detail: alignments.map(entry => `${entry.name}.jsonl`).join(', '), ok: true }
  else if (alignments || !ref || world?.status === 'missing')
    alignment = { detail: 'no session alignment (run `recorder-minecraft session align`)', ok: false }
  else
    alignment = { detail: world?.status === 'error' ? 'could not be checked' : 'checking…', ok: null }

  return {
    alignment,
    key: session.key,
    label: session.label,
    plays: [...plays.values()].map(play => ({
      connectionId: play.connectionId,
      perception: play.replay?.perceptionUrl ? { detail: 'perception.jsonl', ok: true } : { detail: 'no perception.jsonl', ok: false },
      playerName: play.playerName,
    })),
    worldEvents,
  }
}))
</script>

<template>
  <div class="mx-auto max-w-xl w-full flex flex-col gap-3" aria-label="Evidence sources">
    <div class="flex flex-col items-center gap-2 py-4 text-center text-neutral-500">
      <span aria-hidden="true" class="i-mingcute-file-search-line text-2xl" />
      <p class="m-0 text-xs" data-testid="evidence-empty">
        Select an observation divergence, container event, or visibility interval on the timeline.
      </p>
    </div>
    <p v-if="sessions.length === 0" class="m-0 text-center text-xs text-neutral-500" data-testid="evidence-no-sessions">
      Add a session to the timeline to see its evidence tracks.
    </p>
    <section v-for="session in sessions" :key="session.key" class="border border-white/8 rounded bg-white/2 p-3 text-xs" data-testid="evidence-session-sources">
      <h3 class="m-0 mb-2 text-[11px] text-neutral-300 font-semibold">
        {{ session.label }}
      </h3>
      <ul class="m-0 flex flex-col list-none gap-1 p-0">
        <li v-for="row in [{ label: 'World stream', value: session.worldEvents }, { label: 'Alignment', value: session.alignment }]" :key="row.label" class="flex gap-2">
          <span class="w-28 shrink-0 text-neutral-500">{{ row.label }}</span>
          <span :class="row.value.ok === true ? 'text-emerald-200' : row.value.ok === false ? 'text-neutral-400' : 'text-neutral-500'">{{ row.value.detail }}</span>
        </li>
        <li v-for="play in session.plays" :key="play.connectionId" class="flex gap-2">
          <span class="w-28 shrink-0 truncate text-neutral-500">{{ play.playerName }} perception</span>
          <span :class="play.perception.ok ? 'text-emerald-200' : 'text-neutral-400'">{{ play.perception.detail }}</span>
        </li>
      </ul>
    </section>
  </div>
</template>
