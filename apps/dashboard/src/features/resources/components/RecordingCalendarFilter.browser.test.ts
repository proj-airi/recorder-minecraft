import type { RecorderMinecraftApiV1Replay } from '@proj-airi/recorder-minecraft-api'

import { expect, it } from 'vitest'
import { render } from 'vitest-browser-vue'

import RecordingCalendarFilter from './RecordingCalendarFilter.vue'

it('shows recording-day counts and emits a UTC half-open range', async () => {
  const replays = [
    { connectionId: 'one', startedAt: '2026-07-28T08:00:00Z' },
    { connectionId: 'two', startedAt: '2026-07-28T12:00:00Z' },
    { connectionId: 'three', startedAt: '2026-07-30T03:00:00Z' },
  ] satisfies RecorderMinecraftApiV1Replay[]
  let selectedRange: null | number[] = null
  const screen = await render(RecordingCalendarFilter, {
    props: {
      'modelValue': null,
      'onUpdate:modelValue': (value: null | number[]) => {
        selectedRange = value
      },
      replays,
    },
  })

  try {
    await expect.element(screen.getByText('2 recording days')).toBeVisible()
    const first = screen.getByRole('button', { name: /July 28, 2026, 2 recordings/ })
    const last = screen.getByRole('button', { name: /July 30, 2026, 1 recording/ })
    await expect.element(first).toBeVisible()
    await expect.element(last).toBeVisible()

    await first.click()
    await last.click()
    await expect.poll(() => selectedRange).toEqual([
      Date.UTC(2026, 6, 28),
      Date.UTC(2026, 6, 31),
    ])

    await screen.getByRole('button', { name: 'Clear recording date filter' }).click()
    await expect.poll(() => selectedRange).toBeNull()
  }
  finally {
    await screen.unmount()
  }
})
