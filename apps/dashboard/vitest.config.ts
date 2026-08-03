import Vue from '@vitejs/plugin-vue'
import Unocss from 'unocss/vite'
import VueMacros from 'vue-macros/vite'

import { playwright } from '@vitest/browser-playwright'
import { defineConfig } from 'vitest/config'

// NOTICE: This follows Vitest's Browser Mode configuration with an explicit Playwright provider
// and Chromium instance:
// `https://github.com/vitest-dev/vitest/blob/ec367cf2a6c955da8304e8cea935d1f3dc034a98/docs/guide/browser/index.md#L98-L118`.
export default defineConfig({
  // NOTICE: Vidstack registers the player, default layout, and UI controls through separate entry
  // points. Pre-bundle all three so Browser Mode does not reload midway through a test run. The
  // upstream integration emits the same registrations together:
  // `https://github.com/vidstack/player/blob/04143af0634c5c9633dbd05423d0ee62f99754fd/packages/vidstack/src/plugins.ts#L312-L317`.
  optimizeDeps: {
    include: ['vidstack/player', 'vidstack/player/layouts/default', 'vidstack/player/ui'],
  },
  plugins: [
    VueMacros({
      plugins: {
        vue: Vue({
          template: {
            compilerOptions: {
              isCustomElement: tag => tag.startsWith('media-'),
            },
          },
        }),
        vueJsx: false,
      },
    }),
    Unocss(),
  ],
  test: {
    browser: {
      enabled: true,
      headless: true,
      instances: [{ browser: 'chromium' }],
      provider: playwright(),
      viewport: { height: 720, width: 1280 },
    },
    include: ['src/**/*.browser.test.ts'],
    // NOTICE: ResizeObserver loop notifications report deferred resize delivery, not an exception
    // thrown by application code. Filter only this exact browser message through Vitest's narrow
    // unhandled-error hook, leaving every other uncaught error fatal. The hook contract is at
    // `https://github.com/vitest-dev/vitest/blob/ec367cf2a6c955da8304e8cea935d1f3dc034a98/docs/config/onunhandlederror.md#L6-L18`.
    onUnhandledError(error): boolean | void {
      if (error.message === 'ResizeObserver loop completed with undelivered notifications.')
        return false
    },
    testTimeout: 10_000,
  },
})
