<script setup lang="ts">
import type { RecordRef } from '../formats'
import type { InteractionPayload } from '../payload'

import { computed } from 'vue'

import AboutEvidence from './AboutEvidence.vue'
import RecordLink from './RecordLink.vue'

import { dimensionName, formatPos, interactionLabel } from '../labels'

const props = defineProps<{
  payload: InteractionPayload
}>()

const emit = defineEmits<{
  seek: [record: RecordRef]
}>()

const event = computed(() => props.payload.event)
const isClick = computed(() => event.value.kind === 'ACTOR_CONTAINER_CLICK')
</script>

<template>
  <section class="flex flex-col gap-3" aria-label="Container interaction details">
    <header class="border-b border-white/8 pb-3">
      <p class="m-0 text-[10px] text-cyan-300 tracking-wider uppercase">
        {{ isClick ? 'Container click' : 'Container view' }} · {{ interactionLabel(event) }}
      </p>
      <h2 class="m-0 mt-1 text-sm font-semibold">
        {{ payload.actorName }} · {{ payload.containerLabel }}
      </h2>
      <p class="m-0 mt-1 text-xs text-neutral-400">
        <template v-if="isClick">
          A container click applied while a menu backed by this container was open.
        </template>
        <template v-else>
          A container_view record of a menu backed by this container.
        </template>
      </p>
    </header>
    <dl class="grid grid-cols-[max-content_1fr] m-0 items-center gap-x-3 gap-y-1.5 border border-white/8 rounded bg-white/2 p-3 text-xs">
      <dt class="text-neutral-500">
        Record
      </dt>
      <dd class="m-0">
        <RecordLink :actor="payload.actorName" :record="event.source" @seek="emit('seek', $event)" />
      </dd>
      <dt class="text-neutral-500">
        Position
      </dt>
      <dd class="m-0">
        <span class="font-mono">{{ event.blockPos ? formatPos(event.blockPos) : 'unknown' }}</span> in {{ dimensionName(event.dimension) }}
      </dd>
      <template v-if="!isClick">
        <dt class="text-neutral-500">
          View kind
        </dt>
        <dd class="m-0 font-mono">
          {{ event.containerViewKind.toLowerCase() }}
        </dd>
      </template>
    </dl>
    <AboutEvidence :alignment="payload.alignment.header" />
  </section>
</template>
