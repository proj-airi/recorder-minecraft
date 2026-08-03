<script setup lang="ts">
import type { EpisodeTrack } from '../domain'

import { defaultTimelineRendererTheme } from '@proj-airi/canvas-timeline-renderer'
import { Virtualizer } from 'virtua/vue'
import { computed, useTemplateRef } from 'vue'

import { useTrackSortable } from '../composables/useTrackSortable'
import { TIMELINE_TRACK_HEIGHT } from '../domain'

const props = defineProps<{
  editable: boolean
  scrollContainer: HTMLDivElement | null
  tracks: EpisodeTrack[]
}>()

const emit = defineEmits<{
  reorder: [sourceIndex: number, targetIndex: number]
}>()

const trackList = useTemplateRef<HTMLElement>('trackList')
const rulerHeight = defaultTimelineRendererTheme.metrics.rulerHeight
const virtualizerScrollRef = computed(() => props.scrollContainer ?? undefined)

const { activeIndex } = useTrackSortable({
  container: trackList,
  enabled: () => props.editable,
  itemIds: () => props.tracks.map(track => track.id),
  onReorder: (sourceIndex, targetIndex) => emit('reorder', sourceIndex, targetIndex),
  rowHeight: TIMELINE_TRACK_HEIGHT,
  scrollContainer: () => props.scrollContainer,
  scrollPaddingTop: rulerHeight,
})
const keptMountedIndexes = computed(() => activeIndex.value === null ? undefined : [activeIndex.value])
</script>

<template>
  <aside ref="trackList" class="relative border-r border-[var(--dashboard-border-color)] bg-[#27272A]">
    <div
      class="sticky top-0 z-2 flex items-center border-b border-[var(--dashboard-border-color)] bg-#27272A px-3 text-[0.625rem] text-neutral-300 tracking-[0.12em] uppercase"
      :style="{ height: `${rulerHeight}px` }"
    />

    <!-- NOTICE: `Virtualizer` is used instead of `VList` because the canvas and headers share an
         external scroll parent. Its `startMargin` and `scrollRef` contracts are documented at
         `https://github.com/inokawa/virtua/blob/be3b7db9186be035e25b8560b8e4f393b3bd9ac6/src/vue/Virtualizer.tsx#L60-L71`. -->
    <Virtualizer
      v-if="scrollContainer"
      :buffer-size="TIMELINE_TRACK_HEIGHT * 3"
      :data="tracks"
      :item-size="TIMELINE_TRACK_HEIGHT"
      :keep-mounted="keptMountedIndexes"
      :scroll-ref="virtualizerScrollRef"
      :start-margin="rulerHeight"
    >
      <template #default="{ item: track }">
        <div
          :key="track.id"
          class="h-16 w-full flex items-center border-b border-[var(--dashboard-border-color)] bg-#171717 px-3 text-neutral-100 will-change-transform"
          :data-track-id="track.id"
          :data-track-kind="track.kind"
        >
          <button
            v-if="editable"
            :aria-label="`Reorder ${track.label}`"
            class="timeline-track-drag-handle mr-1 flex flex-[0_0_1.5rem] cursor-grab touch-none items-center self-stretch justify-center border-0 bg-transparent p-0 text-base focus-visible:text-[#dfdfdf] hover:text-[#dfdfdf] focus-visible:outline-1 focus-visible:outline-white/60 focus-visible:outline-offset-[-3px] focus-visible:outline"
            :title="`Drag to reorder ${track.label}`"
            type="button"
          >
            <span aria-hidden="true" class="i-mingcute-dot-grid-line" />
          </button>
          <span v-else aria-hidden="true" class="i-mingcute-video-line mr-2 shrink-0 text-base text-neutral-500" />
          <div class="min-w-0">
            <p class="m-0 truncate text-sm text-neutral-200">
              {{ track.label }}
            </p>
            <p class="m-0 mt-0.5 text-[10px] text-neutral-500 tracking-wider uppercase">
              {{ track.kind }}
            </p>
          </div>
        </div>
      </template>
    </Virtualizer>
  </aside>
</template>

<style scoped>
.timeline-track-row--dragging {
  border: 1px solid var(--dashboard-border-color-strong);
  box-shadow: 0 12px 30px rgb(0 0 0 / 35%);
}
</style>
