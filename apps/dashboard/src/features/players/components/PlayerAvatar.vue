<script setup lang="ts">
import type { PlayerIdentity } from '../avatar'

import { computed, shallowRef, watch } from 'vue'

import { resolvePlayerAvatar } from '../avatar'

const props = withDefaults(defineProps<{
  player: PlayerIdentity
  /** Rendered size in CSS pixels. */
  size?: number
}>(), { size: 20 })

// Request twice the CSS size so the face stays crisp on high-density displays.
const avatar = computed(() => resolvePlayerAvatar(props.player, props.size * 2))
const imageFailed = shallowRef(false)
watch(() => avatar.value.imageUrl, () => imageFailed.value = false)
const title = computed(() => `${props.player.name ?? 'Unknown player'} · ${imageFailed.value ? 'Skin unavailable, generated avatar' : avatar.value.note}`)
</script>

<template>
  <span
    class="player-avatar relative inline-block shrink-0 overflow-hidden rounded-[4px] align-middle"
    :style="{ height: `${size}px`, width: `${size}px`, background: avatar.generated.background }"
    :title="title"
  >
    <img
      v-if="avatar.imageUrl && !imageFailed"
      :alt="`${player.name ?? 'Player'} avatar`"
      class="block h-full w-full"
      decoding="async"
      draggable="false"
      loading="lazy"
      referrerpolicy="no-referrer"
      :src="avatar.imageUrl"
      style="image-rendering: pixelated"
      @error="imageFailed = true"
    >
    <svg v-else :aria-label="`${player.name ?? 'Player'} avatar`" class="block h-full w-full" role="img" shape-rendering="crispEdges" viewBox="0 0 8 8">
      <template v-for="(on, index) in avatar.generated.pixels" :key="index">
        <rect v-if="on" :fill="avatar.generated.foreground" height="1" width="1" :x="index % 8" :y="Math.floor(index / 8)" />
      </template>
    </svg>
  </span>
</template>

<style scoped>
/* A hairline over the image keeps dark faces visible against the dark panel background. */
.player-avatar::after {
  border-radius: inherit;
  box-shadow: inset 0 0 0 1px rgb(255 255 255 / 0.1);
  content: '';
  inset: 0;
  pointer-events: none;
  position: absolute;
}
</style>
