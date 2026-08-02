import { createPinia } from 'pinia'
import { expect, it } from 'vitest'
import { render } from 'vitest-browser-vue'

import TimelineEditor from './TimelineEditor.vue'

import { createEpisode, stressOptions } from '../fixtures/episode'
import { useEpisodeStore } from '../stores/episode'

it('renders the stress fixture before the browser-test timeout', async () => {
  const pinia = createPinia()
  const episodeStore = useEpisodeStore(pinia)
  episodeStore.episode = createEpisode(stressOptions)

  const viewport = document.createElement('div')
  viewport.style.height = '720px'
  viewport.style.width = '1280px'
  document.body.append(viewport)

  // NOTICE: Vitest's Vue Browser renderer is asynchronous and its returned locators retry while
  // Vue settles; see
  // `https://github.com/vitest-dev/vitest/blob/ec367cf2a6c955da8304e8cea935d1f3dc034a98/docs/api/browser/vue.md#L5-L24`.
  const screen = await render(TimelineEditor, {
    container: viewport,
    global: { plugins: [pinia] },
  })

  try {
    await expect.element(screen.getByRole('button', { exact: true, name: 'Play' })).toBeVisible()
    await expect.element(screen.getByRole('application')).toBeVisible()
    await expect.element(screen.getByRole('button', { exact: true, name: 'Reorder Video 1' })).toBeVisible()
    await expect.poll(() => screen.container.querySelectorAll('canvas').length).toBeGreaterThanOrEqual(2)

    const mountedTrackCount = screen.container.querySelectorAll('[data-track-id]').length
    expect(mountedTrackCount).toBe(stressOptions.trackCount)
  }
  finally {
    await screen.unmount()
    viewport.remove()
  }
})
