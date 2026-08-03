<script setup lang="ts">
import type { IDockviewPanelHeaderProps } from 'dockview-vue'

defineProps<{
  params: IDockviewPanelHeaderProps
}>()
</script>

<template>
  <!-- NOTICE: The Dockview Vue renderer passes header API values inside a `params` prop. Preserve
       that adapter contract while replacing the default tab—which includes an individual close
       button—with a title-only tab. See
       `https://github.com/mathuo/dockview/blob/08097bd22495af8db171698355dffde93b9f5a88/packages/dockview-vue/src/utils.ts#L242-L260`. -->
  <span class="h-full flex items-center px-2 text-xs text-neutral-200">
    {{ params.api.title }}
  </span>
</template>
