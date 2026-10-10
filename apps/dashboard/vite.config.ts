import process from 'node:process'

import { resolve } from 'node:path'

import Vue from '@vitejs/plugin-vue'
import Unocss from 'unocss/vite'
import VueMacros from 'vue-macros/vite'
import VueRouter from 'vue-router/vite'

import { defineConfig } from 'vite'

// The artifact API that `recorder-minecraft serve` exposes. Override it to run several API servers
// side by side, for example `RECORDER_MINECRAFT_API_URL=http://127.0.0.1:18080 pnpm dev`.
const apiURL = process.env.RECORDER_MINECRAFT_API_URL || 'http://127.0.0.1:8080'

export default defineConfig({
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
    VueRouter({
      dts: resolve(import.meta.dirname, 'src', 'typed-router.d.ts'),
    }),
    Unocss(),
  ],
  server: {
    proxy: {
      '/api': apiURL,
      '/assets': apiURL,
    },
  },
})
