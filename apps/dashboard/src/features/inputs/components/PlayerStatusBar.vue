<script setup lang="ts">
import type { PlayerStatus } from '../../event-log/captureEvents'

import { computed } from 'vue'

import PixelImage from '../../items/PixelImage.vue'

import { gameTextureUrl } from '../../items/icons'
import { effectDisplayName, romanLevel } from '../itemNames'

const props = defineProps<{
  status: PlayerStatus
}>()

interface Stat {
  /** Fill of the small bar under the value, 0–1. */
  fill: number
  fillColor: string
  /** Fallback icon class when the client jar is not configured. */
  icon: string
  key: string
  label: string
  sprite: string
  title: string
  value: string
}

// Health and food are always shown because they are the survival state a reader checks first.
// Armor, air, absorption, and experience only appear when they say something, so an untouched
// player reads as one short line.
const stats = computed<Stat[]>(() => {
  const status = props.status
  const list: Stat[] = [{
    fill: status.maxHealth > 0 ? status.health / status.maxHealth : 0,
    fillColor: 'rgb(239 68 68)',
    icon: 'i-mingcute-heart-fill text-red-400',
    key: 'health',
    label: 'Health',
    sprite: 'gui/sprites/hud/heart/full',
    title: `Health ${formatHalf(status.health)} / ${formatHalf(status.maxHealth)}${status.absorption > 0 ? `, absorption +${formatHalf(status.absorption)}` : ''}`,
    value: `${formatHalf(status.health)}${status.absorption > 0 ? `+${formatHalf(status.absorption)}` : ''}`,
  }, {
    fill: status.foodLevel / 20,
    fillColor: 'rgb(217 119 6)',
    icon: 'i-mingcute-chicken-line text-amber-400',
    key: 'food',
    label: 'Food',
    sprite: 'gui/sprites/hud/food_full',
    title: `Food ${status.foodLevel} / 20, saturation ${formatHalf(status.saturation)}`,
    value: `${status.foodLevel}`,
  }]
  if (status.armor > 0) {
    list.push({
      fill: status.armor / 20,
      fillColor: 'rgb(163 163 163)',
      icon: 'i-mingcute-shield-line text-neutral-300',
      key: 'armor',
      label: 'Armor',
      sprite: 'gui/sprites/hud/armor_full',
      title: `Armor ${status.armor} / 20`,
      value: `${status.armor}`,
    })
  }
  if (status.maxAir > 0 && status.air < status.maxAir) {
    list.push({
      fill: Math.max(0, status.air) / status.maxAir,
      fillColor: 'rgb(56 189 248)',
      icon: 'i-mingcute-bubble-line text-sky-300',
      key: 'air',
      label: 'Air',
      sprite: 'gui/sprites/hud/air',
      title: `Air ${status.air} / ${status.maxAir} ticks`,
      value: `${Math.ceil(Math.max(0, status.air) / 20)}s`,
    })
  }
  if (status.experienceLevel > 0 || status.experienceProgress > 0) {
    list.push({
      fill: status.experienceProgress,
      fillColor: 'rgb(132 204 22)',
      icon: 'i-mingcute-star-line text-lime-400',
      key: 'experience',
      label: 'Level',
      sprite: '',
      title: `Experience level ${status.experienceLevel}, ${Math.round(status.experienceProgress * 100)}% to next`,
      value: `Lv ${status.experienceLevel}`,
    })
  }
  return list
})

const gameMode = computed(() => props.status.gameMode && props.status.gameMode !== 'survival' ? props.status.gameMode : null)
const dimension = computed(() => {
  const id = props.status.dimension.replace(/^minecraft:/, '')
  return id && id !== 'overworld' ? id.replace(/_/g, ' ') : null
})

/** Health and saturation move in half points; show "9.5", not "9.500000". */
function formatHalf(value: number): string {
  return Number.isInteger(value) ? `${value}` : value.toFixed(1)
}

function effectTime(ticks: number): string {
  if (ticks < 0)
    return '∞'
  const seconds = Math.floor(ticks / 20)
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`
}
</script>

<template>
  <div class="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1.5" aria-label="Player status" role="group">
    <span v-if="!status.alive" class="rounded bg-red-500/20 px-1.5 py-0.5 text-[11px] text-red-200 font-semibold">Dead</span>
    <span
      v-for="stat in stats"
      :key="stat.key"
      class="stat"
      :aria-label="stat.title"
      :title="stat.title"
    >
      <PixelImage v-if="stat.sprite" class="stat-icon" :url="gameTextureUrl(stat.sprite)">
        <span aria-hidden="true" :class="stat.icon" />
      </PixelImage>
      <span v-else aria-hidden="true" class="stat-icon" :class="stat.icon" />
      <span class="stat-value">{{ stat.value }}</span>
      <span aria-hidden="true" class="stat-bar"><span :style="{ background: stat.fillColor, width: `${Math.min(1, Math.max(0, stat.fill)) * 100}%` }" /></span>
    </span>
    <span v-if="gameMode" class="rounded bg-white/8 px-1.5 py-0.5 text-[10px] text-neutral-300 uppercase" title="Game mode">{{ gameMode }}</span>
    <span v-if="dimension" class="rounded bg-white/8 px-1.5 py-0.5 text-[10px] text-neutral-300 capitalize" title="Dimension">{{ dimension }}</span>
  </div>
  <ul v-if="status.effects.length > 0" class="m-0 mt-1.5 flex flex-wrap list-none gap-1 p-0" aria-label="Status effects">
    <li
      v-for="effect in status.effects"
      :key="effect.effectId"
      class="flex items-center gap-1 rounded bg-white/6 py-0.5 pl-0.5 pr-1.5 text-[11px] text-neutral-200"
      :title="`${effect.effectId}, amplifier ${effect.amplifier}, ${effect.durationTicks} ticks left`"
    >
      <PixelImage class="h-4 w-4" :url="gameTextureUrl(`mob_effect/${effect.effectId.replace(/^[^:]*:/, '')}`)">
        <span aria-hidden="true" class="i-mingcute-flask-line text-xs text-violet-300" />
      </PixelImage>
      {{ effectDisplayName(effect.effectId) }}{{ effect.amplifier > 0 ? ` ${romanLevel(effect.amplifier + 1)}` : '' }}
      <span class="text-neutral-500 tabular-nums">{{ effectTime(effect.durationTicks) }}</span>
    </li>
  </ul>
</template>

<style scoped>
.stat {
  align-items: center;
  display: inline-grid;
  gap: 1px 4px;
  grid-template-areas:
    'icon value'
    'icon bar';
  grid-template-columns: auto auto;
}

.stat-icon {
  font-size: 14px;
  grid-area: icon;
  height: 14px;
  width: 14px;
}

.stat-value {
  color: rgb(229 229 229);
  font-size: 12px;
  font-variant-numeric: tabular-nums;
  font-weight: 600;
  grid-area: value;
  line-height: 1;
}

.stat-bar {
  background: rgb(255 255 255 / 10%);
  border-radius: 1px;
  grid-area: bar;
  height: 2px;
  min-width: 22px;
}

.stat-bar > span {
  border-radius: 1px;
  display: block;
  height: 100%;
}
</style>
