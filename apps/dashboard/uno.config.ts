import { defineConfig } from 'unocss'

import { sharedUnoConfig } from '../../uno.config'

const shared = sharedUnoConfig()

export default defineConfig({
  ...shared,
  content: {
    ...shared.content,
    // NOTICE: The Vite plugin otherwise generates `uno.css` only from modules it has transformed so
    // far. In Vitest browser mode the test files import `uno.css` while components are still being
    // transformed in parallel, so a run could see CSS without a component's utilities: an inventory
    // slot whose size comes only from `aspect-square` then collapsed to zero height and failed a
    // visibility check intermittently. Scanning the sources up front makes the stylesheet complete
    // regardless of transform order, in tests and in the dev server.
    filesystem: ['src/**/*.{vue,ts}'],
  },
})
