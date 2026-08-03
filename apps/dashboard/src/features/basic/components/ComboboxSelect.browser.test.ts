import type { AcceptableValue } from 'reka-ui'

import { expect, it } from 'vitest'
import { render } from 'vitest-browser-vue'

import ComboboxSelect from './ComboboxSelect.vue'

it('opens custom options and selects a non-empty filter value', async () => {
  let selected: AcceptableValue | undefined = '__all__'
  const screen = await render(ComboboxSelect, {
    props: {
      'label': 'Server filter',
      'modelValue': selected,
      'onUpdate:modelValue': (value: AcceptableValue | undefined) => {
        selected = value
      },
      'options': [
        { description: '1 available instance', label: 'All servers', value: '__all__' },
        { description: '2 recordings', label: 'Example server', value: 'server-id' },
      ],
    },
  })

  try {
    await screen.getByRole('button', { name: 'Open Server filter' }).click()
    await expect.element(screen.getByText('All servers', { exact: true })).toBeVisible()
    await screen.getByText('Example server', { exact: true }).click()
    await expect.poll(() => selected).toBe('server-id')
  }
  finally {
    await screen.unmount()
  }
})
