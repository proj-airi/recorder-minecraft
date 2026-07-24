import { createApp } from 'vue'

import App from './App.vue'

import { createViewerApiClient, parseViewerLaunchUrl } from './api/client'

import './styles.css'

const launch = parseViewerLaunchUrl(window.location.href)

if (launch.sanitizedPath !== `${window.location.pathname}${window.location.search}${window.location.hash}`) {
  window.history.replaceState(window.history.state, '', launch.sanitizedPath)
}

createApp(App, {
  apiClient: createViewerApiClient({ token: launch.token }),
}).mount('#app')
