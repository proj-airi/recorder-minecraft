<script setup lang="ts">
import { useTheme } from '@proj-airi/ui'
import { RouterView } from 'vue-router'

const { isDark } = useTheme()
</script>

<template>
  <div :class="['dashboard-app', { dark: isDark }]">
    <RouterView />
  </div>
</template>
