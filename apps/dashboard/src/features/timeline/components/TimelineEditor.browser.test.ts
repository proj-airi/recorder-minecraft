import { createPinia } from 'pinia'
import { expect, it, vi } from 'vitest'
import { render } from 'vitest-browser-vue'

import IndexPage from '../../../pages/index.vue'

import { createEpisode, stressOptions } from '../fixtures/episode'
import { useEpisodeStore } from '../stores/episode'

it('renders the stress fixture before the browser-test timeout', async () => {
  vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(new Response(JSON.stringify({ serverInstances: [] }), {
    headers: { 'Content-Type': 'application/json' },
    status: 200,
  }))))
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
  const screen = await render(IndexPage, {
    container: viewport,
    global: { plugins: [pinia] },
  })

  try {
    const timelineToolbar = screen.getByLabelText('Timeline toolbar')
    await expect.element(timelineToolbar.getByRole('button', { exact: true, name: 'Play' })).toBeVisible()
    await expect.element(screen.getByRole('region', { exact: true, name: 'Tracks' })).toBeVisible()
    await expect.element(screen.getByRole('tab', { exact: true, name: 'Resources' })).toBeVisible()
    await expect.element(screen.getByRole('tab', { exact: true, name: 'Preview' })).toBeVisible()
    await expect.element(screen.getByRole('tab', { exact: true, name: 'Monitor' })).toBeVisible()
    await expect.element(screen.getByRole('tab', { exact: true, name: 'Inputs' })).toBeVisible()
    await expect.element(screen.getByRole('tab', { exact: true, name: 'Timeline' })).toBeVisible()
    await expect.element(screen.getByRole('region', { exact: true, name: 'Input monitor' })).toBeVisible()
    await expect.element(screen.getByText('Select a replay track in the timeline to inspect its inputs.')).toBeVisible()
    await expect.element(screen.getByText('No recordings available')).toBeVisible()
    expect(screen.container.querySelector('[role="separator"][aria-orientation="vertical"]')).not.toBeNull()
    expect(screen.container.querySelectorAll('[role="tab"]')).toHaveLength(5)
    expect(screen.container.querySelector('[role="tab"][aria-label="Inputs"]')?.getAttribute('aria-selected')).toBe('true')
    await expect.poll(() => {
      const resources = dockGridMetrics(screen.container, 'Resources')
      const preview = dockGridMetrics(screen.container, 'Preview')
      return resources.height / (resources.height + preview.height)
    }).toBeCloseTo(5 / 6, 1)
    const resourcesBounds = dockGridMetrics(screen.container, 'Resources')
    const monitorBounds = dockGridMetrics(screen.container, 'Monitor')
    const inputBounds = dockGridMetrics(screen.container, 'Inputs')
    const timelineBounds = dockGridMetrics(screen.container, 'Timeline')
    expect(monitorBounds.height / (monitorBounds.height + timelineBounds.height)).toBeCloseTo(0.7, 1)
    const totalWidth = resourcesBounds.width + monitorBounds.width + inputBounds.width
    expect(resourcesBounds.width / totalWidth).toBeCloseTo(0.125, 2)
    expect(inputBounds.width / totalWidth).toBeCloseTo(0.125, 2)
    expect(inputBounds.left).toBeGreaterThanOrEqual(monitorBounds.right - 1)
    expect(inputBounds.right).toBeCloseTo(totalWidth, 0)
    await expect.element(screen.getByRole('application')).toBeVisible()
    expect(screen.container.querySelector('[aria-label="Reorder Video 1"]')).not.toBeNull()
    await expect.poll(() => screen.container.querySelectorAll('canvas').length).toBeGreaterThanOrEqual(2)

    await expect.poll(() => screen.container.querySelectorAll('[data-track-id]').length).toBe(stressOptions.trackCount)

    const playbackItems = Array.from(screen.container.querySelector('[aria-label="Timeline toolbar"] [aria-label="Current timecode"]')?.parentElement?.children ?? [])
      .map(element => element.getAttribute('aria-label'))
    expect(playbackItems).toEqual([
      'Go to start',
      'Step backward',
      'Play',
      'Current timecode',
      'Step forward',
      'Go to end',
    ])
    const playbackControls = screen.container.querySelector('[aria-label="Timeline toolbar"] [aria-label="Timeline playback controls"]')
    expect(playbackControls).not.toBeNull()
    expect(playbackControls?.parentElement?.getAttribute('aria-label')).toBe('Timeline toolbar')
    expect(playbackControls?.parentElement?.parentElement?.querySelector('.splitpanes')).not.toBeNull()
    expect(screen.container.querySelector('main > [aria-label="Timeline playback controls"]')).toBeNull()
    const buttonsMissingAccessibleText = Array.from(screen.container.querySelectorAll('button'))
      .filter(button => !button.getAttribute('aria-label') || !button.getAttribute('title'))
    expect(buttonsMissingAccessibleText).toHaveLength(0)

    window.dispatchEvent(new KeyboardEvent('keydown', { bubbles: true, code: 'Space', key: ' ' }))
    await expect.element(timelineToolbar.getByRole('button', { exact: true, name: 'Pause' })).toBeEnabled()
    const playbackCanvas = screen.container.querySelector<HTMLCanvasElement>('[role="application"]')
    if (!playbackCanvas)
      throw new Error('Timeline interaction canvas did not mount')
    const playbackBounds = playbackCanvas.getBoundingClientRect()
    Object.defineProperty(playbackCanvas, 'setPointerCapture', { configurable: true, value: () => {} })
    playbackCanvas.dispatchEvent(new PointerEvent('pointerdown', {
      bubbles: true,
      button: 0,
      buttons: 1,
      clientX: playbackBounds.left + playbackBounds.width / 2,
      clientY: playbackBounds.top + 4,
      pointerId: 9,
      pointerType: 'mouse',
    }))
    playbackCanvas.dispatchEvent(new PointerEvent('pointerup', {
      bubbles: true,
      button: 0,
      clientX: playbackBounds.left + playbackBounds.width / 2,
      clientY: playbackBounds.top + 4,
      pointerId: 9,
      pointerType: 'mouse',
    }))
    await new Promise(resolve => setTimeout(resolve, 150))
    expect(displayedTick(screen.container)).toBeGreaterThan(100)
    window.dispatchEvent(new KeyboardEvent('keyup', { bubbles: true, code: 'Space', key: ' ' }))
    window.dispatchEvent(new KeyboardEvent('keydown', { bubbles: true, code: 'Space', key: ' ' }))
    await expect.element(timelineToolbar.getByRole('button', { exact: true, name: 'Play' })).toBeEnabled()
    window.dispatchEvent(new KeyboardEvent('keyup', { bubbles: true, code: 'Space', key: ' ' }))
    screen.container.querySelector<HTMLButtonElement>('[aria-label="Timeline toolbar"] button[aria-label="Go to start"]')?.click()
    await expect.poll(() => displayedTick(screen.container)).toBe(0)

    window.dispatchEvent(new KeyboardEvent('keydown', { bubbles: true, key: 'ArrowRight' }))
    await new Promise<void>(resolve => requestAnimationFrame(() => resolve()))
    window.dispatchEvent(new KeyboardEvent('keyup', { bubbles: true, key: 'ArrowRight' }))
    await expect.element(timelineToolbar.getByLabelText('Current timecode')).toHaveTextContent('00:00:00:01')

    window.dispatchEvent(new KeyboardEvent('keydown', { bubbles: true, key: 'ArrowRight' }))
    await new Promise(resolve => setTimeout(resolve, 380))
    window.dispatchEvent(new KeyboardEvent('keyup', { bubbles: true, key: 'ArrowRight' }))
    await new Promise<void>(resolve => requestAnimationFrame(() => resolve()))
    expect(screen.container.querySelector('[aria-label="Timeline toolbar"] [aria-label="Current timecode"]')?.textContent).not.toContain('00:00:00:01')

    window.dispatchEvent(new KeyboardEvent('keydown', { altKey: true, bubbles: true, code: 'AltLeft', key: 'Alt' }))
    window.dispatchEvent(new KeyboardEvent('keydown', { altKey: true, bubbles: true, key: 'ArrowRight' }))
    await new Promise(resolve => setTimeout(resolve, 700))
    window.dispatchEvent(new KeyboardEvent('keyup', { altKey: true, bubbles: true, key: 'ArrowRight' }))
    window.dispatchEvent(new KeyboardEvent('keyup', { bubbles: true, code: 'AltLeft', key: 'Alt' }))
    await new Promise<void>(resolve => requestAnimationFrame(() => resolve()))
    expect(screen.container.querySelector('[aria-label="Timeline toolbar"] [aria-label="Current timecode"]')?.textContent).not.toContain('00:00:00:01')

    window.dispatchEvent(new KeyboardEvent('keydown', { bubbles: true, code: 'ControlLeft', ctrlKey: true, key: 'Control' }))
    window.dispatchEvent(new KeyboardEvent('keydown', { bubbles: true, code: 'ShiftLeft', ctrlKey: true, key: 'Shift', shiftKey: true }))
    window.dispatchEvent(new KeyboardEvent('keydown', { bubbles: true, ctrlKey: true, key: 'ArrowRight', shiftKey: true }))
    await expect.element(timelineToolbar.getByLabelText('Current timecode')).toHaveTextContent('00:20:00:00')
    window.dispatchEvent(new KeyboardEvent('keyup', { bubbles: true, ctrlKey: true, key: 'ArrowRight', shiftKey: true }))
    window.dispatchEvent(new KeyboardEvent('keyup', { bubbles: true, code: 'ShiftLeft', ctrlKey: true, key: 'Shift' }))
    window.dispatchEvent(new KeyboardEvent('keyup', { bubbles: true, code: 'ControlLeft', key: 'Control' }))

    window.dispatchEvent(new KeyboardEvent('keydown', { bubbles: true, code: 'ControlLeft', ctrlKey: true, key: 'Control' }))
    window.dispatchEvent(new KeyboardEvent('keydown', { bubbles: true, code: 'ShiftLeft', ctrlKey: true, key: 'Shift', shiftKey: true }))
    window.dispatchEvent(new KeyboardEvent('keydown', { bubbles: true, ctrlKey: true, key: 'ArrowLeft', shiftKey: true }))
    await expect.element(timelineToolbar.getByLabelText('Current timecode')).toHaveTextContent('00:00:00:00')
    window.dispatchEvent(new KeyboardEvent('keyup', { bubbles: true, ctrlKey: true, key: 'ArrowLeft', shiftKey: true }))
    window.dispatchEvent(new KeyboardEvent('keyup', { bubbles: true, code: 'ShiftLeft', ctrlKey: true, key: 'Shift' }))
    window.dispatchEvent(new KeyboardEvent('keyup', { bubbles: true, code: 'ControlLeft', key: 'Control' }))

    const segmentCount = episodeStore.episode.segments.length
    const timelineCanvas = screen.container.querySelector<HTMLCanvasElement>('[role="application"]')
    if (!timelineCanvas)
      throw new Error('Timeline interaction canvas did not mount')
    const canvasBounds = timelineCanvas.getBoundingClientRect()
    Object.defineProperty(timelineCanvas, 'setPointerCapture', { value: () => {} })
    timelineCanvas.dispatchEvent(new PointerEvent('pointerdown', {
      bubbles: true,
      button: 0,
      buttons: 1,
      clientX: canvasBounds.left + 80,
      clientY: canvasBounds.top + 64,
      pointerId: 1,
      pointerType: 'mouse',
    }))
    timelineCanvas.dispatchEvent(new PointerEvent('pointerup', {
      bubbles: true,
      button: 0,
      clientX: canvasBounds.left + 80,
      clientY: canvasBounds.top + 64,
      pointerId: 1,
      pointerType: 'mouse',
    }))
    await new Promise<void>(resolve => requestAnimationFrame(() => resolve()))
    window.dispatchEvent(new KeyboardEvent('keydown', { bubbles: true, key: 'Delete' }))
    await expect.poll(() => episodeStore.episode.segments.length).toBeLessThan(segmentCount)
    window.dispatchEvent(new KeyboardEvent('keyup', { bubbles: true, key: 'Delete' }))
    episodeStore.undo()
    await expect.poll(() => episodeStore.episode.segments.length).toBe(segmentCount)

    await expect.element(screen.getByRole('region', { exact: true, name: 'Input monitor' })).toBeVisible()
  }
  finally {
    await screen.unmount()
    viewport.remove()
    vi.unstubAllGlobals()
  }
})

interface DockGridMetrics {
  height: number
  left: number
  right: number
  width: number
}

function displayedTick(container: HTMLElement): number {
  const current = container.querySelector('[aria-label="Timeline toolbar"] [aria-label="Current timecode"]')?.textContent?.split('/')[0]?.trim()
  const values = current?.split(':').map(Number)
  if (!values || values.length !== 4 || values.some(value => !Number.isFinite(value)))
    throw new Error(`Invalid timecode: ${current ?? 'missing'}`)
  return (((values[0]! * 60 + values[1]!) * 60 + values[2]!) * 20) + values[3]!
}

function dockGridMetrics(container: HTMLElement, viewLabel: string): DockGridMetrics {
  const tab = container.querySelector(`[role="tab"][aria-label="${viewLabel}"]`)
  const group = tab?.closest<HTMLElement>('.dv-groupview')
  if (!group)
    throw new Error(`Dockview group for "${viewLabel}" did not mount`)

  // NOTICE: Browser component tests mount the route without `main.ts`, so Dockview's global CSS
  // is absent. Its inline grid sizes and offsets remain authoritative and let this regression test
  // verify nesting and ratios without substituting a second layout implementation.
  const gridViews: HTMLElement[] = []
  let ancestor = group.parentElement
  while (ancestor && ancestor !== container) {
    if (ancestor.classList.contains('dv-view'))
      gridViews.push(ancestor)
    ancestor = ancestor.parentElement
  }
  if (gridViews.length === 0)
    throw new Error(`Dockview grid view for "${viewLabel}" did not mount`)

  const left = gridViews.reduce((sum, view) => sum + Number.parseFloat(view.style.left || '0'), 0)
  const width = gridViews.map(view => Number.parseFloat(view.style.width)).find(Number.isFinite) ?? 0
  const height = gridViews.map(view => Number.parseFloat(view.style.height)).find(Number.isFinite) ?? 0
  return { height, left, right: left + width, width }
}

it('shows a recoverable artifact service error', async () => {
  const fetchMock = vi.fn(() => Promise.resolve(new Response(JSON.stringify({ code: 14, message: 'Artifact backend is unavailable' }), {
    headers: { 'Content-Type': 'application/json' },
    status: 503,
  })))
  vi.stubGlobal('fetch', fetchMock)
  const pinia = createPinia()
  const viewport = document.createElement('div')
  viewport.style.height = '720px'
  viewport.style.width = '1280px'
  document.body.append(viewport)
  const screen = await render(IndexPage, {
    container: viewport,
    global: { plugins: [pinia] },
  })

  try {
    await expect.element(screen.getByText('Artifact service unavailable')).toBeVisible()
    await expect.element(screen.getByText('Start recorder-minecraft serve, then retry the connection.')).toBeVisible()
    screen.container.querySelector<HTMLElement>('summary')?.click()
    await expect.poll(() => screen.container.querySelector<HTMLDetailsElement>('details')?.open).toBe(true)
    expect(screen.container.querySelector('details')?.textContent).toContain('Artifact backend is unavailable (code 14)')
    expect(screen.container.textContent).not.toContain('[object Object]')
    const retryButton = screen.container.querySelector<HTMLButtonElement>('button[aria-label="Retry connection"]')
    expect(retryButton).not.toBeNull()
    retryButton!.click()
    await expect.poll(() => fetchMock.mock.calls.length).toBe(2)
  }
  finally {
    await screen.unmount()
    viewport.remove()
    vi.unstubAllGlobals()
  }
})
