import { createPinia } from 'pinia'
import { createApp } from 'vue'
import { createRouter, createWebHistory } from 'vue-router'
import { routes } from 'vue-router/auto-routes'

import App from './App.vue'

import '@unocss/reset/tailwind.css'
// NOTICE: Dockview ships the layout structure and theme variables separately from its Vue
// component, as shown in its setup at
// `https://github.com/mathuo/dockview/blob/08097bd22495af8db171698355dffde93b9f5a88/packages/dockview-vue/README.md#L49-L70`.
import 'dockview-vue/dist/styles/dockview.css'
// NOTICE: Splitpanes ships structural pane and splitter styles separately from its Vue components;
// import them globally as documented at
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
