import { createPinia } from 'pinia'
import { createApp } from 'vue'
import { createRouter, createWebHistory } from 'vue-router'
import { routes } from 'vue-router/auto-routes'

import App from './App.vue'

import '@unocss/reset/tailwind.css'
// NOTICE: Splitpanes ships its structural layout separately from the Vue components. Keep this
// global import as documented at
// `https://github.com/antoniandre/splitpanes/blob/c13526b5d751ad188e19c6b6797466a7559a88d4/src/views/documentation.vue#L150-L156`.
import 'splitpanes/dist/splitpanes.css'
import 'uno.css'

const router = createRouter({
  history: createWebHistory(),
  routes,
})

createApp(App)
  .use(createPinia())
  .use(router)
  .mount('#app')
