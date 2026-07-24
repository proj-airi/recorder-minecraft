<script setup lang="ts">
import { computed } from 'vue'

import type { AppliedAction, PlayerTickState, ReconstructedControls } from '../types/viewer'

const props = defineProps<{
  actions: AppliedAction[]
  state: PlayerTickState | null
}>()

interface ControlKey {
  field: keyof ReconstructedControls
  key: string
  label: string
  className: string
}

const movementKeys: ControlKey[] = [
  { className: 'forward', field: 'forward', key: 'W', label: 'Forward' },
  { className: 'left', field: 'left', key: 'A', label: 'Left' },
  { className: 'backward', field: 'backward', key: 'S', label: 'Back' },
  { className: 'right', field: 'right', key: 'D', label: 'Right' },
]

const modifierKeys: ControlKey[] = [
  { className: '', field: 'sprint', key: 'SPR', label: 'Sprint' },
  { className: '', field: 'jump', key: 'SPC', label: 'Jump' },
  { className: '', field: 'sneak', key: 'SHF', label: 'Sneak' },
]

const pointerKeys: ControlKey[] = [
  { className: '', field: 'primary', key: 'LMB', label: 'Primary' },
  { className: '', field: 'secondary', key: 'RMB', label: 'Secondary' },
]

const actionSummary = computed(() => (
  props.actions.length === 0 ? 'No applied action at this tick' : `${props.actions.length} ordered action${props.actions.length === 1 ? '' : 's'}`
))

const completePayload = computed(() => (
  props.state ? JSON.stringify(props.state.payload, null, 2) : ''
))

function controlValue(field: keyof ReconstructedControls): boolean | null {
  return props.state?.controls[field] ?? null
}
</script>

<template>
  <section class="panel controls-panel" aria-labelledby="controls-title">
    <div class="panel-heading">
      <div>
        <p class="eyebrow">
          Reconstructed observation
        </p>
        <h2 id="controls-title">
          Control state
        </h2>
      </div>
      <span class="non-raw-label">not raw device telemetry</span>
    </div>

    <div class="control-layout">
      <div>
        <div class="movement-grid">
          <div
            v-for="control in movementKeys"
            :key="control.field"
            class="control-key"
            :class="[
              control.className,
              { active: controlValue(control.field) === true, unavailable: controlValue(control.field) === null },
            ]"
          >
            <kbd>{{ control.key }}</kbd>
            <span>{{ control.label }}</span>
          </div>
        </div>
        <div class="modifier-grid">
          <div
            v-for="control in modifierKeys"
            :key="control.field"
            class="control-key modifier"
            :class="{ active: controlValue(control.field) === true, unavailable: controlValue(control.field) === null }"
          >
            <kbd>{{ control.key }}</kbd>
            <span>{{ control.label }}</span>
          </div>
        </div>
        <div class="pointer-grid">
          <div
            v-for="control in pointerKeys"
            :key="control.field"
            class="control-key pointer"
            :class="{ active: controlValue(control.field) === true, unavailable: controlValue(control.field) === null }"
          >
            <kbd>{{ control.key }}</kbd>
            <span>{{ control.label }}</span>
          </div>
        </div>
      </div>

      <dl v-if="state" class="state-metrics">
        <div><dt>Dimension</dt><dd>{{ state.dimension }}</dd></div>
        <div><dt>Pose</dt><dd>{{ state.pose }}</dd></div>
        <div><dt>Health</dt><dd>{{ state.health.toFixed(1) }}</dd></div>
        <div><dt>Food / air</dt><dd>{{ state.food }} / {{ state.air }}</dd></div>
        <div><dt>Velocity</dt><dd>{{ state.velocity.x.toFixed(2) }}, {{ state.velocity.y.toFixed(2) }}, {{ state.velocity.z.toFixed(2) }}</dd></div>
        <div><dt>XP</dt><dd>{{ state.xp_level }} · {{ Math.round(state.xp_progress * 100) }}%</dd></div>
        <div><dt>Game mode</dt><dd>{{ state.game_mode }}</dd></div>
        <div><dt>Selected slot</dt><dd>{{ state.selected_slot }}</dd></div>
        <div><dt>Entity</dt><dd>#{{ state.current_player_entity_id }}</dd></div>
        <div><dt>Apply barrier</dt><dd>{{ state.apply_barrier }}</dd></div>
      </dl>
    </div>

    <div class="action-heading">
      <strong>Applied packet actions</strong>
      <span>{{ actionSummary }}</span>
    </div>
    <ol v-if="actions.length > 0" class="action-list">
      <li v-for="action in actions" :key="`${action.tick}:${action.apply_sequence}:${action.action_type}`">
        <span class="sequence">#{{ action.apply_sequence }}</span>
        <div>
          <strong>{{ action.label }}</strong>
          <small>{{ action.action_type }}<template v-if="action.detail"> · {{ action.detail }}</template></small>
        </div>
      </li>
    </ol>
    <p v-else class="empty-copy">
      No action was applied at this server tick.
    </p>
    <details v-if="state" class="payload-details">
      <summary>Complete canonical player payload</summary>
      <pre>{{ completePayload }}</pre>
    </details>
  </section>
</template>

<style scoped>
.controls-panel { padding: 1rem; }
.panel-heading, .action-heading { display: flex; justify-content: space-between; align-items: start; gap: 1rem; }
.non-raw-label { max-width: 9rem; color: var(--warning); text-align: right; font: 720 .62rem/1.25 var(--mono); text-transform: uppercase; }
.control-layout { display: grid; grid-template-columns: minmax(14rem, .9fr) minmax(14rem, 1.1fr); gap: .9rem; margin-top: .9rem; }
.movement-grid { display: grid; grid-template-columns: repeat(3, 1fr); grid-template-areas: '. forward .' 'left backward right'; gap: .35rem; }
.control-key.forward { grid-area: forward; }.control-key.left { grid-area: left; }.control-key.backward { grid-area: backward; }.control-key.right { grid-area: right; }
.modifier-grid { display: grid; grid-template-columns: 1fr 1.5fr 1fr; gap: .35rem; margin-top: .35rem; }
.pointer-grid { display: grid; grid-template-columns: 1fr 1fr; gap: .35rem; margin-top: .35rem; }
.control-key { min-width: 0; min-height: 3rem; display: grid; place-content: center; gap: .2rem; border: 1px solid #334139; border-radius: .45rem; background: #0a100c; color: var(--muted); text-align: center; }
.control-key kbd { font: 800 .78rem/1 var(--mono); }.control-key span { font-size: .56rem; font-weight: 720; letter-spacing: .06em; text-transform: uppercase; }
.control-key.active { border-color: #b8f6c7; color: #071009; background: var(--accent); box-shadow: 0 0 1rem #75dd9344; }
.control-key.unavailable { border-style: dashed; opacity: .38; }
.control-key.modifier { min-height: 2.6rem; }.control-key.pointer { min-height: 3.4rem; }
.state-metrics { display: grid; grid-template-columns: 1fr 1fr; gap: .35rem; margin: 0; }
.state-metrics div { min-width: 0; padding: .55rem; border: 1px solid var(--line); border-radius: .45rem; background: #0a100c; }
.state-metrics dt { color: var(--muted); font-size: .57rem; font-weight: 700; letter-spacing: .07em; text-transform: uppercase; }
.state-metrics dd { margin: .25rem 0 0; overflow: hidden; color: #d6e1d8; font: 680 .7rem/1.25 var(--mono); text-overflow: ellipsis; white-space: nowrap; }
.action-heading { align-items: baseline; margin-top: 1rem; padding-top: .9rem; border-top: 1px solid var(--line); }
.action-heading strong { font-size: .78rem; }.action-heading span { color: var(--muted); font-size: .66rem; }
.action-list { display: grid; gap: .35rem; max-height: 12rem; margin: .65rem 0 0; padding: 0; overflow: auto; list-style: none; }
.action-list li { display: grid; grid-template-columns: auto 1fr; gap: .55rem; align-items: center; padding: .5rem; border: 1px solid var(--line); border-radius: .4rem; background: #0a100c; }
.action-list strong { display: block; font-size: .72rem; }.action-list small { display: block; margin-top: .2rem; color: var(--muted); font: 600 .6rem/1.25 var(--mono); }
.sequence { color: var(--accent); font: 700 .65rem/1 var(--mono); }
.empty-copy { margin: .65rem 0 0; color: var(--muted); font-size: .72rem; }
.payload-details { margin-top: .75rem; padding-top: .7rem; border-top: 1px solid var(--line); }.payload-details summary { cursor: pointer; color: #c8d6cb; font-size: .68rem; font-weight: 700; }.payload-details pre { max-height: 18rem; margin: .6rem 0 0; padding: .65rem; overflow: auto; border: 1px solid var(--line); border-radius: .4rem; background: #070b08; color: #b7c8bb; font: 570 .6rem/1.45 var(--mono); white-space: pre-wrap; overflow-wrap: anywhere; }
@media (max-width: 720px) { .control-layout { grid-template-columns: 1fr; } }
</style>
