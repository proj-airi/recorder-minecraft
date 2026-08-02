# Canvas Timeline renderer port

This package ports the framework-neutral source from [`@techsquidtv/canvas-timeline-renderer`](https://github.com/techsquidtv/canvas-timeline/tree/1536a2dbc54e3a333ace360894a2e4508b295cf1/packages/renderer) at commit `1536a2dbc54e3a333ace360894a2e4508b295cf1`.

The following upstream files retain their behavior, defaults, styling values, public types, and worker protocol:

- `render/types.ts`
- `render/geometry.ts`
- `render/feedback.ts`
- `render/ruler.ts`
- `render/tracks.ts`
- `renderTimeline.ts`
- `worker.ts`

Except for the documented adaptations below, ported source files only add an MPL/NOTICE header. Import specifiers and formatting otherwise remain aligned with upstream.

## Local clip styling

`render/clips.ts` keeps upstream clip geometry and keyframe behavior, but renders clip bodies with a low-opacity accent fill and a full-width label band attached to the top edge. The clip's own color supplies the default fill, outline, and label; the selected state uses the theme's selected colors.

`theme.ts` defaults the selected accent to `#facc15`, the body fill opacity to `0.25`, the label opacity to `0.75`, and adds serializable clip metrics. They can be overridden through a renderer theme or CSS variables:

- `clipFillOpacity` / `--timeline-clip-fill-opacity`
- `clipLabelInset` / `--timeline-clip-label-inset`
- `clipLabelOpacity` / `--timeline-clip-label-opacity`
- `clipLabelPaddingX` / `--timeline-clip-label-padding-x`
- `clipLabelPaddingY` / `--timeline-clip-label-padding-y`

## Package boundary changes

The upstream `CanvasRenderer.tsx`, `TimelineCanvasLayer.tsx`, and `useTimelineCanvasLayer.ts` depend on React. They are not part of this framework-neutral package. Their Vue counterparts belong in `@proj-airi/canvas-timeline-vue`.

`CanvasRenderer.ts` retains only the worker protocol types required by the unchanged worker, and `createRendererWorker.ts` exposes worker construction to the Vue binding. These two files are integration seams, not renderer redesigns.

No renderer capability has been removed: the OffscreenCanvas worker, diagnostics messages, keyframe rendering, CSS-variable theme resolution, default theme, and color presets remain present.
