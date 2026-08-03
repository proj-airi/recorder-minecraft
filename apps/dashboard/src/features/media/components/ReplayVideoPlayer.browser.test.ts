import { expect, it } from 'vitest'

import ReplayVideoPlayer from './ReplayVideoPlayer.vue'

it('registers the headless Vidstack player used by the separate preview controls', () => {
  expect(ReplayVideoPlayer).toBeDefined()
  expect(customElements.get('media-player')).toBeDefined()
  expect(customElements.get('media-provider')).toBeDefined()
})
