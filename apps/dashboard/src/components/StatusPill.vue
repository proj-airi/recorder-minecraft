<script setup lang="ts">
import { badgeClass } from '../utils'

defineProps<{
  value?: unknown
}>()
</script>

<template>
  <span :class="['status', badgeClass(value)]">{{ value ?? '-' }}</span>
</template>
