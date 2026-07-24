<script setup lang="ts">
import { computed } from 'vue'

import type { BundleSummary } from '../types/viewer'
import { formatBytes } from '../utils/viewer'

const props = defineProps<{
  bundle: BundleSummary
}>()

const sortedInventory = computed(() => [...props.bundle.inventory].sort((left, right) => left.path.localeCompare(right.path)))
const inventoryBytes = computed(() => props.bundle.integrity.inventory_bytes || props.bundle.inventory.reduce((total, entry) => total + entry.size, 0))
</script>

<template>
  <section class="panel integrity-panel" aria-labelledby="integrity-title">
    <div class="panel-heading">
      <div>
        <p class="eyebrow">
          Integrity & provenance
        </p>
        <h2 id="integrity-title">
          Validated manifest
        </h2>
      </div>
      <span class="integrity-status" :class="{ valid: bundle.integrity.validated }">
        {{ bundle.integrity.validated ? 'verified' : 'not verified' }}
      </span>
    </div>

    <dl class="integrity-summary">
      <div><dt>Bundle ID</dt><dd :title="bundle.bundle_id">{{ bundle.bundle_id }}</dd></div>
      <div><dt>Format</dt><dd>{{ bundle.format }} / v{{ bundle.version }}</dd></div>
      <div><dt>Manifest</dt><dd>{{ bundle.integrity.inventory_entries }} files · {{ formatBytes(inventoryBytes) }}</dd></div>
      <div><dt>Validated</dt><dd>{{ bundle.integrity.validated_at ? new Date(bundle.integrity.validated_at).toLocaleString() : 'on local import' }}</dd></div>
      <div><dt>Sensitivity</dt><dd>{{ bundle.sensitivity }}</dd></div>
      <div><dt>Known gaps</dt><dd>{{ bundle.modality_gaps.length ? bundle.modality_gaps.join(', ') : 'none declared' }}</dd></div>
    </dl>

    <details class="inventory-details">
      <summary>SHA-256 inventory ({{ sortedInventory.length }})</summary>
      <div class="inventory-list">
        <div v-for="entry in sortedInventory" :key="entry.path" class="inventory-row">
          <span class="role">{{ entry.media_role }}</span>
          <code class="path" :title="entry.path">{{ entry.path }}</code>
          <code class="hash" :title="entry.sha256">{{ entry.sha256 }}</code>
          <span>{{ formatBytes(entry.size) }}</span>
        </div>
      </div>
    </details>

    <ul v-if="bundle.integrity.warnings.length > 0" class="warnings">
      <li v-for="warning in bundle.integrity.warnings" :key="warning">
        {{ warning }}
      </li>
    </ul>
  </section>
</template>

<style scoped>
.integrity-panel { padding: 1rem; }
.panel-heading { display: flex; justify-content: space-between; align-items: start; gap: 1rem; }
.integrity-status { border: 1px solid #6a4943; border-radius: 99px; padding: .28rem .55rem; color: #ffaaa0; font: 760 .63rem/1 var(--mono); text-transform: uppercase; }.integrity-status.valid { border-color: #396748; color: var(--accent); }
.integrity-summary { display: grid; grid-template-columns: 1fr 1fr; gap: .4rem; margin: .8rem 0 0; }
.integrity-summary div { min-width: 0; padding: .55rem; border: 1px solid var(--line); border-radius: .4rem; background: #0a100c; }.integrity-summary dt { color: var(--muted); font-size: .57rem; font-weight: 700; letter-spacing: .06em; text-transform: uppercase; }.integrity-summary dd { margin: .25rem 0 0; overflow: hidden; color: #d4dfd6; font: 610 .66rem/1.3 var(--mono); text-overflow: ellipsis; white-space: nowrap; }
.inventory-details { margin-top: .75rem; padding-top: .7rem; border-top: 1px solid var(--line); }.inventory-details summary { cursor: pointer; color: #cbd8ce; font-size: .7rem; font-weight: 700; }.inventory-list { display: grid; max-height: 16rem; margin-top: .6rem; overflow: auto; border: 1px solid var(--line); border-radius: .45rem; }
.inventory-row { display: grid; grid-template-columns: 7rem minmax(9rem, 1.2fr) minmax(10rem, 1fr) auto; gap: .55rem; padding: .45rem .55rem; border-top: 1px solid var(--line); background: #080d0a; font-size: .6rem; }.inventory-row:first-child { border-top: 0; }.inventory-row code { overflow: hidden; color: #b8c8bc; font: inherit; text-overflow: ellipsis; white-space: nowrap; }.role { color: var(--accent); }.inventory-row > span:last-child { color: var(--muted); font-family: var(--mono); text-align: right; }
.warnings { margin: .75rem 0 0; padding: .6rem .6rem .6rem 1.5rem; border-left: 2px solid var(--warning); background: #1c190e; color: #e8d7a6; font-size: .67rem; }
@media (max-width: 620px) { .integrity-summary { grid-template-columns: 1fr; }.inventory-row { grid-template-columns: 6rem minmax(0, 1fr) auto; }.inventory-row .hash { display: none; } }
</style>
