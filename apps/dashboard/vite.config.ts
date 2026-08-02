import { resolve } from 'node:path'

import Vue from '@vitejs/plugin-vue'
import Unocss from 'unocss/vite'
import VueMacros from 'vue-macros/vite'
import VueRouter from 'vue-router/vite'

import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [
    VueMacros({
      plugins: {
        vue: Vue(),
        vueJsx: false,
      },
    }),
    VueRouter({
      dts: resolve(import.meta.dirname, 'src', 'typed-router.d.ts'),
    }),
    Unocss(),
  ],
})
