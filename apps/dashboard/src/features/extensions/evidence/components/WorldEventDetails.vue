<script setup lang="ts">
import type { RecordRef } from '../formats'
import type { WorldEventPayload } from '../payload'

import { computed } from 'vue'

import RecordLink from './RecordLink.vue'
import SlotList from './SlotList.vue'

import { dimensionName, formatPos, worldEventColor, worldEventLabel } from '../labels'

const props = defineProps<{
  payload: WorldEventPayload
}>()

const emit = defineEmits<{
  seek: [record: RecordRef]
}>()

const event = computed(() => props.payload.event)
const record = computed<RecordRef>(() => ({ connectionId: '', sequence: event.value.sequence, serverTick: event.value.serverTick, stream: 'WORLD_EVENTS' }))
const snapshot = computed(() => event.value.kind === 'snapshot' ? event.value : null)
const filled = computed(() => snapshot.value?.slots.reduce((total, slot) => total + slot.count, 0) ?? 0)
</script>

<template>
  <section class="flex flex-col gap-3" aria-label="World container event details">
    <header class="border-b border-white/8 pb-3">
      <p class="m-0 text-[10px] tracking-wider uppercase" :style="{ color: worldEventColor(event) }">
        World container {{ event.kind === 'snapshot' ? 'snapshot' : 'removed' }} · {{ worldEventLabel(event) }}
      </p>
      <h2 class="m-0 mt-1 text-sm font-semibold">
        {{ payload.containerLabel }}
      </h2>
      <p class="m-0 mt-1 text-xs text-neutral-400">
        Server-side state at the end of tick {{ event.serverTick }} (engine-reported, not any player's view).
      </p>
    </header>

    <dl class="grid grid-cols-[max-content_1fr] m-0 items-center gap-x-3 gap-y-1.5 border border-white/8 rounded bg-white/2 p-3 text-xs">
      <dt class="text-neutral-500">
        Record
      </dt>
      <dd class="m-0">
        <RecordLink :record="record" @seek="emit('seek', $event)" />
      </dd>
      <dt class="text-neutral-500">
        Position
      </dt>
      <dd class="m-0">
        <span class="font-mono">{{ formatPos(event.blockPos) }}</span> in {{ dimensionName(event.dimension) }}
      </dd>
      <dt class="text-neutral-500">
        Block entity
      </dt>
      <dd class="m-0 font-mono">
        {{ event.blockEntityType || 'unknown' }}
      </dd>
      <template v-if="snapshot">
        <dt class="text-neutral-500">
          Reason
        </dt>
        <dd class="m-0">
          {{ snapshot.reason.toLowerCase().replace('_', ' ') }}
        </dd>
        <dt class="text-neutral-500">
          Contents
        </dt>
        <dd class="m-0" data-testid="world-event-contents-state">
          <span v-if="snapshot.contentsState === 'LOOT_UNGENERATED'" class="text-purple-200">loot table not rolled yet: contents are undetermined, not empty</span>
          <span v-else>{{ snapshot.slots.length }} of {{ snapshot.containerSize }} slots filled, {{ filled }} item(s)</span>
        </dd>
        <template v-if="snapshot.lootTable">
          <dt class="text-neutral-500">
            Loot table
          </dt>
          <dd class="m-0 font-mono">
            {{ snapshot.lootTable }}
          </dd>
        </template>
      </template>
      <template v-else-if="event.kind === 'removed'">
        <dt class="text-neutral-500">
          Cause
        </dt>
        <dd class="m-0">
          {{ event.cause === 'CHUNK_UNLOADED' ? 'chunk unloaded (contents persist in the save)' : event.cause === 'DESTROYED' ? 'block entity destroyed' : 'unspecified' }}
        </dd>
      </template>
    </dl>

    <section v-if="snapshot" class="flex flex-col gap-2" data-testid="world-event-slots">
      <h3 class="m-0 text-[11px] text-neutral-300 font-semibold">
        Full snapshot contents
      </h3>
      <SlotList :empty-label="snapshot.contentsState === 'LOOT_UNGENERATED' ? 'undetermined (loot not generated)' : 'empty'" :slots="snapshot.slots" />
    </section>
  </section>
</template>
