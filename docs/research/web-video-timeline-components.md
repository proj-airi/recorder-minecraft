# Web video timeline component research

> This initial market scan is superseded for recorder-specific design decisions by
> [Dataset timeline editor architecture research](dataset-timeline-architecture.md), which includes
> fixed-revision source review and inspection of the repository's actual artifacts.

Research date: 2026-08-01

## Summary

There are usable browser timeline libraries and complete browser video-editor SDKs, but there is
not yet a mature, broadly adopted, open-source Vue component that can be dropped in to obtain a
CapCut- or Final Cut Pro-class editing experience.

The practical choices are:

1. Evaluate one of the young Vue-native packages for a narrow timeline-only proof of concept.
2. Wrap a framework-neutral canvas timeline or build the Vue presentation around a headless
   timeline/media engine.
3. Buy a commercial SDK when a complete editor, rendering pipeline, and short delivery time matter
   more than source availability and vendor independence.

For recorder-minecraft, the first prototype should treat the timeline as an editor for existing
capture/scene/render spans, not as a browser-side video renderer. That keeps persisted domain data
owned by the recorder contracts and makes the timeline a replaceable UI module.

## Candidate comparison

| Candidate | Form | Timeline capability | License / availability | Assessment |
| --- | --- | --- | --- | --- |
| [`@aicut/vue`](https://github.com/ziqiangai/AiCut/tree/main/packages/vue) | Vue 3 wrapper over `@aicut/core` | Video-editor-oriented Vue surface | MIT; first published in June 2026 and still `0.x` | Closest Vue-native option for a PoC, but too new to select without stress-testing and API-churn review. |
| [`vue-clip-track`](https://github.com/caohongz/vue-clip-track) | Vue 3 component library | Video track editing component | MIT; currently `0.1.x` | Smaller and easier to isolate than a full editor, but still early-stage. |
| [Shotstack Studio SDK](https://shotstack.io/docs/guide/studio-sdk/) | Framework-neutral TypeScript SDK with Vue 3, React, Angular, and vanilla starters | Canvas preview, editable timeline, playback controls, undo/redo, browser export | PolyForm Shield package license; rendering services and commercial terms need review | Most direct documented Vue route to a complete editor, but creates licensing and vendor-model commitments. |
| [Rendley Video Editor UI](https://docs.rendleysdk.com/video-editor-ui/overview.html) | Stencil Web Component | Multi-layer timeline, reorder, trim, split, zoom, transitions, preview, export | Requires a Rendley license; free CDN use exists, source/self-hosting is separately sold | Best literal Web Component integration; strongest fit when commercial closed-source SDK use is acceptable. |
| [IMG.LY Video Editor SDK](https://img.ly/products/video-sdk/) | Commercial SDK with Vue, JavaScript, React, Angular, Svelte, and other integrations | Complete timeline editor, trim/split/reorder, keyframes, templates, browser encoding | Commercial; free trial | Mature product path with broad framework support, but not an open-source component. |
| [`@designcombo/timeline`](https://designcombo.dev/framework/core-concepts/timeline-system) | Canvas-based TypeScript timeline package; surrounding SDK is React-oriented | Tracks, trimmable items, resize controls, drag events, zoom/time conversion | Public npm package; package licensing must be confirmed before adoption | Technically attractive because the timeline itself accepts an `HTMLCanvasElement`; Vue integration is feasible but not first-class. |
| [Canvas Timeline](https://canvastimeline.com/) | Headless engine + Canvas/Web Worker renderer + React bindings | Frame-accurate trim, slip/slide calculations, snapping, spatial indexes | MPL-2.0; initial `0.1.0` release in July 2026 | Promising architecture for large timelines, but much too new for production adoption without a substantial spike. |
| [`@xzdarcy/react-timeline-editor`](https://github.com/xzdarcy/react-timeline-editor) | React component and separate engine | Multiple rows, draggable/resizable actions, playhead and animation engine | MIT; `1.0.0` released in January 2026 | Established open-source timeline reference, but embedding React inside this Vue dashboard would add avoidable runtime and ownership complexity. |
| [vis-timeline](https://github.com/visjs/vis-timeline) | Vanilla JavaScript DOM timeline | Groups/tracks, ranges, move/resize/create/delete, pan and zoom from milliseconds upward | MIT or Apache-2.0 | Mature generic primitive. Good for capture spans and annotations; video-editor behavior such as frame snapping, ripple edits, thumbnails, and waveforms must be built. |
| [Waveform Playlist](https://naomiaro.com/waveform-playlist/) | React components and framework-neutral Web Components | Multi-track audio, waveforms, drag, trim, split, sample-accurate positioning, effects and WAV export | Open-source project; confirm package-level license before adoption | Strong audio-track subsystem, not a complete video timeline. Useful if waveform editing becomes a separate requirement. |

## Media engines and full-editor references

These projects are useful but should not be mistaken for drop-in Vue timeline components:

- [WebAV](https://github.com/WebAV-Tech/WebAV) is an MIT-licensed WebCodecs media SDK. Its clips,
  sprites, compositor, Canvas, and WebAudio integration can provide decode, preview, composition,
  and export beneath a custom Vue timeline. The timeline UI still has to be built.
- [Etro](https://etrojs.dev/docs/intro/) is a framework-neutral frontend video-processing framework
  that supports Vue and plain JavaScript. It supplies composition and rendering rather than a
  professional timeline UI.
- [wavesurfer.js](https://wavesurfer.xyz/docs/) supplies waveform, regions, timeline ticks, zoom,
  envelope, and other audio-focused plugins. It is a useful track renderer, not the global editing
  timeline.
- [OpenCut](https://opencut.dev/) demonstrates a Next.js/Zustand/canvas multi-track editor
  architecture.
- [FreeCut](https://github.com/walterlow/freecut) is a substantial React/browser NLE reference with
  multi-track editing, compound clips, ripple/rolling/slip/slide operations, transitions, WebGPU,
  WebCodecs, OPFS, and worker-based processing. It is an application/reference implementation, not
  an embeddable Vue package.
- [React Video Editor's timeline](https://www.reactvideoeditor.com/docs/core/components/timeline)
  documents useful interaction requirements: multi-track editing, magnetic snapping, marquee
  selection, zoom, history, splitting, shortcuts, and alignment guides. Its implementation is
  React/source-copy oriented.

## What “CapCut / Final Cut-like” actually requires

A convincing NLE timeline is more than horizontally positioned rectangles. Before selecting a
library, the prototype should verify:

- frame/tick-accurate time conversion and snapping;
- linked clips and selection across tracks;
- trim, split, ripple, roll, slip, slide, and gap behavior;
- playhead scrubbing without updating the whole Vue component tree per frame;
- horizontal virtualization for long captures and vertical virtualization for many tracks;
- thumbnail and waveform generation outside the main interaction loop;
- undo/redo transactions rather than ad hoc inverse mutations;
- keyboard focus, shortcuts, marquee selection, autoscroll, and accessible alternatives;
- a stable serialization boundary that maps UI edits back to recorder domain identifiers and
  source-relative ranges without mutating capture inputs.

Canvas rendering helps with dense timelines, but it also moves hit testing, focus, accessibility,
text rendering, and testability into application code. DOM rendering is easier to integrate with
Vue and accessibility tooling, but needs virtualization once clip counts become large.

## Recommendation for recorder-minecraft

### Recommended first spike

Build a disposable route that feeds the same synthetic scene/episode data into:

1. `vue-clip-track`, and
2. either `@aicut/vue` or a thin `vis-timeline` adapter.

Measure the following with at least 1,000 spans: pan/zoom latency, trim precision, autoscroll,
selection behavior, custom clip rendering, controlled-state integration, and how easily edits map
to the recorder's tick/range model. Do not connect export or mutate real captures in this spike.

`vue-clip-track`/`@aicut/vue` test whether an early Vue-native project already meets the interaction
bar. `vis-timeline` establishes the cost of using a mature framework-neutral primitive. The
comparison is more informative than choosing from screenshots or feature lists.

### If a complete video editor is required

- Choose **Rendley** for the simplest framework-independent `<rendley-video-editor>` embedding.
- Choose **Shotstack** when its edit JSON/rendering workflow fits the backend and the Vue starter is
  valuable.
- Evaluate **IMG.LY** when product maturity, templates, mobile parity, and vendor support justify a
  commercial SDK.

Commercial candidates should go through a separate licensing, data-flow, offline-operation, and
export-cost review before implementation.

### If the timeline becomes core product infrastructure

Use a framework-neutral timeline domain model and event/command layer, with Vue responsible only
for composition and controls. Evaluate Canvas Timeline or `@designcombo/timeline` as render and
interaction engines, and WebAV only if browser-side preview/export becomes necessary. This is the
highest-effort route, but it avoids coupling recorder contracts to a young Vue component or a
commercial editor schema.

## Conclusion

There are credible building blocks, but no obvious open-source Vue winner. For the current
dashboard, a short comparative prototype is the lowest-risk next step. A full commercial SDK is
only justified if the requested scope includes browser-side media preview, composition, and export;
for editing recorder scene ranges and render requests, a replaceable timeline-only module is the
better boundary.
