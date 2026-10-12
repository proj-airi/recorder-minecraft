<script setup lang="ts">
import type { ControlStateSample, InventoryItem, PlayerStateSample } from '../../event-log/captureEvents'

import { useLocalStorage } from '@vueuse/core'
import { computed } from 'vue'

import Kbd from '../../basic/components/Kbd.vue'
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
  field: KeyField
  /** Column in the WASD cluster: W sits over S, and A starts the second row. */
  placement?: string
  /** Glyph and name of the default Minecraft binding, for modifier caps. */
  glyph?: { key: string, symbol: string }
  label: string
}

const keys: readonly KeyDef[] = [
  { field: 'forward', label: 'W', placement: 'col-start-2' },
  { field: 'left', label: 'A', placement: 'col-start-1' },
  { field: 'backward', label: 'S' },
  { field: 'right', label: 'D' },
]
const modifierKeys: readonly KeyDef[] = [
  { field: 'jump', glyph: { key: 'Space', symbol: '␣' }, label: 'Jump' },
  { field: 'sneak', glyph: { key: 'Shift', symbol: '⇧' }, label: 'Sneak' },
  { field: 'sprint', glyph: { key: 'Ctrl', symbol: '⌃' }, label: 'Sprint' },
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
        <!-- Nothing held: the cluster stays for orientation but steps back so the summary reads first. -->
        <div class="flex items-center gap-2 transition-opacity duration-120 ease-out" :class="pressed.length === 0 && 'opacity-55'" aria-hidden="true">
          <div class="grid grid-cols-[repeat(3,1.55rem)] gap-1">
            <Kbd
              v-for="key in keys"
              :key="key.field"
              :pressed="sample?.[key.field] ?? false"
              :class="key.placement"
              size="cap"
            >
              {{ key.label }}
            </Kbd>
          </div>
          <div class="flex flex-col gap-1">
            <Kbd
              v-for="key in modifierKeys"
              :key="key.field"
              :pressed="sample?.[key.field] ?? false"
              size="bar"
              :title="`${key.label} (${key.glyph!.key})`"
            >
              <span class="text-[11px]" :class="sample?.[key.field] ? 'text-[#524008]' : 'text-[#8c8c8c]'">{{ key.glyph!.symbol }}</span>{{ key.label }}
            </Kbd>
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

      <ol aria-label="Hotbar" class="grid grid-cols-9 m-0 mt-3 list-none gap-[3px] p-0 @container" lang="en">
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
        <ol aria-label="Armor and off hand" class="grid grid-cols-9 m-0 list-none gap-[3px] p-0 @container" lang="en">
          <li
            v-for="slot in equipmentSlots"
            :key="slot.index"
            :aria-label="slotAriaLabel(slot.index)"
            :class="slot.name === 'Off hand' && 'col-start-6'"
            :title="slotTitle(slot.index)"
          >
            <InventorySlot :item="slots[slot.index] ?? null" :placeholder="slot.placeholder" />
          </li>
        </ol>
        <ol aria-label="Backpack" class="grid grid-cols-9 m-0 mt-1 list-none gap-[3px] p-0 @container" lang="en">
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
