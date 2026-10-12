<script setup lang="ts">
import type { PlayerIdentity } from '../avatar'

import { computed } from 'vue'

import PlayerAvatar from './PlayerAvatar.vue'

const props = withDefaults(defineProps<{
  /** Avatars beyond this count collapse into a "+N" chip. */
  max?: number
  players: readonly PlayerIdentity[]
  size?: number
}>(), { max: 4, size: 18 })

const shown = computed(() => props.players.slice(0, props.max))
const hidden = computed(() => props.players.length - shown.value.length)
const label = computed(() => props.players.map(player => player.name ?? 'Unknown player').join(', '))
</script>

<template>
  <span
    :aria-label="label"
    class="avatar-group inline-flex shrink-0 items-center"
    role="img"
    :style="{ '--avatar-size': `${size}px` }"
    :title="label"
  >
    <span v-for="player in shown" :key="player.uuid ?? player.name" class="avatar-group-item">
      <PlayerAvatar :player="player" :size="size" />
    </span>
    <span v-if="hidden > 0" class="avatar-group-item avatar-group-more">+{{ hidden }}</span>
  </span>
</template>

<style scoped>
/*
 * NOTICE: Motion modeled on the "Avatar group hover" idea from transitions.dev
 * (`https://transitions.dev/detail.html?t=avatar-group-hover`): the stack spreads when the group is
 * hovered, the hovered avatar lifts most and its neighbours less (distance falloff), and the
 * return overshoots slightly. The page does not publish its values in a fetchable form, so the
 * durations, distances, and easing below are our own.
 */
.avatar-group {
  --avatar-overlap: calc(var(--avatar-size) * -0.35);
  --avatar-spread: 2px;
  --avatar-lift: -3px;
  --avatar-ease-out: cubic-bezier(0.34, 1.56, 0.64, 1);
  padding-block: 3px;
}

.avatar-group-item {
  border-radius: 5px;
  box-shadow: 0 0 0 1.5px var(--avatar-ring, #171717);
  display: inline-flex;
  transition:
    margin 220ms var(--avatar-ease-out),
    transform 260ms var(--avatar-ease-out);
}

.avatar-group-item + .avatar-group-item {
  margin-left: var(--avatar-overlap);
}

.avatar-group:hover .avatar-group-item + .avatar-group-item {
  margin-left: var(--avatar-spread);
}

.avatar-group-item:hover {
  transform: translateY(var(--avatar-lift));
}

/* Neighbours of the hovered avatar lift by a third: the falloff. */
.avatar-group-item:hover + .avatar-group-item,
.avatar-group-item:has(+ .avatar-group-item:hover) {
  transform: translateY(calc(var(--avatar-lift) / 3));
}

.avatar-group-more {
  align-items: center;
  background: rgb(255 255 255 / 0.08);
  color: rgb(163 163 163);
  font-size: 9px;
  height: var(--avatar-size);
  justify-content: center;
  min-width: var(--avatar-size);
  padding-inline: 2px;
}

@media (prefers-reduced-motion: reduce) {
  .avatar-group-item {
    transition: none;
  }
}
</style>
