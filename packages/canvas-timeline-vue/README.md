# Canvas Timeline Vue binding

Vue 3 binding for `@proj-airi/canvas-timeline-renderer`.

`CanvasRenderer.vue` and `useCanvasRenderer.ts` port the worker-backed lifecycle in upstream [`CanvasRenderer.tsx`](https://github.com/techsquidtv/canvas-timeline/blob/1536a2dbc54e3a333ace360894a2e4508b295cf1/packages/renderer/src/CanvasRenderer.tsx#L132-L428). React context is replaced by the required `engine` prop, React callbacks are exposed as Vue events, and React effects/refs are represented by Vue lifecycle hooks, watchers, and template refs.

The upstream custom app-owned canvas APIs, `TimelineCanvasLayer.tsx` and `useTimelineCanvasLayer.ts`, have not yet been ported because the dashboard does not consume them. This is the only renderer-package feature currently omitted.
