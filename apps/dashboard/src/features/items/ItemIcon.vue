<script setup lang="ts">
import { computed } from 'vue'

import PixelImage from './PixelImage.vue'

import { itemIconUrl } from './icons'

const props = defineProps<{
  itemId: string
}>()

const url = computed(() => itemIconUrl(props.itemId))
</script>

<template>
  <PixelImage :url="url">
    <slot />
  </PixelImage>
</template>
