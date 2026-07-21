import { resolve } from 'node:path'

import Vue from '@vitejs/plugin-vue'
import UnoCSS from 'unocss/vite'
import VueRouter from 'unplugin-vue-router/vite'
import VueMacros from 'vue-macros/vite'

import { defineConfig } from 'vite'

export default defineConfig({
  build: {
    emptyOutDir: true,
    manifest: true,
    outDir: 'dist',
    sourcemap: true,
  },
  plugins: [
    VueRouter({
      dts: resolve(import.meta.dirname, 'src', 'typed-router.d.ts'),
      extensions: ['.vue'],
      importMode: 'async',
      routesFolder: [
        resolve(import.meta.dirname, 'src', 'pages'),
      ],
    }),
    VueMacros({
      plugins: {
        vue: Vue(),
        vueJsx: false,
      },
    }),
    // https://unocss.dev/integrations/vite
    // See uno.config.ts for the shared preset stack.
    UnoCSS(),
  ],
  resolve: {
    alias: {
      '~': resolve(import.meta.dirname, 'src'),
    },
  },
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:8765',
    },
  },
})
