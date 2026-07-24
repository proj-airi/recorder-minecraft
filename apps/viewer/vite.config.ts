import { fileURLToPath, URL } from 'node:url'

import Vue from '@vitejs/plugin-vue'

import { defineConfig } from 'vite'

export default defineConfig({
  base: './',
  build: {
    emptyOutDir: true,
    manifest: true,
    outDir: '../minerec/src/minerec/viewer_dist',
    sourcemap: true,
  },
  plugins: [Vue()],
  resolve: {
    alias: {
      '~': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    host: '127.0.0.1',
    proxy: {
      '/api': 'http://127.0.0.1:8766',
    },
  },
})
