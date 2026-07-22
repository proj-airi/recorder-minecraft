<script setup lang="ts">
import { Button } from '@proj-airi/ui'

import { fmtBytes } from '../utils'

defineProps<{
  activeDatasetId?: string
  datasets: any[]
  issues: string
}>()

const emit = defineEmits<{
  refresh: []
  select: [id: string]
}>()
</script>

<template>
  <aside class="panel dataset-list-panel">
    <div class="panel-heading">
      <h2>Exports</h2>
      <Button class="quiet" variant="ghost" size="sm" label="Refresh" @click="emit('refresh')" />
    </div>
    <div class="dataset-list">
      <p v-if="!datasets.length" class="empty">
        No verified *.dataset exports found.
      </p>
      <button v-for="dataset in datasets" v-else :key="dataset.id" :class="['dataset-item', { active: dataset.id === activeDatasetId }]" @click="emit('select', dataset.id)">
        <strong>{{ dataset.session_id }}</strong>
        <small>{{ dataset.status || 'indexed' }} · {{ dataset.state_count ?? '-' }} observations · {{ dataset.sample_count ?? '-' }} transitions · {{ fmtBytes(dataset.size_bytes) }}</small>
      </button>
    </div>
    <p class="dataset-issues">
      {{ issues }}
    </p>
  </aside>
</template>
