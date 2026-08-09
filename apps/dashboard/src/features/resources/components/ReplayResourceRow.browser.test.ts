import { expect, it } from 'vitest'
import { render } from 'vitest-browser-vue'

import ReplayResourceRow from './ReplayResourceRow.vue'

it('shows the transient Play summary without final inventory details', async () => {
  const screen = await render(ReplayResourceRow, {
    props: {
      active: false,
      replay: {
        connectionId: 'connection-id',
        playerName: 'player',
        serverName: 'server',
        startedAt: '2026-08-09T05:00:00Z',
        summary: {
          durationTicks: '21',
          finalInventory: [{ count: 4, itemId: 'minecraft:diamond', slot: 2 }],
          idlePercentage: 50,
          observedPathDistanceBlocks: 3.456,
        },
      },
    },
  })

  try {
    await expect.element(screen.getByText('00:01 · 3.5 blocks · 50.0% idle')).toBeVisible()
    expect(screen.getByText('minecraft:diamond')).not.toBeInTheDocument()
  }
  finally {
    await screen.unmount()
  }
})
