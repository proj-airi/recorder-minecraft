# Dashboard resource browser and media preview research

Research snapshot: 2026-08-03.

## Recommendation

Use the following first implementation:

- **`@vuepic/vue-datepicker` v14** for the replay time-range filter. It is the shortest path to a
  Vue 3 date-and-time range picker with seconds, 24-hour display, UTC conversion, and a timestamp
  model. Its built-in stylesheet is acceptable as third-party component CSS; adapt its theme to the
  dashboard through the documented CSS variables rather than recreating calendar behavior.
- **`VList` from `virtua/vue`** for the resource-result list. The existing workspace version is
  already appropriate. Give the Dockview pane a definite height with `min-height: 0`, pass the
  filtered replay array through `data`, use the normal slot API, and put the replay ID on the slot's
  single root node as its Vue `key`.
- **Do not add a general-purpose video-player package for the first preview pane.** Build a small
  Vue-owned playback session around Mediabunny's maintained advanced player example:
  `UrlSource` + `Input`, `CanvasSink` for video, `AudioBufferSink` and Web Audio for audio, and
  `AudioContext.currentTime` as the playback clock. The visible surface remains a Canvas, so the
  timeline and preview can share exact application-owned playback state without routing through an
  `HTMLVideoElement`.

The likely popular Hacker News project the user remembers is Video.js v10. It is a useful
state/UI/media separation reference, but it is not the right renderer here: its official examples
still use `<video>`, and its August 2026 line remains a beta. The technically exact Hacker News
match is Mediabunny's own player without `<video>` or `<audio>`, although that post was not popular.

## Time-range picker

### Primary choice: `@vuepic/vue-datepicker`

The current v14 component supports a two-date range and requires the second endpoint when
`range.partialRange` is false. Its time configuration supports seconds, 24-hour display, time
increments, and an inline time picker
([range configuration](https://vue3datepicker.com/props/modes-configuration/),
[time configuration](https://vue3datepicker.com/props/time-picker-configuration/)). The model can
emit timestamps or ISO strings; a date range is represented as a two-value array
([model configuration](https://vue3datepicker.com/props/general-configuration/)). The dedicated
timezone API uses `TZDate` from `@date-fns/tz` and can display and emit values in UTC
([timezone documentation](https://vue3datepicker.com/props/timezone/)).

A suitable initial contract is:

```vue
<VueDatePicker
  v-model="range"
  :range="{ partialRange: false }"
  :time-config="{ enableSeconds: true, is24: true }"
  model-type="timestamp"
  timezone="utc"
/>
```

Treat the component value as input only. Normalize it at the API boundary into explicit
`from`/`to` timestamps, and define the server filter as a half-open interval (`from <= started_at <
to`). This avoids letting a local browser timezone or an inclusive end instant leak into the
artifact query contract.

The component ships its own layout CSS, but its colors, dimensions, radii, and transitions are
officially exposed as CSS variables
([theming documentation](https://vue3datepicker.com/customization/theming/)). That fits the
repository's styling rule: use UnoCSS for the surrounding filter layout and keep a small scoped
override only for third-party internals.

### Alternative: Reka UI

Reka UI's headless Date Range Picker is Vue-native, keyboard accessible, localized, and accepts
date-time values through `@internationalized/date`
([official Date Range Picker documentation](https://reka-ui.com/docs/components/date-range-picker)).
It would fit UnoCSS more naturally, but the date picker, date range picker, and time range field are
all explicitly marked **Alpha** in the current documentation. It also requires composing and
styling many primitives before it becomes a compact resource filter. Revisit it if complete visual
ownership becomes more important than implementation speed; do not make it the first dependency
for this pane.

## Virtua's Vue API

Virtua has a first-party Vue entry point, requires Vue 3.2 or newer, and documents this usage:
`import { VList } from 'virtua/vue'`
([official Vue example](https://github.com/inokawa/virtua/blob/dc92d9d6485df2578e10f5acb06875c69d1bda3b/README.md#L390-L413)).
`VList` accepts `data`, `itemSize`, `bufferSize`, scroll callbacks, cache restoration, and
`keepMounted`; it exposes `scrollToIndex`, `scrollTo`, and `scrollBy`
([Vue binding source](https://github.com/inokawa/virtua/blob/dc92d9d6485df2578e10f5acb06875c69d1bda3b/src/vue/VList.tsx#L23-L39),
[imperative handle](https://github.com/inokawa/virtua/blob/dc92d9d6485df2578e10f5acb06875c69d1bda3b/src/vue/VList.tsx#L74-L93)).

For the resource browser:

```vue
<VList
  :data="filteredReplays"
  :item-size="64"
  class="h-full min-h-0"
>
  <template #default="{ item }">
    <ReplayResourceRow :key="item.id" :replay="item" />
  </template>
</VList>
```

The single-root `key` matters because the Vue adapter extracts that key for its internal list item
and otherwise falls back to the array index
([key selection](https://github.com/inokawa/virtua/blob/dc92d9d6485df2578e10f5acb06875c69d1bda3b/src/vue/utils.ts#L7-L16)).
Use a fixed `itemSize` while every result row has the same height; this removes measurement
uncertainty during large filter changes. Let Virtua own the scroll container rather than placing a
second `overflow-y-auto` container around it. Its `VList` root already uses full width/height and
scroll overflow
([root layout](https://github.com/inokawa/virtua/blob/dc92d9d6485df2578e10f5acb06875c69d1bda3b/src/vue/VList.tsx#L95-L123)).

Filtering itself should remain a computed transformation of the API result. Do not mount all rows
and hide unmatched ones with CSS; pass only the filtered array to `VList`. Selection state should
be keyed by replay ID outside row components, because virtualized rows are expected to unmount.

## Media preview architecture

### Why the Mediabunny example is the production reference

Mediabunny provides efficient seeking, on-demand reads, decoded video samples, Canvas output, and
Web Audio buffers. `CanvasSink` supports `getCanvas`, contiguous iteration, and sparse timestamps;
its canvas pool bounds framebuffer allocation and is specifically recommended when only a few
frames remain live
([media sinks guide](https://mediabunny.dev/guide/media-sinks)). Sparse
`samplesAtTimestamps`/`canvasesAtTimestamps` avoids decoding the same packets repeatedly, which is
the appropriate primitive for preview thumbnails.

The maintained advanced media-player example already establishes the important runtime behavior:

- it creates a network `UrlSource`, probes primary tracks and codec decodability, then creates a
  `CanvasSink` with a pool of two and an `AudioBufferSink`
  ([source and sinks](https://github.com/Vanilagy/mediabunny/blob/7a871cec4929f03a44620f64fa9363a199f4c70a/examples/media-player/media-player.ts#L107-L199));
- source changes and seeks invalidate stale async work and dispose prior iterators; the renderer
  keeps the current and next Canvas frame and advances them from `requestAnimationFrame`
  ([video iterator and renderer](https://github.com/Vanilagy/mediabunny/blob/7a871cec4929f03a44620f64fa9363a199f4c70a/examples/media-player/media-player.ts#L280-L366));
- short audio buffers are scheduled through Web Audio, and `AudioContext.currentTime` is the A/V
  master clock even for video-only files
  ([audio and playback clock](https://github.com/Vanilagy/mediabunny/blob/7a871cec4929f03a44620f64fa9363a199f4c70a/examples/media-player/media-player.ts#L370-L448));
- seeking pauses playback, moves the base time, rebuilds the video iterator at the requested
  timestamp, and resumes only after the new frame is ready
  ([seek behavior](https://github.com/Vanilagy/mediabunny/blob/7a871cec4929f03a44620f64fa9363a199f4c70a/examples/media-player/media-player.ts#L478-L490)).

This should become an application-owned `MediaPreviewSession`, not code embedded in a Vue
component. The session owns the `Input`, sinks, iterators, queued audio nodes, async generation ID,
clock base, and cleanup. A Vue component owns only the visible Canvas, resize observation, controls,
and translating session events into editor state. On source change or unmount it must return async
iterators, stop queued audio nodes, and dispose the `Input`.

Keep the timeline's canonical integer tick as application state. Convert ticks to Mediabunny's
seconds only at the session boundary. During playback, use the audio clock to render both the
preview frame and the timeline playhead. Avoid committing a large Pinia graph on every animation
frame: update the hot playhead/render path directly, and publish throttled state for ordinary Vue
labels and persistence.

For thumbnails, create a separate task over `CanvasSink.canvasesAtTimestamps()` and cancel it when
the selected resource changes. A pooled playback Canvas must not also be retained as an arbitrary
thumbnail cache because pooled canvases are reused. Copy completed thumbnails to `ImageBitmap` or a
dedicated small Canvas, and impose an explicit cache budget.

### Network and Go static-serving requirements

`UrlSource` performs optimized, prefetched random reads and supports bounded parallel requests. Its
official documentation warns about CORS for cross-origin URLs and requires disposal of the owning
`Input` to cancel work
([reading media files](https://mediabunny.dev/guide/reading-media-files)). Prefer same-origin asset
URLs from the gRPC Gateway response.

The Go asset handler must preserve:

- a stable content length and media content type;
- byte-range requests with `206 Partial Content`, `Content-Range`, and validators;
- containment checks so a client-supplied replay ID or path cannot escape the configured artifact
  root;
- immutable or revisioned URLs when a rendered file is complete.

Go's `http.ServeContent` already handles Range requests when given a seekable source and modification
time ([standard-library contract](https://pkg.go.dev/net/http#ServeContent)); HTTP range semantics are
defined by RFC 9110 ([Range Requests](https://www.rfc-editor.org/rfc/rfc9110.html#name-range-requests)).
Do not wrap the media endpoint in response middleware that buffers or recompresses the whole file.

### What not to adopt now

The most likely “recent popular Hacker News player” is
[Video.js v10](https://news.ycombinator.com/item?id=47506713), whose March 2026 post received 648
points. Its rewrite cleanly separates State, UI, and Media, but its official HTML and React examples
still render a native `<video>` element, and the maintainers describe v10 as API-unstable beta
([official v10 beta announcement](https://videojs.org/blog/videojs-v10-beta-hello-world-again)).
It is therefore an architecture reference, not a dependency for this editor preview.

The exact technical match is the smaller July 2025 Show HN post,
[“Self-made web media player without `<video>` or `<audio>`”](https://news.ycombinator.com/item?id=44602379),
which links to the Mediabunny advanced example and explicitly targets accurate editor-style seeking
and direct frame access. It is an example, not an embeddable player package.

Omakase Player is aimed at professional frame-accurate review, but its own specification says the
core player is built on HTML5 and Media Source Extensions
([official product description](https://player.byomakase.org/)). Adopting it would introduce a
second media and timeline model rather than wrap Mediabunny. Media Chrome and player.style are also
primarily control surfaces around media elements. They can be revisited later if a standard media
adapter is built, but the dashboard already has Vue playback controls, so that bridge has no value
in the first slice.

Mediabunny itself is MPL-2.0. Depending on the package does not require copying its player example.
Implement the session from its public APIs and behavior, and keep the repository-required pinned
`NOTICE` reference near any code materially derived from the example. If implementation text is
copied rather than independently written, review MPL file-level obligations before merging.

## First-slice acceptance checks

The initial preview/browser integration should prove these behaviors before adding waveform,
multi-angle, or advanced player UI:

1. Filter a large replay list by server, player, and a UTC time range without mounting all rows.
2. Selecting a replay cancels the prior preview session and cannot display a late frame from the
   previous source.
3. Seek to arbitrary timeline ticks and display the corresponding decoded frame.
4. Play audio and video from the AudioContext clock while the timeline playhead follows the same
   tick conversion.
5. Repeated seek and source switching leave bounded Canvas/decoder/audio allocations.
6. Remote preview issues byte-range requests instead of downloading the whole rendered file.
7. Dockview resizing redraws the visible Canvas at the current device-pixel ratio without
   reconstructing the media session on every resize notification.
