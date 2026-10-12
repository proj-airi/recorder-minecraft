<script setup lang="ts">
import type { PlayerIdentity } from '../avatar'

import { computed, shallowRef } from 'vue'

import PixelImage from '../../items/PixelImage.vue'

import { resolvePlayerAvatar } from '../avatar'

const props = withDefaults(defineProps<{
  player: PlayerIdentity
  /** Rendered size in CSS pixels. */
  size?: number
}>(), { size: 20 })

// Request twice the CSS size so the face stays crisp on high-density displays.
const avatar = computed(() => resolvePlayerAvatar(props.player, props.size * 2))
const imageFailed = shallowRef(false)
const title = computed(() => `${props.player.name ?? 'Unknown player'} · ${imageFailed.value ? 'Skin unavailable, generated avatar' : avatar.value.note}`)
</script>

<template>
  <!-- The inset outline paints above the image, keeping dark faces visible on dark panels. -->
  <span
    class="relative inline-block shrink-0 overflow-hidden rounded-[4px] align-middle outline-1 outline-white/10 outline -outline-offset-1"
    :style="{ height: `${size}px`, width: `${size}px`, background: avatar.generated.background }"
    :title="title"
  >
    <PixelImage
      :alt="`${player.name ?? 'Player'} avatar`"
      class="h-full w-full"
      :url="avatar.imageUrl ?? null"
      @status="imageFailed = avatar.imageUrl !== undefined && $event === 'error'"
    >
      <svg :aria-label="`${player.name ?? 'Player'} avatar`" class="block h-full w-full" role="img" shape-rendering="crispEdges" viewBox="0 0 8 8">
        <template v-for="(on, index) in avatar.generated.pixels" :key="index">
          <rect v-if="on" :fill="avatar.generated.foreground" height="1" width="1" :x="index % 8" :y="Math.floor(index / 8)" />
        </template>
      </svg>
    </PixelImage>
  </span>
</template>
