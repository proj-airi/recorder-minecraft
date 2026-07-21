<script setup lang="ts">
defineProps<{
  message?: string
  open: boolean
}>()

const emit = defineEmits<{
  answer: [accepted: boolean]
}>()
</script>

<template>
  <div v-if="open" class="confirm-backdrop" role="presentation">
    <section class="confirm-dialog" role="dialog" aria-modal="true" aria-labelledby="confirm-title">
      <p id="confirm-title">{{ message }}</p>
      <div class="actions">
        <button class="quiet" @click="emit('answer', false)">Cancel</button>
        <button class="danger" @click="emit('answer', true)">Confirm</button>
      </div>
    </section>
  </div>
</template>
