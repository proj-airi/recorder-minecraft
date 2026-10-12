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
  <div class="inventory-slot" :class="[selected && 'selected', !item && 'empty']">
    <span v-if="label" class="slot-label" aria-hidden="true">{{ label }}</span>
    <ItemIcon v-if="item" class="slot-icon" :item-id="item.itemId">
      <span class="slot-name">{{ itemDisplayName(item.itemId) }}</span>
    </ItemIcon>
    <PixelImage v-else-if="placeholder" class="slot-icon slot-placeholder" :url="gameTextureUrl(placeholder)" />
    <span v-if="item && item.count > 1" class="slot-count" aria-hidden="true">{{ item.count }}</span>
    <span v-if="durability" class="slot-durability" aria-hidden="true">
      <span :style="{ background: durability.color, width: durability.width }" />
    </span>
  </div>
</template>

<style scoped>
/*
 * A square cell like the in-game slot. The icon fills the middle and the count overlaps its
 * bottom-right corner, as in game, so the cell never grows taller than wide. The parent grid is a
 * size container; cells read their own width from it through `cqi`.
 */
.inventory-slot {
  aspect-ratio: 1;
  background: rgb(255 255 255 / 5%);
  border: 1px solid rgb(255 255 255 / 8%);
  border-radius: 4px;
  min-width: 0;
  position: relative;
}

.inventory-slot.empty {
  background: rgb(255 255 255 / 2%);
}

.inventory-slot:hover {
  border-color: rgb(255 255 255 / 25%);
}

.inventory-slot.selected {
  border-color: rgb(252 211 77);
  box-shadow: 0 0 0 1px rgb(252 211 77);
}

.slot-icon {
  inset: 14%;
  position: absolute;
}

.slot-placeholder {
  opacity: 0.25;
}

.slot-label {
  color: rgb(115 115 115);
  font-size: 8px;
  left: 2px;
  line-height: 1;
  position: absolute;
  top: 2px;
}

/* Text fallback when no icon is available (no client jar configured, or an unknown item). */
.slot-name {
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
  color: rgb(229 229 229);
  display: -webkit-box;
  font-size: clamp(6px, 1.9cqi, 10px);
  font-weight: 600;
  line-height: 1.1;
  overflow: hidden;
  overflow-wrap: anywhere;
  text-align: center;
}

.slot-count {
  bottom: 1px;
  color: rgb(250 250 250);
  font-size: clamp(8px, 2.4cqi, 11px);
  font-variant-numeric: tabular-nums;
  font-weight: 700;
  line-height: 1;
  position: absolute;
  right: 2px;
  text-shadow: 1px 1px 0 rgb(0 0 0 / 85%);
}

.slot-durability {
  background: rgb(0 0 0 / 70%);
  bottom: 12%;
  height: 2px;
  left: 16%;
  position: absolute;
  right: 16%;
}

.slot-durability > span {
  display: block;
  height: 100%;
}

/* Below ~30px per cell the corner number crowds the icon; the readout line names the slot instead. */
@container (max-width: 300px) {
  .slot-label {
    display: none;
  }
}
</style>
