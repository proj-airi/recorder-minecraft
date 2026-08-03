import type { DateValue } from '@ark-ui/vue/date-picker'
import type { RecorderMinecraftApiV1Replay } from '@proj-airi/recorder-minecraft-api'
import type { MaybeRefOrGetter, Ref } from 'vue'

import { CalendarDate, parseDate, today } from '@internationalized/date'
import { computed, shallowRef, toValue, watch } from 'vue'

export interface RecordingDay {
  count: number
  firstStartedAt: number
  lastStartedAt: number
}

export function dayKey(date: DateValue): string {
  return `${date.year.toString().padStart(4, '0')}-${date.month.toString().padStart(2, '0')}-${date.day.toString().padStart(2, '0')}`
}

export function useRecordingCalendar(
  replays: MaybeRefOrGetter<RecorderMinecraftApiV1Replay[]>,
  timeRange: Ref<null | number[]>,
) {
  let changingTimeRange = false
  const selectedDates = shallowRef<DateValue[]>(rangeToDates(timeRange.value))
  const focusedDate = shallowRef<DateValue>(today('UTC'))
  const recordingDays = computed(() => {
    const days = new Map<string, RecordingDay>()
    for (const replay of toValue(replays)) {
      const startedAt = replay.startedAt ? Date.parse(replay.startedAt) : Number.NaN
      if (!Number.isFinite(startedAt))
        continue
      const key = new Date(startedAt).toISOString().slice(0, 10)
      const existing = days.get(key)
      if (existing) {
        existing.count += 1
        existing.firstStartedAt = Math.min(existing.firstStartedAt, startedAt)
        existing.lastStartedAt = Math.max(existing.lastStartedAt, startedAt)
      }
      else {
        days.set(key, { count: 1, firstStartedAt: startedAt, lastStartedAt: startedAt })
      }
    }
    return days
  })

  // Keep the useful data in view when the catalog first loads or a server/player filter changes.
  // Subsequent month navigation only updates focusedDate and does not recompute recordingDays.
  watch(recordingDays, (days) => {
    const latest = Array.from(days.keys()).sort().at(-1)
    if (latest)
      focusedDate.value = parseDate(latest)
  }, { immediate: true })

  watch(timeRange, (range) => {
    if (!changingTimeRange)
      selectedDates.value = rangeToDates(range)
  }, { flush: 'sync' })

  function clear(): void {
    selectedDates.value = []
    updateTimeRange(null)
  }

  function recordingDay(date: DateValue): RecordingDay | undefined {
    return recordingDays.value.get(dayKey(date))
  }

  function selectDates(value: DateValue[]): void {
    selectedDates.value = value
    if (value.length < 2) {
      updateTimeRange(null)
      return
    }
    // Calendar range endpoints are inclusive. The catalog filter uses a half-open interval, so
    // midnight after the selected end date is the exclusive upper bound.
    const range = [value[0]!.toDate('UTC').getTime(), value[1]!.add({ days: 1 }).toDate('UTC').getTime()]
    updateTimeRange(range)
  }

  function selectionLabel(): string {
    if (selectedDates.value.length < 2)
      return 'All recording dates'
    return `${dayKey(selectedDates.value[0]!)} – ${dayKey(selectedDates.value[1]!)}`
  }

  function updateTimeRange(value: null | number[]): void {
    changingTimeRange = true
    try {
      timeRange.value = value
    }
    finally {
      changingTimeRange = false
    }
  }

  return {
    clear,
    focusedDate,
    recordingDay,
    recordingDays,
    selectDates,
    selectedDates,
    selectionLabel,
  }
}

function rangeToDates(range: null | number[]): DateValue[] {
  if (range?.length !== 2)
    return []
  const start = new Date(range[0]!)
  // The end is exclusive. Moving back one millisecond recovers the selected inclusive civil day.
  const end = new Date(range[1]! - 1)
  return [
    new CalendarDate(start.getUTCFullYear(), start.getUTCMonth() + 1, start.getUTCDate()),
    new CalendarDate(end.getUTCFullYear(), end.getUTCMonth() + 1, end.getUTCDate()),
  ]
}
