<script setup lang="ts">
import { computed } from 'vue'

import { formatControlNumber, normalizedPacketValue } from '../utils'

const props = defineProps<{
  sample?: any
}>()

const transitionAvailable = computed(() => props.sample?.transition_available === true)
const control = computed(() => props.sample?.action?.reconstructed_control)
const payload = computed(() => control.value?.payload)
const available = computed(() => transitionAvailable.value && Boolean(payload.value && typeof payload.value === 'object'))
const actionsAvailable = computed(() => transitionAvailable.value && Array.isArray(props.sample?.action?.ordered_packets))
const selectedSlot = computed(() => Number.isInteger(payload.value?.selected_slot) ? payload.value.selected_slot + 1 : null)
const metrics = computed(() => [
  ['Yaw', formatControlNumber(payload.value?.camera_yaw, '°')],
  ['Pitch', formatControlNumber(payload.value?.camera_pitch, '°')],
  ['Hotbar', selectedSlot.value == null ? '-' : `slot ${selectedSlot.value}`],
  ['Delta yaw', formatControlNumber(payload.value?.camera_delta_yaw, '°')],
  ['Delta pitch', formatControlNumber(payload.value?.camera_delta_pitch, '°')],
  ['Source', control.value?.source?.record_type || (available.value ? 'control_state' : '-')],
])
const packets = computed(() => actionsAvailable.value ? props.sample.action.ordered_packets : [])
const mouseDelta = computed(() => {
  const yaw = payload.value?.camera_delta_yaw
  const pitch = payload.value?.camera_delta_pitch
  if (!available.value || typeof yaw !== 'number' || !Number.isFinite(yaw) || typeof pitch !== 'number' || !Number.isFinite(pitch)) {
    return {
      available: false,
      label: 'Mouse delta unavailable',
      text: 'unavailable',
      x: 80,
      y: 80,
      zero: false,
    }
  }
  const magnitude = Math.hypot(yaw, pitch)
  const displayLength = magnitude === 0 ? 0 : Math.min(54, Math.max(18, magnitude * 2.4))
  const displayScale = magnitude === 0 ? 0 : displayLength / magnitude
  return {
    available: true,
    label: `Accepted mouse delta: yaw ${yaw.toFixed(2)} degrees, pitch ${pitch.toFixed(2)} degrees`,
    text: `Delta ${yaw.toFixed(2)}°, ${pitch.toFixed(2)}°`,
    x: 80 + yaw * displayScale,
    y: 80 + pitch * displayScale,
    zero: magnitude === 0,
  }
})
const inputNote = computed(() => {
  if (!transitionAvailable.value && props.sample)
    return 'This observation has no following transition, so controls and packet actions are unavailable.'
  if (available.value)
    return 'Held keys and accepted camera deltas are reconstructed at 20 Hz; click indicators come from applied packet actions, not raw device events.'
  return 'No reconstructed control is available for this transition; button state is unknown.'
})

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
      <span class="axis-note">{{ available && Number.isInteger(control?.server_tick) ? `control tick ${control.server_tick}` : 'unavailable' }}</span>
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
          <button :class="['input-key mouse-key', { active: mousePressed('left'), unavailable: !actionsAvailable }]" type="button" tabindex="-1" :aria-pressed="mousePressed('left')"><kbd>LMB</kbd><span>Left click</span></button>
          <button :class="['input-key mouse-key', { active: mousePressed('right'), unavailable: !actionsAvailable }]" type="button" tabindex="-1" :aria-pressed="mousePressed('right')"><kbd>RMB</kbd><span>Right click</span></button>
        </div>
      </div>
      <figure :class="['mouse-vector', { unavailable: !mouseDelta.available, zero: mouseDelta.zero }]" :aria-label="mouseDelta.label">
        <figcaption><span>Mouse delta</span><output>{{ mouseDelta.text }}</output></figcaption>
        <svg viewBox="0 0 160 160" aria-hidden="true">
          <defs>
            <marker id="mouse-arrowhead" marker-height="5" marker-width="5" orient="auto-start-reverse" ref-x="4.5" ref-y="2.5">
              <path d="M 0 0 L 5 2.5 L 0 5 z" />
            </marker>
          </defs>
          <line class="mouse-axis" x1="10" y1="80" x2="150" y2="80" />
          <line class="mouse-axis" x1="80" y1="10" x2="80" y2="150" />
          <text x="146" y="75">yaw</text>
          <text x="85" y="15">pitch</text>
          <circle class="mouse-origin" cx="80" cy="80" r="3" />
          <line class="mouse-vector-line" x1="80" y1="80" :x2="mouseDelta.x" :y2="mouseDelta.y" marker-end="url(#mouse-arrowhead)" />
        </svg>
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
        <span v-if="!packets.length" class="muted">{{ transitionAvailable ? 'No applied packet actions in this transition' : 'Transition unavailable at this tick' }}</span>
        <span v-for="(packet, index) in packets" v-else :key="index" class="packet-action">{{ String(packet.action_type || 'unknown').replaceAll('_', ' ') }}</span>
      </div>
    </div>
    <p class="muted input-note">
      {{ inputNote }}
    </p>
  </section>
</template>
