<script setup lang="ts">
import { RouterView } from 'vue-router'
</script>

<template>
  <RouterView />
</template>

<style>
:root {
  --dashboard-border-color: rgb(255 255 255 / 8%);
  --dashboard-border-color-strong: rgb(255 255 255 / 24%);
}

html,
body,
#app {
  height: 100%;
  margin: 0;
  overscroll-behavior: none;
}
</style>
