<script setup lang="ts">
import type { DateValue } from '@ark-ui/vue/date-picker'
import type { RecorderMinecraftApiV1Replay } from '@proj-airi/recorder-minecraft-api'

import { DatePicker } from '@ark-ui/vue/date-picker'
import { computed, toRef } from 'vue'

import { dayKey, useRecordingCalendar } from '../composables/useRecordingCalendar'

const props = defineProps<{
  replays: RecorderMinecraftApiV1Replay[]
}>()

const timeRange = defineModel<null | number[]>({ required: true })
const { clear, focusedDate, recordingDay, recordingDays, selectedDates, selectionLabel, selectDates } = useRecordingCalendar(toRef(props, 'replays'), timeRange)
const markerSummary = computed(() => `${recordingDays.value.size} recording ${recordingDays.value.size === 1 ? 'day' : 'days'}`)

function label(date: DateValue): string {
  const recording = recordingDay(date)
  const formatted = new Intl.DateTimeFormat(undefined, {
    dateStyle: 'full',
    timeZone: 'UTC',
  }).format(date.toDate('UTC'))
  if (!recording)
    return formatted
  return `${formatted}, ${recording.count} ${recording.count === 1 ? 'recording' : 'recordings'}`
}
</script>

<template>
  <!-- NOTICE: The component anatomy follows Ark UI's Vue range-selection example while keeping
       rendering and styling local to this dashboard:
       `https://github.com/chakra-ui/ark/blob/fc62e4ee648ef979415a1b0b2946626a8f0282d1/packages/vue/src/components/date-picker/examples/range-selection.vue#L9-L53`. -->
  <DatePicker.Root
    v-model:focused-value="focusedDate"
    :model-value="selectedDates"
    fixed-weeks
    inline
    locale="en-US"
    selection-mode="range"
    :start-of-week="1"
    time-zone="UTC"
    @update:model-value="selectDates"
  >
    <div class="border border-white/8 rounded bg-neutral-950/35 p-2">
      <div class="mb-1 flex items-center justify-between gap-2 px-1">
        <div class="min-w-0">
          <p class="m-0 truncate text-[10px] text-neutral-300 font-medium">
            {{ selectionLabel() }}
          </p>
          <p class="m-0 mt-0.5 text-[9px] text-neutral-600">
            {{ markerSummary }} · UTC
          </p>
        </div>
        <button
          aria-label="Clear recording date filter"
          class="border-0 bg-transparent p-1 text-[10px] text-neutral-500 disabled:cursor-default hover:text-neutral-200 disabled:opacity-35"
          :disabled="selectedDates.length === 0"
          title="Clear recording date filter"
          type="button"
          @click="clear"
        >
          Clear
        </button>
      </div>

      <DatePicker.View class="w-full" view="day">
        <DatePicker.Context v-slot="calendar">
          <DatePicker.ViewControl class="h-8 flex items-center justify-between">
            <DatePicker.PrevTrigger
              aria-label="Previous month"
              class="h-7 w-7 flex items-center justify-center border-0 rounded bg-transparent text-neutral-500 hover:bg-white/6 hover:text-neutral-200"
              title="Previous month"
            >
              <span aria-hidden="true" class="i-mingcute-left-line" />
            </DatePicker.PrevTrigger>
            <DatePicker.RangeText class="text-xs text-neutral-200 font-medium" />
            <DatePicker.NextTrigger
              aria-label="Next month"
              class="h-7 w-7 flex items-center justify-center border-0 rounded bg-transparent text-neutral-500 hover:bg-white/6 hover:text-neutral-200"
              title="Next month"
            >
              <span aria-hidden="true" class="i-mingcute-right-line" />
            </DatePicker.NextTrigger>
          </DatePicker.ViewControl>

          <DatePicker.Table class="w-full border-collapse table-fixed">
            <DatePicker.TableHead>
              <DatePicker.TableRow>
                <DatePicker.TableHeader
                  v-for="weekDay in calendar.weekDays"
                  :key="weekDay.short"
                  class="h-6 text-center text-[9px] text-neutral-600 font-normal"
                  :title="weekDay.long"
                >
                  {{ weekDay.narrow }}
                </DatePicker.TableHeader>
              </DatePicker.TableRow>
            </DatePicker.TableHead>
            <DatePicker.TableBody>
              <DatePicker.TableRow v-for="(week, weekIndex) in calendar.weeks" :key="weekIndex">
                <DatePicker.TableCell v-for="day in week" :key="dayKey(day)" class="p-0.5" :value="day">
                  <DatePicker.TableCellTrigger
                    :aria-label="label(day)"
                    class="relative h-8 w-full flex items-center justify-center border-0 rounded bg-transparent text-[10px] text-neutral-400 outline-none transition-colors data-[in-range]:bg-amber-400/12 data-[range-end]:bg-amber-400/28 data-[range-start]:bg-amber-400/28 data-[selected]:bg-amber-400/28 hover:bg-white/7 data-[outside-range]:text-neutral-700 data-[today]:text-amber-300 hover:text-neutral-100 data-[focus]:ring-1 data-[focus]:ring-amber-300/70"
                    :title="label(day)"
                  >
                    <span>{{ day.day }}</span>
                    <template v-if="recordingDay(day)">
                      <span
                        v-if="recordingDay(day)!.count > 1"
                        aria-hidden="true"
                        class="absolute right-0.5 top-0 text-[7px] text-emerald-300 font-semibold leading-none"
                      >
                        {{ recordingDay(day)!.count }}
                      </span>
                      <span aria-hidden="true" class="absolute bottom-0.5 h-1 w-1 rounded-full bg-emerald-400 shadow-[0_0_4px_rgba(52,211,153,0.75)]" />
                    </template>
                  </DatePicker.TableCellTrigger>
                </DatePicker.TableCell>
              </DatePicker.TableRow>
            </DatePicker.TableBody>
          </DatePicker.Table>
        </DatePicker.Context>
      </DatePicker.View>
    </div>
  </DatePicker.Root>
</template>
