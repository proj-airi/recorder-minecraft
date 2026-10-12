<script setup lang="ts">
import { useEventListener } from '@vueuse/core'
import { RouterView } from 'vue-router'

// NOTICE: A mouse click leaves focus on the clicked button. Editor shortcuts deliberately yield
// Space and Backspace to a focused button, so after clicking "Go to start" the next Space pressed
// that button again instead of toggling playback, and Chrome then showed a focus ring because a key
// was pressed while it had focus. Release focus after pointer clicks only (`detail > 0`); keyboard
// activation (`detail === 0`) keeps focus so tabbing through controls still works. Dialogs and
// popovers manage their own focus, so buttons inside them are left alone.
useEventListener(document, 'click', (event: MouseEvent) => {
  if (event.detail === 0 || !(event.target instanceof Element))
    return
  const control = event.target.closest('button, [role="button"], [role="tab"]')
  if (!(control instanceof HTMLElement) || document.activeElement !== control)
    return
  if (control.closest('[role="dialog"], [role="menu"], [role="listbox"], [data-popover], [aria-modal="true"]'))
    return
  control.blur()
})
</script>

<template>
  <RouterView />
</template>

<style>
:root {
  --dashboard-border-color: rgb(255 255 255 / 8%);
  --dashboard-border-color-strong: rgb(255 255 255 / 24%);
  /* Behind the docked panels; darker than panel surfaces so the gaps read as separation. */
  --dashboard-workspace-color: #0b0b0d;
}

html,
body,
#app {
  height: 100%;
  margin: 0;
  overscroll-behavior: none;
}
</style>
