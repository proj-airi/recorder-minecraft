<script setup lang="ts">
import type { InventoryItem } from '../../event-log/captureEvents'

import { computed } from 'vue'

import ItemIcon from '../../items/ItemIcon.vue'
import PixelImage from '../../items/PixelImage.vue'

import { gameTextureUrl } from '../../items/icons'
import { itemDisplayName } from '../itemNames'

const props = defineProps<{
  item: InventoryItem | null
  /** Short corner label such as the hotbar number; hidden when the cell is too small. */
  label?: string
  /** Texture path drawn faintly in an empty slot, e.g. the helmet outline of the head slot. */
  placeholder?: string
  selected?: boolean
}>()

// Minecraft draws remaining durability as a bar whose hue runs from green (full) to red (broken).
const durability = computed(() => {
  const item = props.item
  if (!item || item.maxDamage <= 0 || item.damage <= 0)
    return null
  const remaining = Math.max(0, 1 - item.damage / item.maxDamage)
  return { color: `hsl(${Math.round(remaining * 120)} 90% 50%)`, width: `${remaining * 100}%` }
})
</script>

<template>
  <!--
    A square cell like the in-game slot. The icon fills the middle and the count overlaps its
    bottom-right corner, as in game, so the cell never grows taller than wide. The parent grid is an
    `@container`; text scales with it through `cqi`, and below ~30px per cell (a 300px grid) the
    corner number is hidden because it would crowd the icon.
  -->
  <div
    class="relative aspect-square min-w-0 border rounded hover:border-white/25"
    :class="[
      selected ? 'border-amber-300 ring-1 ring-amber-300' : 'border-white/8',
      item ? 'bg-white/5' : 'bg-white/2',
    ]"
  >
    <span v-if="label" class="absolute left-0.5 top-0.5 hidden text-[8px] text-neutral-500 leading-none @[301px]:block" aria-hidden="true">{{ label }}</span>
    <ItemIcon v-if="item" class="absolute inset-[14%]" :item-id="item.itemId">
      <!-- Text fallback when no icon is available (no client jar configured, or an unknown item). -->
      <span class="[overflow-wrap:anywhere] line-clamp-2 text-center text-[clamp(6px,1.9cqi,10px)] text-neutral-200 font-semibold leading-[1.1]">{{ itemDisplayName(item.itemId) }}</span>
    </ItemIcon>
    <PixelImage v-else-if="placeholder" class="absolute inset-[14%] opacity-25" :url="gameTextureUrl(placeholder)" />
    <span
      v-if="item && item.count > 1"
      class="[text-shadow:1px_1px_0_rgb(0_0_0/85%)] absolute bottom-px right-0.5 text-[clamp(8px,2.4cqi,11px)] text-neutral-50 font-bold leading-none tabular-nums"
      aria-hidden="true"
    >{{ item.count }}</span>
    <span v-if="durability" class="absolute inset-x-[16%] bottom-[12%] h-0.5 bg-black/70" aria-hidden="true">
      <span class="block h-full" :style="{ background: durability.color, width: durability.width }" />
    </span>
  </div>
</template>
