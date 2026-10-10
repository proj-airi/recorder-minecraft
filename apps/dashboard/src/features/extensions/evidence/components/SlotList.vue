<script setup lang="ts">
import type { ItemStack } from '../formats'

import { computed } from 'vue'

import { itemName, shortId } from '../labels'

const props = defineProps<{
  /** Stacks that differ from the other side of a comparison are marked. */
  compareWith?: readonly ItemStack[]
  /** Empty-state text, e.g. "empty" or "undetermined". */
  emptyLabel?: string
  slots: readonly ItemStack[]
}>()

function sameStack(left: ItemStack, right: ItemStack): boolean {
  return left.itemId === right.itemId && left.count === right.count && left.damage === right.damage
}

const rows = computed(() => [...props.slots]
  .sort((left, right) => left.slot - right.slot)
  .map(stack => ({
    differs: props.compareWith ? !props.compareWith.some(other => other.slot === stack.slot && sameStack(other, stack)) : false,
    hue: hueOf(stack.itemId),
    name: itemName(stack.itemId),
    stack,
  })))

/** Stable hue per item id, so the same item has the same badge color on both sides. */
function hueOf(itemId: string): number {
  let hash = 0
  for (const char of itemId)
    hash = (hash * 31 + char.charCodeAt(0)) % 360
  return hash
}
</script>

<template>
  <ul v-if="rows.length" class="m-0 flex flex-col list-none gap-1 p-0">
    <li
      v-for="row in rows"
      :key="row.stack.slot"
      class="flex items-center gap-2 border rounded px-2 py-1 text-xs"
      :class="row.differs ? 'border-rose-400/50 bg-rose-500/10' : 'border-white/8 bg-white/3'"
      :data-differs="row.differs || undefined"
    >
      <span
        aria-hidden="true"
        class="h-5 w-5 flex shrink-0 items-center justify-center rounded text-[10px] text-black font-bold"
        :style="{ background: `hsl(${row.hue} 70% 65%)` }"
      >{{ row.name[0] }}</span>
      <span class="min-w-0 flex-1 truncate" :title="row.stack.itemId">{{ row.name }}</span>
      <span class="text-neutral-100 font-mono">×{{ row.stack.count }}</span>
      <span class="w-12 text-right text-[10px] text-neutral-500 font-mono" :title="shortId(row.stack.itemId)">slot {{ row.stack.slot }}</span>
    </li>
  </ul>
  <p v-else class="m-0 border border-white/8 rounded border-dashed px-2 py-2 text-center text-xs text-neutral-400">
    {{ emptyLabel ?? 'empty' }}
  </p>
</template>
