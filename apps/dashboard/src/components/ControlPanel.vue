<script setup lang="ts">
import { computed } from 'vue'

import { formatControlNumber, normalizedPacketValue } from '../utils'

const props = defineProps<{
  sample?: any
}>()

const control = computed(() => props.sample?.action?.reconstructed_control)
const payload = computed(() => control.value?.payload)
const available = computed(() => Boolean(payload.value && typeof payload.value === 'object'))
const selectedSlot = computed(() => Number.isInteger(payload.value?.selected_slot) ? payload.value.selected_slot + 1 : null)
const metrics = computed(() => [
  ['Yaw', formatControlNumber(payload.value?.camera_yaw, '°')],
  ['Pitch', formatControlNumber(payload.value?.camera_pitch, '°')],
  ['Hotbar', selectedSlot.value == null ? '-' : `slot ${selectedSlot.value}`],
  ['Delta yaw', formatControlNumber(payload.value?.camera_delta_yaw, '°')],
  ['Delta pitch', formatControlNumber(payload.value?.camera_delta_pitch, '°')],
  ['Source', control.value?.source?.record_type || (available.value ? 'control_state' : '-')],
])
const packets = computed(() => Array.isArray(props.sample?.action?.ordered_packets) ? props.sample.action.ordered_packets : [])

function controlPressed(name: string) {
  return available.value && payload.value?.[name] === true
}

function mousePressed(button: 'left' | 'right') {
  return packets.value.some((packet: any) => {
    const data = packet?.payload && typeof packet.payload === 'object' ? packet.payload : {}
    const actionType = normalizedPacketValue(packet?.action_type || data.action_kind)
    const packetType = normalizedPacketValue(data.packet_type).split(':').at(-1) || ''
    const interaction = normalizedPacketValue(data.interaction)
    const action = normalizedPacketValue(data.action)
    if (button === 'left') {
      return actionType === 'swing'
        || packetType === 'swing'
        || (actionType === 'interact' && interaction === 'attack')
        || (actionType === 'player_action' && ['start_destroy_block', 'abort_destroy_block', 'stop_destroy_block'].includes(action))
    }
    return actionType === 'use'
      || packetType.startsWith('use_item')
      || (actionType === 'interact' && ['interact', 'interact_at'].includes(interaction))
  })
}
</script>

<template>
  <section class="input-card" aria-labelledby="input-heading">
    <div class="observation-heading">
      <div><p class="eyebrow">SERVER-RECONSTRUCTED</p><h2 id="input-heading">Controls</h2></div>
      <span class="axis-note">{{ Number.isInteger(control?.server_tick) ? `control tick ${control.server_tick}` : 'unavailable' }}</span>
    </div>
    <div class="input-pad" aria-label="Persistent movement controls">
      <button v-for="item in [['forward', 'W', 'Forward'], ['left', 'A', 'Left'], ['backward', 'S', 'Back'], ['right', 'D', 'Right']]" :key="item[0]" :class="['input-key', `key-${item[0]}`, { active: controlPressed(item[0]), unavailable: !available }]" type="button" tabindex="-1" :aria-pressed="controlPressed(item[0])">
        <kbd>{{ item[1] }}</kbd><span>{{ item[2] }}</span>
      </button>
    </div>
    <div class="input-modifiers">
      <button v-for="item in [['jump', 'Space', 'Jump'], ['sneak', 'Shift', 'Sneak'], ['sprint', 'Ctrl', 'Sprint']]" :key="item[0]" :class="['input-key', item[0] === 'jump' ? 'wide' : '', { active: controlPressed(item[0]), unavailable: !available }]" type="button" tabindex="-1" :aria-pressed="controlPressed(item[0])">
        <kbd>{{ item[1] }}</kbd><span>{{ item[2] }}</span>
      </button>
    </div>
    <div class="pointer-inputs">
      <div class="mouse-buttons">
        <p class="input-section-label">Click actions</p>
        <div class="mouse-button-grid" aria-label="Observed mouse click actions">
          <button :class="['input-key mouse-key', { active: mousePressed('left'), unavailable: !packets.length }]" type="button" tabindex="-1" :aria-pressed="mousePressed('left')"><kbd>LMB</kbd><span>Left click</span></button>
          <button :class="['input-key mouse-key', { active: mousePressed('right'), unavailable: !packets.length }]" type="button" tabindex="-1" :aria-pressed="mousePressed('right')"><kbd>RMB</kbd><span>Right click</span></button>
        </div>
      </div>
      <figure class="mouse-vector unavailable" aria-label="Mouse delta unavailable">
        <figcaption><span>Mouse delta</span><output>{{ available ? `Delta ${formatControlNumber(payload?.camera_delta_yaw, '°')}, ${formatControlNumber(payload?.camera_delta_pitch, '°')}` : 'unavailable' }}</output></figcaption>
      </figure>
    </div>
    <dl class="control-metrics">
      <div v-for="[label, value] in metrics" :key="label">
        <dt>{{ label }}</dt><dd>{{ value }}</dd>
      </div>
    </dl>
    <div>
      <p class="input-section-label">Applied packet actions</p>
      <div class="packet-actions">
        <span v-if="!packets.length" class="muted">No applied packet actions in this transition</span>
        <span v-for="(packet, index) in packets" v-else :key="index" class="packet-action">{{ String(packet.action_type || 'unknown').replaceAll('_', ' ') }}</span>
      </div>
    </div>
    <p class="muted input-note">
      {{ available ? 'Held keys and accepted camera deltas are reconstructed at 20 Hz; click indicators come from applied packet actions, not raw device events.' : 'No reconstructed control is available for this transition; button state is unknown.' }}
    </p>
  </section>
</template>
