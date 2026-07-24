<script setup lang="ts">
import { computed, shallowRef, useTemplateRef } from 'vue'

import type { ImportProgress } from '../types/viewer'
import { formatBytes } from '../utils/viewer'

const props = defineProps<{
  busy: boolean
  cancellable: boolean
  progress: ImportProgress
}>()

const emit = defineEmits<{
  cancel: []
  import: [file: File]
  reject: [message: string]
}>()

const fileInput = useTemplateRef<HTMLInputElement>('fileInput')
const isDragging = shallowRef(false)

const progressPercent = computed(() => {
  if (props.progress.phase !== 'uploading') {
    return props.busy || props.progress.phase === 'ready' ? 100 : 0
  }
  const total = props.progress.file_size ?? 0
  const loaded = props.progress.uploaded_bytes ?? 0
  return total > 0 ? Math.min(100, Math.max(0, loaded / total * 100)) : 0
})

const progressTitle = computed(() => {
  switch (props.progress.phase) {
    case 'uploading': return `Uploading ${Math.round(progressPercent.value)}%`
    case 'validating': return 'Validating bundle'
    case 'loading': return 'Loading bounded views'
    case 'committing': return 'Activating bundle'
    default: return 'Drop a .mcplay.zip here'
  }
})

function openPicker(): void {
  if (!props.busy) {
    fileInput.value?.click()
  }
}

function onInput(event: Event): void {
  const input = event.currentTarget as HTMLInputElement
  acceptFiles(input.files)
  input.value = ''
}

function onDrop(event: DragEvent): void {
  isDragging.value = false
  if (!props.busy) {
    acceptFiles(event.dataTransfer?.files ?? null)
  }
}

function acceptFiles(files: FileList | null): void {
  if (!files || files.length === 0) {
    return
  }
  if (files.length !== 1) {
    emit('reject', 'Drop exactly one bundle at a time.')
    return
  }
  const file = files.item(0)
  if (!file) {
    return
  }
  if (!file.name.toLowerCase().endsWith('.zip')) {
    emit('reject', 'Portable play bundles must be ZIP files.')
    return
  }
  emit('import', file)
}
</script>

<template>
  <section class="drop-card panel" aria-labelledby="bundle-drop-title">
    <div class="section-heading">
      <div>
        <p class="eyebrow">
          Portable input
        </p>
        <h2 id="bundle-drop-title">
          Open one play bundle
        </h2>
      </div>
      <span class="privacy-badge">stays on this machine</span>
    </div>

    <div
      class="drop-target"
      :class="{ dragging: isDragging, disabled: busy }"
      role="button"
      :aria-busy="busy"
      :aria-disabled="busy"
      tabindex="0"
      @click="openPicker"
      @dragenter.prevent="isDragging = true"
      @dragleave.prevent="isDragging = false"
      @dragover.prevent
      @drop.prevent="onDrop"
      @keydown.enter.prevent="openPicker"
      @keydown.space.prevent="openPicker"
    >
      <input
        ref="fileInput"
        class="visually-hidden"
        type="file"
        accept=".zip,.mcplay.zip,application/zip"
        :disabled="busy"
        @change="onInput"
      >
      <div class="drop-glyph" aria-hidden="true">
        ↓
      </div>
      <div>
        <strong>{{ busy ? `${progressTitle}…` : progressTitle }}</strong>
        <p>{{ progress.message }}</p>
        <small v-if="progress.file_name">
          {{ progress.file_name }} · {{ formatBytes(progress.file_size ?? 0) }}
        </small>
      </div>
    </div>

    <div
      v-if="busy"
      class="validation-progress"
      role="progressbar"
      aria-label="Bundle import progress"
      aria-valuemin="0"
      aria-valuemax="100"
      :aria-valuenow="Math.round(progressPercent)"
      :aria-valuetext="progressTitle"
    >
      <span><i :style="{ width: `${progressPercent}%` }" /></span>
      <small>{{ progressTitle }}</small>
    </div>
    <button v-if="busy && cancellable" class="button secondary cancel-button" type="button" @click="emit('cancel')">
      Cancel import
    </button>
  </section>
</template>

<style scoped>
.drop-card { padding: 1.15rem; }
.section-heading { display: flex; align-items: start; justify-content: space-between; gap: 1rem; }
.privacy-badge { color: var(--accent); border: 1px solid color-mix(in srgb, var(--accent) 35%, transparent); border-radius: 99px; padding: .25rem .55rem; font-size: .68rem; font-weight: 750; }
.drop-target { display: grid; grid-template-columns: auto 1fr; gap: 1rem; align-items: center; min-height: 8rem; margin-top: 1rem; padding: 1rem; border: 1px dashed #41604d; border-radius: .85rem; background: #0a110d; cursor: pointer; transition: border-color .15s, background .15s, transform .15s; }
.drop-target:hover, .drop-target.dragging { border-color: var(--accent); background: #0e1a13; transform: translateY(-1px); }
.drop-target.disabled { cursor: progress; opacity: .78; transform: none; }
.drop-target strong { display: block; font-size: 1rem; }
.drop-target p { margin: .35rem 0 .25rem; color: var(--muted); font-size: .82rem; }
.drop-target small { color: #bfd0c3; font: 650 .7rem/1.3 var(--mono); overflow-wrap: anywhere; }
.drop-glyph { width: 3.1rem; height: 3.1rem; display: grid; place-items: center; border: 1px solid #34503e; border-radius: .7rem; color: var(--accent); background: #122019; font: 750 1.4rem/1 var(--mono); }
.validation-progress { display: grid; grid-template-columns: 1fr auto; align-items: center; gap: .65rem; margin-top: .75rem; }
.validation-progress span { height: .25rem; overflow: hidden; border-radius: 99px; background: #26332b; }
.validation-progress i { display: block; height: 100%; border-radius: inherit; background: var(--accent); box-shadow: 0 0 .7rem #75dd9344; transition: width .12s ease-out; }
.validation-progress small { color: var(--muted); font: 650 .62rem/1 var(--mono); }
.cancel-button { width: 100%; margin-top: .65rem; }
</style>
