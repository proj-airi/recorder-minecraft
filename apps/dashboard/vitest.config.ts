import Vue from '@vitejs/plugin-vue'
import Unocss from 'unocss/vite'
import VueMacros from 'vue-macros/vite'

import { playwright } from '@vitest/browser-playwright'
import { defineConfig } from 'vitest/config'

// NOTICE: This follows Vitest's Browser Mode configuration with an explicit Playwright provider
// and Chromium instance:
// `https://github.com/vitest-dev/vitest/blob/ec367cf2a6c955da8304e8cea935d1f3dc034a98/docs/guide/browser/index.md#L98-L118`.
export default defineConfig({
  plugins: [
    VueMacros({
      plugins: {
        vue: Vue(),
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
