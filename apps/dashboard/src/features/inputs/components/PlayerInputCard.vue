<script setup lang="ts">
import type { ControlStateSample, InventoryItem, PlayerStateSample } from '../../event-log/captureEvents'

import { useLocalStorage } from '@vueuse/core'
import { computed } from 'vue'

import PlayerAvatar from '../../players/components/PlayerAvatar.vue'
import InventorySlot from './InventorySlot.vue'
import LookIndicator from './LookIndicator.vue'
import MouseGlyph from './MouseGlyph.vue'
import PlayerStatusBar from './PlayerStatusBar.vue'

import { INVENTORY_SLOTS } from '../../event-log/captureEvents'
import { itemDisplayName } from '../itemNames'

export type PlayerInputStatus = 'error' | 'idle' | 'loading' | 'no-events' | 'ready'

const props = defineProps<{
  color: string
  error?: null | string
  playerName: string
  playerState: null | PlayerStateSample
  playerUuid?: string
  sample: ControlStateSample | null
  selected: boolean
  serverTick: null | number
  sessionLabel?: string
  status: PlayerInputStatus
}>()

type KeyField = 'backward' | 'forward' | 'jump' | 'left' | 'right' | 'sneak' | 'sprint'

interface KeyDef {
  /** Grid area inside the key cluster. */
  area: string
  field: KeyField
  /** Glyph and name of the default Minecraft binding, for modifier caps. */
  glyph?: { key: string, symbol: string }
  label: string
}

const keys: readonly KeyDef[] = [
  { area: 'w', field: 'forward', label: 'W' },
  { area: 'a', field: 'left', label: 'A' },
  { area: 's', field: 'backward', label: 'S' },
  { area: 'd', field: 'right', label: 'D' },
]
const modifierKeys: readonly KeyDef[] = [
  { area: 'jump', field: 'jump', glyph: { key: 'Space', symbol: '␣' }, label: 'Jump' },
  { area: 'sneak', field: 'sneak', glyph: { key: 'Shift', symbol: '⇧' }, label: 'Sneak' },
  { area: 'sprint', field: 'sprint', glyph: { key: 'Ctrl', symbol: '⌃' }, label: 'Sprint' },
]

const EMPTY_SLOTS: readonly (InventoryItem | null)[] = Array.from<InventoryItem | null>({ length: INVENTORY_SLOTS.size }).fill(null)

const selectedSlot = computed(() => props.playerState?.selectedSlot ?? props.sample?.selectedSlot ?? null)
const slots = computed(() => props.playerState?.slots ?? EMPTY_SLOTS)
const hotbarSlots = Array.from({ length: 9 }, (_, index) => INVENTORY_SLOTS.hotbar + index)
const mainSlots = Array.from({ length: 27 }, (_, index) => INVENTORY_SLOTS.main + index)
// Top to bottom as the inventory screen shows them, with the placeholder outline the game draws in
// an empty equipment slot.
const equipmentSlots = [
  { index: INVENTORY_SLOTS.armor.head, name: 'Head', placeholder: 'gui/sprites/container/slot/helmet' },
  { index: INVENTORY_SLOTS.armor.chest, name: 'Chest', placeholder: 'gui/sprites/container/slot/chestplate' },
  { index: INVENTORY_SLOTS.armor.legs, name: 'Legs', placeholder: 'gui/sprites/container/slot/leggings' },
  { index: INVENTORY_SLOTS.armor.feet, name: 'Feet', placeholder: 'gui/sprites/container/slot/boots' },
  { index: INVENTORY_SLOTS.offhand, name: 'Off hand', placeholder: 'gui/sprites/container/slot/shield' },
] as const
const backpackCount = computed(() => [...mainSlots, ...equipmentSlots.map(slot => slot.index)].filter(index => slots.value[index]).length)
const offhand = computed(() => slots.value[INVENTORY_SLOTS.offhand] ?? null)
// One switch for every card, remembered per viewer, so several players' backpacks compare side by side.
const backpackOpen = useLocalStorage('recorder-minecraft.inputs.backpack-open', false)
const pressed = computed(() => {
  const sample = props.sample
  if (!sample)
    return []
  const labels: string[] = [...keys, ...modifierKeys].filter(key => sample[key.field]).map(key => key.label)
  if (sample.leftClick)
    labels.push('left mouse')
  if (sample.rightClick)
    labels.push('right mouse')
  return labels
})
const pressedLabel = computed(() => pressed.value.join(', ') || 'none')

function slotName(index: number): string {
  const item = slots.value[index]
  return item ? `${itemDisplayName(item.itemId)}${item.count > 1 ? ` ×${item.count}` : ''}` : 'Empty'
}

/** Where a slot index lives, as a person would say it. */
function slotPlace(index: number): string {
  if (index < INVENTORY_SLOTS.main)
    return `Hotbar ${index + 1}`
  if (index < INVENTORY_SLOTS.armor.feet)
    return `Backpack ${index - INVENTORY_SLOTS.main + 1}`
  return equipmentSlots.find(slot => slot.index === index)?.name ?? `Slot ${index}`
}

function slotAriaLabel(index: number): string {
  const item = slots.value[index]
  return `${slotPlace(index)}${item ? `: ${item.count} ${itemDisplayName(item.itemId)}` : ', empty'}${index === selectedSlot.value ? ', held' : ''}`
}

function slotTitle(index: number): string {
  const item = slots.value[index]
  if (!item)
    return `${slotPlace(index)}: empty`
  const wear = item.maxDamage > 0 ? ` · durability ${item.maxDamage - item.damage}/${item.maxDamage}` : ''
  return `${slotPlace(index)}: ${item.count}× ${itemDisplayName(item.itemId)} (${item.itemId})${wear}`
}

const summaryParts = computed(() => {
  const parts: { key: string, prefix?: string, text: string }[] = []
  if (pressed.value.length > 0)
    parts.push({ key: 'keys', text: pressed.value.join(' · ') })
  if (selectedSlot.value !== null && slots.value[selectedSlot.value])
    parts.push({ key: 'main', prefix: 'holding', text: slotName(selectedSlot.value) })
  if (offhand.value)
    parts.push({ key: 'offhand', prefix: 'off hand', text: slotName(INVENTORY_SLOTS.offhand) })
  return parts
})
</script>

<template>
  <article
    :aria-current="selected ? 'true' : undefined"
    :aria-label="`Inputs of ${playerName}`"
    class="border rounded-lg p-2.5"
    :class="selected ? 'border-amber-300/50 bg-amber-300/6' : 'border-[var(--dashboard-border-color)] bg-white/2'"
  >
    <header class="flex items-center gap-2">
      <!-- The ring carries the player's identity color, so cards stay distinct when faces look alike. -->
      <span class="flex shrink-0 rounded-[6px] p-[2px]" :style="{ boxShadow: `inset 0 0 0 2px ${color}` }">
        <PlayerAvatar :player="{ name: playerName, uuid: playerUuid }" :size="20" />
      </span>
      <h3 class="m-0 min-w-0 truncate text-sm text-neutral-100 font-semibold" :title="sessionLabel ? `${playerName} · ${sessionLabel}` : playerName">
        {{ playerName }}
      </h3>
      <span v-if="sessionLabel" class="min-w-0 truncate text-[11px] text-neutral-500">{{ sessionLabel }}</span>
      <span class="ml-auto shrink-0 text-[11px] text-neutral-500 font-mono tabular-nums">
        {{ serverTick === null ? 'off clip' : `tick ${serverTick}` }}
      </span>
    </header>

    <p v-if="status === 'idle'" class="m-0 mt-2 text-xs text-neutral-500">
      No clip of this player at the playhead.
    </p>
    <p v-else-if="status === 'loading'" class="m-0 mt-2 flex items-center gap-1 text-xs text-neutral-500">
      <span aria-hidden="true" class="i-mingcute-loading-3-line animate-spin" /> Loading control states…
    </p>
    <p v-else-if="status === 'error'" class="m-0 mt-2 text-xs text-red-300">
      {{ error ?? 'Events could not load.' }}
    </p>
    <p v-else-if="status === 'no-events'" class="m-0 mt-2 text-xs text-neutral-500">
      This Play has no events stream.
    </p>
    <template v-else>
      <!-- Summary first: what is pressed and held reads without scanning the key cluster. -->
      <!-- Only what the cluster and hotbar cannot say at a glance: which keys are down, and the name
           of what is in hand (icons do not name items). Idle and empty states are already visible. -->
      <p v-if="!sample" class="m-0 mt-2 text-xs text-neutral-500">
        No control sample yet at this tick
      </p>
      <p v-else-if="summaryParts.length > 0" class="m-0 mt-2 text-[13px] leading-snug" data-testid="input-summary">
        <template v-for="(part, index) in summaryParts" :key="part.key">
          <span v-if="index > 0" class="text-neutral-600"> · </span>
          <span v-if="part.key === 'keys'" class="text-amber-200 font-medium">{{ part.text }}</span>
          <template v-else>
            <span class="mr-1 text-neutral-400">{{ part.prefix }}</span>
            <span class="text-neutral-100 font-medium">{{ part.text }}</span>
          </template>
        </template>
      </p>
      <PlayerStatusBar v-if="playerState" :status="playerState.status" />

      <div class="mt-2.5 flex flex-wrap items-center gap-x-2.5 gap-y-2" :aria-label="`Pressed: ${pressedLabel}`" role="group">
        <div class="flex items-center gap-2" :class="pressed.length === 0 && 'idle'" aria-hidden="true">
          <div class="wasd">
            <kbd
              v-for="key in keys"
              :key="key.field"
              class="keycap"
              :class="sample?.[key.field] && 'pressed'"
              :style="{ gridArea: key.area }"
            >
              {{ key.label }}
            </kbd>
          </div>
          <div class="flex flex-col gap-1">
            <kbd
              v-for="key in modifierKeys"
              :key="key.field"
              class="keycap modifier"
              :class="sample?.[key.field] && 'pressed'"
              :title="`${key.label} (${key.glyph!.key})`"
            >
              <span class="glyph">{{ key.glyph!.symbol }}</span>{{ key.label }}
            </kbd>
          </div>
        </div>
        <MouseGlyph :left="sample?.leftClick ?? false" :right="sample?.rightClick ?? false" />
        <LookIndicator
          v-if="sample"
          :delta-pitch="sample.cameraDeltaPitch"
          :delta-yaw="sample.cameraDeltaYaw"
          :pitch="sample.cameraPitch"
          :title="`Camera yaw ${sample.cameraYaw.toFixed(2)}°, pitch ${sample.cameraPitch.toFixed(2)}°, delta ${sample.cameraDeltaYaw.toFixed(2)}°, ${sample.cameraDeltaPitch.toFixed(2)}°`"
          :yaw="sample.cameraYaw"
        />
      </div>

      <ol aria-label="Hotbar" class="slot-grid mt-3" lang="en">
        <li
          v-for="index in hotbarSlots"
          :key="index"
          :aria-current="selectedSlot === index ? 'true' : undefined"
          :aria-label="slotAriaLabel(index)"
          :title="slotTitle(index)"
        >
          <InventorySlot :item="slots[index] ?? null" :label="String(index + 1)" :selected="selectedSlot === index" />
        </li>
      </ol>

      <button
        :aria-expanded="backpackOpen"
        aria-label="Toggle backpack"
        class="mt-2 flex items-center gap-1 border-0 rounded bg-transparent px-0.5 py-0.5 text-xs text-neutral-400 hover:text-neutral-100"
        :title="backpackOpen ? 'Hide backpack, armor, and off hand' : 'Show backpack, armor, and off hand'"
        type="button"
        @click="backpackOpen = !backpackOpen"
      >
        <span aria-hidden="true" class="text-sm" :class="backpackOpen ? 'i-mingcute-down-line' : 'i-mingcute-right-line'" />
        <span aria-hidden="true" class="i-mingcute-backpack-line text-sm" />
        Backpack
        <span class="text-neutral-500">· {{ backpackCount === 0 ? 'empty' : `${backpackCount} ${backpackCount === 1 ? 'stack' : 'stacks'}` }}</span>
      </button>
      <div v-if="backpackOpen" class="mt-1.5">
        <ol aria-label="Armor and off hand" class="slot-grid" lang="en">
          <li
            v-for="slot in equipmentSlots"
            :key="slot.index"
            :aria-label="slotAriaLabel(slot.index)"
            :class="slot.name === 'Off hand' && 'offhand'"
            :title="slotTitle(slot.index)"
          >
            <InventorySlot :item="slots[slot.index] ?? null" :placeholder="slot.placeholder" />
          </li>
        </ol>
        <ol aria-label="Backpack" class="slot-grid mt-1" lang="en">
          <li
            v-for="index in mainSlots"
            :key="index"
            :aria-label="slotAriaLabel(index)"
            :title="slotTitle(index)"
          >
            <InventorySlot :item="slots[index] ?? null" />
          </li>
        </ol>
      </div>
    </template>
  </article>
</template>

<style scoped>
.wasd {
  display: grid;
  gap: 4px;
  grid-template-areas:
    '. w .'
    'a s d';
  grid-template-columns: repeat(3, 1.55rem);
}

/* Nothing held: the cluster stays for orientation but steps back so the summary reads first. */
.idle {
  opacity: 0.55;
  transition: opacity 120ms ease-out;
}

/*
 * Raised keycap: the face sits on a darker skirt drawn as a bottom shadow, and a pressed key sinks
 * by the skirt height and lights up. The skirt is a shadow, not a border, so pressing a cap does
 * not shift the layout.
 */
.keycap {
  align-items: center;
  background: rgb(58 58 58);
  border-radius: 6px;
  box-shadow:
    0 2px 0 0 rgb(30 30 30),
    inset 0 1px 0 rgb(255 255 255 / 7%);
  color: rgb(200 200 200);
  display: inline-flex;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Liberation Sans', sans-serif;
  font-size: 11px;
  font-weight: 600;
  gap: 3px;
  height: 1.55rem;
  justify-content: center;
  min-width: 1.55rem;
  padding: 0 5px;
  position: relative;
  top: -1px;
  transition:
    background 60ms ease-out,
    box-shadow 60ms ease-out,
    top 60ms ease-out;
  user-select: none;
}

.keycap.modifier {
  font-size: 10px;
  height: 1.1rem;
  justify-content: flex-start;
  min-width: 3.75rem;
}

.keycap .glyph {
  color: rgb(140 140 140);
  font-size: 11px;
}

.keycap.pressed {
  background: rgb(252 211 77);
  box-shadow: 0 0 8px rgb(252 211 77 / 35%);
  color: rgb(23 23 23);
  top: 1px;
}

.keycap.pressed .glyph {
  color: rgb(82 64 8);
}

/* Nine columns like the in-game inventory; a size container so slots can scale their text. */
.slot-grid {
  container-type: inline-size;
  display: grid;
  gap: 3px;
  grid-template-columns: repeat(9, minmax(0, 1fr));
  list-style: none;
  margin-bottom: 0;
  margin-left: 0;
  margin-right: 0;
  padding: 0;
}

/* The off hand sits apart from the armor, as on the inventory screen. */
.slot-grid > .offhand {
  grid-column: 6;
}
</style>
