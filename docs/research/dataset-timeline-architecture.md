# Dataset timeline editor architecture research

Research date: 2026-08-01

## Outcome

The dashboard should not embed a complete browser video editor or model recorder data as video
clips. Its timeline is a projection and editor over immutable recorder plays. The first useful
architecture is:

```text
Artifacts V1 / future API DTOs
  -> dataset-domain (Play references, Episode, Segment, Label, Group)
  -> timeline-model (tick ranges, projection, selection, commands, indexes)
  -> timeline-canvas (layout, rendering, hit testing, gestures)
  -> timeline-vue (Vue lifecycle bridge and surrounding DOM controls)
  -> apps/dashboard (routes and feature composition)
```

Use integer `server_tick` as the authoritative time coordinate. Keep source ranges separate from
episode placement ranges. Treat Canvas Timeline as the strongest architectural reference, not yet
as a dependency. Borrow the framework-neutral layout/hit-test/draw split from AiCut, interaction
transactions from Canvas Timeline and Waveform Playlist, and Vue composition ideas from
vue-clip-track. WebAV is only relevant later if browser-side media preview or export becomes a
requirement. Moveable is only relevant to spatial overlays in a preview, not to the timeline.

This report is intentionally a design investigation. It does not define a new persisted dataset
contract, connect the dashboard to a backend, or change Artifacts V1.

## Evidence base

The following repositories were cloned locally and inspected at fixed revisions:

| Repository | Revision | Relevant role | Adoption assessment |
| --- | --- | --- | --- |
| [`caohongz/vue-clip-track`](https://github.com/caohongz/vue-clip-track/tree/043d038a2e615910b700f8699269f3460e3bc6ff) | `043d038a` | Vue component composition and interaction checklist | Read for ideas; do not depend on it |
| [`ipmotionmc/AiCut`](https://github.com/ipmotionmc/AiCut/tree/fdb41eda13dfa69b4f4945e00bd8bcd623afbe8b) | `fdb41eda` | Framework-free Canvas timeline and thin Vue wrapper | Extract patterns; do not pull the complete package |
| [`techsquidtv/canvas-timeline`](https://github.com/techsquidtv/canvas-timeline/tree/1536a2dbc54e3a333ace360894a2e4508b295cf1) | `1536a2db` | Deep timeline core, edit commands, Canvas renderer and worker | Best spike candidate, but still young |
| [`daybrush/moveable`](https://github.com/daybrush/moveable/tree/75069102f30c88cd89ecaaa8ca7e5f7434e54807) | `75069102` | DOM/SVG spatial transform handles | Optional preview tool only |
| [`WebAV-Tech/WebAV`](https://github.com/WebAV-Tech/WebAV/tree/42dd6fd65ccbda39c729265c14aab56ac4d28235) | `42dd6fd6` | WebCodecs decode, composition and export primitives | Future media engine, not a timeline |
| [`naomiaro/waveform-playlist`](https://github.com/naomiaro/waveform-playlist/tree/fb81b68d66f678ef2489a93655841f8e7add61c0) | `fb81b68d` | Headless engine, playback adapter, transactions and chunked Canvas | Borrow engine and virtualization patterns |

All six repositories were clean at the recorded revision after cloning. This is source-level
research, not a comparison based on screenshots or README feature lists.

## What the recorder actually produces

The current model has two independent stages connected by files: the mod records one primitive
capture for one player connection, and explicit processors later derive actions, scenes and
renders. The recorder does not own dataset assembly
([Terms and Concepts](../TERMS_AND_CONCEPTS.md#system-model)).

The canonical hierarchy is server -> player -> play. A **Play** is one join-to-disconnect player
connection, while `session_id` identifies one recorder process run and does not create a persisted
container ([Terms and Concepts](../TERMS_AND_CONCEPTS.md#identities-and-ordering)). Multiple players
or overlapping connections therefore produce independent plays. The future Episode must reference
plays; it must not reinterpret a session as a dataset directory.

### Time and ordering semantics

The durable alignment coordinate is `server_tick`. A connection-local `sequence` totally orders
all event records, while `apply_sequence` orders main-thread packet application
([events.proto](../../apis/proto/recorder-minecraft/artifacts/v1/events.proto#L23)). Replay time is a
separate local coordinate joined to server time through emitted timeline markers.

The mod increments the server tick at tick start, stamps packet arrival and application according
to the current tick phase, snapshots player/control state at tick end, and then sends a replay
timeline marker ([CaptureCoordinator.kt](../../mods/recorder-mod/src/main/kotlin/dev/mcdata/recorder/capture/CaptureCoordinator.kt#L68)). Packet application is observed immediately before the
handler body on the main thread
([PacketUtilsMixin.java](../../mods/recorder-mod/src/main/java/dev/mcdata/recorder/mixin/PacketUtilsMixin.java#L14)). The player snapshot records a state barrier so actions can be ordered between adjacent
authoritative state samples without trusting network arrival time.

Consequences for the editor:

- `server_tick` is the authoritative domain coordinate; seconds are presentation only.
- Ranges should be represented internally as half-open `[startTick, endTick)` intervals. Existing
  processor CLI boundaries are inclusive, so mapping at the API boundary must be explicit.
- Event `sequence` is a tie-breaker within a tick, not an alternate horizontal coordinate.
- Replay tick, render frame ordinal and wall-clock timestamp are attached coordinates, not the
  Episode's source of truth.
- A gap is real. The UI must not compress reconnect gaps or missing replay coverage invisibly.

### Current artifacts sample

The checked-in ignored artifact root is 277 MiB and contains one server, one player and two plays
from the same recorder session:

| Play | Metadata ticks | Event records | Derived outputs |
| --- | ---: | ---: | --- |
| `448823fe-8a3b-4e3c-b792-1964d7dc7662` | `184397..187069` | 15,493 | none |
| `63af3daf-27a7-4b0d-a225-ee909c34fd22` | `187136..187906` | 5,538 | 2,384 actions, Scene Store V2, 767 FPV frames |

Both plays have session `c70069ce-d0be-410e-8058-17682ae51408`, but are different connections with
a 67-tick metadata gap. This is a concrete reason not to make session the editable container.

The processed play's Scene Store has:

| Data | Rows |
| --- | ---: |
| frames | 767 |
| player states | 767 |
| entity versions | 22,315 |
| section versions | 15,079 |
| block-entity versions | 88 |
| content-addressed blobs | 29,148 |

The render has one frame per selected server tick from `187139` through `187905`. Scene versions
are time-bounded and spatially indexed; they are not reasonable DOM nodes. At overview zoom, the
timeline needs density summaries. At detail zoom, it can query visible ranges and show discrete
actions or versions.

All captures declare the known gaps `audio_not_extracted`, `particles_not_extracted`,
`lighting_not_persisted_in_scene_v2`, and `unopened_container_contents_may_be_unknown`. Missing
modalities and unknown world cells need explicit UI states; they must not appear as empty or zero.

### Current API state

No service proto, `google.api.http` annotation, grpc-gateway configuration, OpenAPI document or
HeyAPI configuration exists in the current worktree. The generated artifact messages are data
contracts, not a dashboard service. The proposed client boundary below is therefore deliberately
an interface plus in-memory fixtures; generated transport code should be added only when the
backend contract lands.

## Proposed domain language

The existing glossary explicitly lists the old “Dataset V1/V2 exports” and old dataset viewers as
obsolete ([Terms and Concepts](../TERMS_AND_CONCEPTS.md#obsolete-terms-and-components)). Reusing
those names without a new contract would create ambiguity. For the research prototype, use these
working terms:

| Term | Proposed meaning |
| --- | --- |
| Source Play | An immutable Artifacts V1 play and its optional derived outputs |
| Placement | A reference to a source play range positioned on an Episode timeline; never a copy or mutation of capture data |
| Player Track | A timeline projection grouping placements and observations for one stable player UUID |
| Episode | A curated aggregate that aligns one or more source-play placements |
| Segment | A named half-open Episode range selected for labelling, grouping or later derivation |
| Label Assignment | A typed label attached to a Segment or another addressable Episode entity |
| Group | A persistent relationship among typed Episode entities; not merely a multi-selection |
| Timeline Session | Ephemeral viewport, hover, selection, gesture and expansion state |

The important separation is source time versus episode time:

```ts
interface PlayPlacement {
  id: PlacementId
  play: PlayRef
  source: TickRange
  episodeStart: Tick
  alignment: 'preserve-server-tick' | 'append' | 'manual'
}
```

`preserve-server-tick` aligns simultaneous players naturally. `append` is useful for disconnected
material. `manual` is an explicit editorial offset. The source range never changes when the
placement moves.

### Decisions that must precede persistence

These are product/domain decisions, not implementation details:

1. Can one Episode cross server instances, or only sessions within one server instance?
2. May Segments overlap and nest? Labelling work usually needs overlap; an export format may not.
3. Does splitting a Segment preserve one semantic identity with parts, or create two new Segments?
4. Can a Group mix players, world ranges and labels, or only contain Segments?
5. Are labels versioned schema values or free-form tags?
6. Is an Episode placement allowed to retime data, or only translate its tick origin?
7. Which references survive source play relocation: canonical IDs only, paths, or both?

Do not create a persisted Episode proto until these invariants are resolved.

## Source-level component findings

### Canvas Timeline: strongest architecture reference

Canvas Timeline is split into framework-neutral core, renderer, media adapters, React bindings and
an aggregate package. That dependency direction is a close match for a future Vue adapter. Its
core separates timeline state, editing, geometry, snapping, history, keyframes and media sync; the
renderer is a distinct Canvas layer. This is substantially better than putting domain mutations in
Vue components.

Its most valuable primitive is `RationalTime`: an integer value and integer rate rather than a
floating second value
([time.ts](https://github.com/techsquidtv/canvas-timeline/blob/1536a2dbc54e3a333ace360894a2e4508b295cf1/packages/utils/src/time.ts#L1-L25)). Recorder can use the simpler canonical server-tick clock, but should preserve the same
integer and half-open-range discipline. Its editing API is also unusually useful: commands pass
through validate -> preview -> commit/cancel, and previews expose rejection reasons, affected
ranges and impacts
([editing.ts](https://github.com/techsquidtv/canvas-timeline/blob/1536a2dbc54e3a333ace360894a2e4508b295cf1/packages/core/src/engine/editing.ts#L87-L148)). That shape maps well to explaining which players, world spans or labels a Segment operation will
affect before it is saved.

Snapping is prepared once at gesture start, stored in a sorted typed-array index and queried with
binary search during pointer movement; applications may register custom providers
([snapping.ts](https://github.com/techsquidtv/canvas-timeline/blob/1536a2dbc54e3a333ace360894a2e4508b295cf1/packages/core/src/snapping.ts#L156-L207)). The dashboard should borrow the interaction-scoped index but keep integer ticks instead of
converting long captures to floating seconds.

It should still be treated as a spike candidate rather than selected immediately:

- the project is new and still on a `0.1.x` line;
- the shipped binding and even the renderer package depend on React 19, so Vue needs an owned
  lifecycle and render bridge;
- its clip/media model is richer than this viewer needs in some places and lacks recorder-specific
  entity/action/world projections in others;
- visible queries and hit tests still scan tracks/clips linearly; there is no general interval or
  spatial index;
- worker rendering sends a complete structured-cloned timeline state and performs a full redraw,
  while history stores full JSON snapshots; these paths do not fit very large captures;
- its standard pointer layer only exposes ordinary move/trim even though the headless command API
  names more NLE operations, and its current `slide` is not standard FCP slide-edit semantics;
- MPL-2.0 file-level copyleft requires a deliberate dependency/modification policy.

The spike should use it as an API and behavior benchmark. A small experiment may adapt recorder
projections to the public core without forking, but the existing React renderer, query/index,
history and worker protocol should not become production foundations. If recorder needs changes
inside library files, licensing and maintenance cost must be reviewed before proceeding.

#### How Canvas Timeline's core and renderer actually interact

The hot render path does not pass through React state. The core owns a mutable `TimelineState` and
publishes typed events. Most visual mutations emit a payload-free `render` event; playhead movement
also emits the new `RationalTime` through the narrower `playhead:scrub` event
([events.ts](https://github.com/techsquidtv/canvas-timeline/blob/1536a2dbc54e3a333ace360894a2e4508b295cf1/packages/core/src/events.ts#L213-L281)). The worker-backed renderer subscribes directly to those events:

```text
TimelineEngine mutation
  ├─ render ──────────> CanvasRenderer reads engine.getState()
  │                       └─ UPDATE_STATE + complete TimelineState
  │                            └─ Worker requestAnimationFrame
  │                                 └─ renderTimeline(...)
  └─ playhead:scrub ──> UPDATE_PLAYHEAD + RationalTime
```

The React `TimelineProvider` is a separate, slower bridge for DOM components. It shallow-copies the
top-level engine state after settled/selection/playback events and publishes that snapshot through
React context
([Provider.tsx](https://github.com/techsquidtv/canvas-timeline/blob/1536a2dbc54e3a333ace360894a2e4508b295cf1/packages/react/src/Provider.tsx#L24-L51),
[Provider.tsx](https://github.com/techsquidtv/canvas-timeline/blob/1536a2dbc54e3a333ace360894a2e4508b295cf1/packages/react/src/Provider.tsx#L105-L140)). Canvas redraws therefore do not require a React component-tree render.

`CanvasRenderer` performs five framework/lifecycle jobs rather than drawing itself:

1. It creates a pointer-transparent `HTMLCanvasElement` and sizes its bitmap for device pixel
   ratio.
2. It transfers that canvas to an `OffscreenCanvas` and starts a module Worker.
3. It posts `INIT`, then complete `UPDATE_STATE` messages after core `render` events.
4. It posts the much smaller `UPDATE_PLAYHEAD`, `RESIZE` and `UPDATE_OPTIONS` messages for those
   special cases.
5. It owns subscriptions, `ResizeObserver`, Worker errors/statistics and teardown.

This lifecycle is visible in
[`CanvasRenderer.tsx`](https://github.com/techsquidtv/canvas-timeline/blob/1536a2dbc54e3a333ace360894a2e4508b295cf1/packages/renderer/src/CanvasRenderer.tsx#L296-L449). The Worker keeps only the latest state, coalesces requests with one
`requestAnimationFrame`, then performs a complete draw
([worker.ts](https://github.com/techsquidtv/canvas-timeline/blob/1536a2dbc54e3a333ace360894a2e4508b295cf1/packages/renderer/src/worker.ts#L70-L166)).

The pure draw pass is simple and valuable. `renderTimeline()` resets/scales the context, then draws
background, ruler, borders, markers, drop feedback, tracks/clips, snap guides and in/out overlays in
a fixed order
([renderTimeline.ts](https://github.com/techsquidtv/canvas-timeline/blob/1536a2dbc54e3a333ace360894a2e4508b295cf1/packages/renderer/src/renderTimeline.ts#L24-L76)). It vertically culls tracks and horizontally rejects clips after computing their x range, but still
iterates every clip in each visible track
([tracks.ts](https://github.com/techsquidtv/canvas-timeline/blob/1536a2dbc54e3a333ace360894a2e4508b295cf1/packages/renderer/src/render/tracks.ts#L35-L121),
[clips.ts](https://github.com/techsquidtv/canvas-timeline/blob/1536a2dbc54e3a333ace360894a2e4508b295cf1/packages/renderer/src/render/clips.ts#L13-L68)).

Keyframe rendering reveals a useful boundary: keyframe geometry is prepared by core on the main
thread and passed as serializable geometry to the Worker, while ordinary clip geometry is derived
inside the renderer. Recorder should generalize this into a serializable render-frame contract for
all dense domain projections, not send complete Episode/domain state to the Worker.

#### The component layer is Canvas-first, not Canvas-only

Canvas Timeline deliberately uses a hybrid layer stack. Its basic demo is:

```text
Timeline.Root                         DOM: viewport measurement, wheel/pinch pan/zoom
  CanvasRenderer                     Canvas: ruler, tracks, clips, markers, feedback
  PlayheadArea / PlayheadGrabber     DOM: scrubbing and lightweight moving cursor
  TrackList + empty Track rows       DOM: row layout/header alignment
  ClipInteractionLayer              DOM: delegated pointer and keyboard interaction
  RangeSelector                     DOM: editable range handles
ViewportScrollbar                   DOM
```

The actual composition is shown in
[`BasicTimeline.tsx`](https://github.com/techsquidtv/canvas-timeline/blob/1536a2dbc54e3a333ace360894a2e4508b295cf1/apps/www/src/demos/basic-editor-surface/BasicTimeline.tsx#L9-L58). The canvas has `pointer-events: none`; one constant-size interaction layer asks core to hit-test the
pointer and creates only one DOM overlay for the hovered/selected/dragged clip
([ClipInteractionLayer.tsx](https://github.com/techsquidtv/canvas-timeline/blob/1536a2dbc54e3a333ace360894a2e4508b295cf1/packages/react/src/components/interactions/ClipInteractionLayer.tsx#L105-L258),
[ClipInteractionLayer.tsx](https://github.com/techsquidtv/canvas-timeline/blob/1536a2dbc54e3a333ace360894a2e4508b295cf1/packages/react/src/components/interactions/ClipInteractionLayer.tsx#L332-L469)). This is the component pattern recorder should reproduce in Vue: dense data stays on Canvas,
while focus, handles, menus, playhead and accessible controls remain DOM.

Geometry must have one owner. Canvas theme metrics, core hit testing and DOM overlay dimensions all
need identical ruler/track/edge values. Allowing a Vue component, Worker theme and interaction
layer to choose independent defaults would cause visible clips and pointer targets to drift.

#### Can the current renderer be used directly from Vue?

Not cleanly in its current published shape:

- `@techsquidtv/canvas-timeline-core` is framework-neutral and can be used from Vue.
- `@techsquidtv/canvas-timeline-renderer` has a React 19 peer dependency and imports the React
  provider directly
  ([package.json](https://github.com/techsquidtv/canvas-timeline/blob/1536a2dbc54e3a333ace360894a2e4508b295cf1/packages/renderer/package.json#L58-L65)).
- Its public entry exports the React `CanvasRenderer`, React `TimelineCanvasLayer` and React hook;
  it does not export the pure `renderTimeline()` or a framework-neutral Worker controller
  ([index.ts](https://github.com/techsquidtv/canvas-timeline/blob/1536a2dbc54e3a333ace360894a2e4508b295cf1/packages/renderer/src/index.ts#L1-L6)).
- The Worker URL is created inside the React component, so a Vue wrapper cannot configure the
  published Worker by importing a supported subpath.

Mounting a React island inside Vue would technically display it, but it would also require React
for the root, interaction, playhead and scrollbar layers and would create two UI lifecycles. That
is not a useful architecture here.

There are two credible routes:

1. **Upstream split:** extract `renderTimeline`, theme/render types, worker protocol and a
   `CanvasRendererController` into a zero-framework package. React and Vue components then become
   equally thin lifecycle adapters. This is the best route if upstream is willing to accept it.
2. **Recorder-owned renderer:** depend only on the framework-neutral core ideas/APIs and implement
   recorder-specific render primitives, visible-range queries and Worker protocol. This is the
   safer production route if our player/action/world lanes diverge from media clips.

Importing unpublished `dist` internals is not a third route: it would be version-fragile and still
leave the React package and Worker entry assumptions. Copying or modifying upstream renderer files
also activates the MPL-2.0 file-level obligations and requires pinned source notices under this
repository's contribution rules.

#### Recommended framework-neutral renderer boundary

The renderer should not receive Vue refs, generated API DTOs or the Episode domain model. It should
consume a compact, serializable `RenderFrame` produced from the current viewport:

```ts
type RenderPrimitive =
  | { kind: 'span'; track: number; start: number; end: number; style: number }
  | { kind: 'point'; track: number; at: number; glyph: number; style: number }
  | { kind: 'density'; track: number; values: Float32Array; style: number }
  | { kind: 'label-band'; start: number; end: number; label: string; style: number }

interface RenderFrame {
  revision: number
  viewport: RenderViewport
  tracks: readonly RenderTrack[]
  primitives: readonly RenderPrimitive[]
  selection: SelectionRenderState
  feedback: EditFeedback
}

interface TimelineRendererController {
  mount(canvas: HTMLCanvasElement): void
  render(frame: RenderFrame): void
  updatePlayhead(x: number): void
  resize(width: number, height: number, dpr: number): void
  setTheme(theme: TimelineCanvasTheme): void
  dispose(): void
}
```

Use viewport-relative integer ticks or already-projected numeric offsets in a render frame. Keep
the original `bigint` tick on semantic hit/domain references, but do not ask Canvas to convert a
huge absolute `int64` to floating point for every glyph. Transfer density/polyline typed-array
buffers when large. A frame should contain only visible/overscanned tracks and items; selection or
playhead-only changes should use small patch messages rather than cloning the complete projection.

The core-to-renderer event should eventually carry invalidation intent instead of only
`render: void`:

```ts
type RenderInvalidation =
  | { kind: 'viewport' }
  | { kind: 'content'; range?: TickRange; trackIds?: readonly TrackId[] }
  | { kind: 'selection'; ids: readonly TimelineEntityRef[] }
  | { kind: 'feedback' }
  | { kind: 'playhead'; tick: Tick }
```

This allows the projection/query layer to decide whether it needs a new visible frame, a small
patch or only a DOM playhead transform.

#### Thin Vue adapter

Vue should own the DOM lifecycle, not the pixels. The renderer/controller and engine are opaque
external instances stored in `shallowRef`; domain/projected state flows down as immutable props or
through a typed injected context, while semantic commands flow up as events.

```vue
<script setup lang="ts">
import { onMounted, onUnmounted, shallowRef, useTemplateRef, watch } from 'vue'

const props = defineProps<{
  frame: RenderFrame
  theme: TimelineCanvasTheme
}>()

const emit = defineEmits<{
  renderError: [error: Error]
}>()

const canvas = useTemplateRef<HTMLCanvasElement>('canvas')
const renderer = shallowRef<TimelineRendererController>()

onMounted(() => {
  const element = canvas.value
  if (!element)
    return

  const controller = createTimelineRenderer({ onError: error => emit('renderError', error) })
  controller.mount(element)
  controller.setTheme(props.theme)
  controller.render(props.frame)
  renderer.value = controller
})

watch(() => props.frame, frame => renderer.value?.render(frame))
watch(() => props.theme, theme => renderer.value?.setTheme(theme))

onUnmounted(() => {
  renderer.value?.dispose()
  renderer.value = undefined
})
</script>

<template>
  <canvas ref="canvas" class="timeline-canvas" aria-hidden="true" />
</template>
```

The actual feature should split responsibilities rather than grow this component:

| Vue unit | Single responsibility | Contract |
| --- | --- | --- |
| `TimelineSurface.vue` | Stack and size the timeline layers | receives Episode projection/session; emits commands |
| `TimelineCanvas.vue` | Mount/dispose the renderer controller | `frame`, `theme`; emits renderer diagnostics |
| `TimelineInteractionLayer.vue` | Pointer capture, hit testing and gesture state | emits semantic command intents; never paints dense items |
| `TimelinePlayhead.vue` | Scrub target and cheap transform updates | tick/x in; scrub intent out |
| `TimelineTrackHeaders.vue` | Visible player/world/annotation headers | visible track layouts in; collapse/select actions out |
| `TimelineA11yNavigator.vue` | Keyboard navigation and selected-item description | selection/focus model in; navigation intents out |
| `useTimelineRenderer.ts` | Renderer, Worker and observer lifecycle | opaque controller plus diagnostics |
| `useTimelineContext.ts` | Typed provide/inject for one editor instance | readonly snapshot plus explicit actions |

The Vue adapter should replace snapshot refs only on meaningful engine revisions; it should not
deep-proxy the engine, Canvas frame, typed arrays or every raw observation. This preserves the same
important property as Canvas Timeline's React implementation: Canvas redraw scheduling remains
imperative and does not cause a Vue component-tree update on every pointer move.

### AiCut: borrow layout, hit testing and interaction ghosts

AiCut's useful seam is its framework-free imperative Canvas timeline. It separates shared
time/pixel layout functions, semantic hit testing, stateless draw passes and pointer lifecycle.
The same coordinate functions drive draw and hit testing
([layout.ts](https://github.com/ipmotionmc/AiCut/blob/fdb41eda13dfa69b4f4945e00bd8bcd623afbe8b/packages/core/src/timeline/layout.ts#L117-L193)), while hit testing returns semantic targets with explicit priority
([hit.ts](https://github.com/ipmotionmc/AiCut/blob/fdb41eda13dfa69b4f4945e00bd8bcd623afbe8b/packages/core/src/timeline/hit.ts#L18-L82)).

Its best interaction choice is keeping `dragGhost` outside the committed project and applying the
mutation only at pointer-up
([timeline/index.ts](https://github.com/ipmotionmc/AiCut/blob/fdb41eda13dfa69b4f4945e00bd8bcd623afbe8b/packages/core/src/timeline/index.ts#L1206-L1272)). The dashboard should adopt that rule: pointer movement
must not mutate Episode state, create undo entries or call the backend.

Do not depend on the whole package. It couples the core to media/3D dependencies, supports only a
single selected clip, shares some timeline metrics through module globals, and publishes the
current packages to a private AWS CodeArtifact registry. The Vue component also treats project
props as initial values and exposes an imperative API rather than providing server-authoritative
controlled data flow.

### vue-clip-track: useful shell, unsuitable state model

The top-level `ToolsBar + Ruler + Tracks + ContextMenu` composition and renderer slots are a useful
Vue component checklist
([components/index.vue](https://github.com/caohongz/vue-clip-track/blob/043d038a2e615910b700f8699269f3460e3bc6ff/src/components/index.vue#L1-L105)). Its actual timeline mounts every track and every clip as Vue DOM
nodes, however
([Tracks/index.vue](https://github.com/caohongz/vue-clip-track/blob/043d038a2e615910b700f8699269f3460e3bc6ff/src/components/Tracks/index.vue#L11-L38)); the virtual-scroll composable is not used by production components.

Its persistent clip shape contains presentation/selection fields, while the Pinia store also owns
a separate selected-ID set. Export then mixes tracks with current time, zoom and snapping
preferences. This is the exact coupling the dashboard should avoid. Its time model is floating
seconds rounded to milliseconds, and its high-zoom ruler hardcodes 30 FPS. It cannot preserve
Minecraft tick semantics reliably.

Use it as a checklist for toolbar, ruler, headers, context menu, snap guides and autoscroll. Do not
use its global Pinia singleton or snapshot/export model.

### Waveform Playlist: headless engine and chunked Canvas

Waveform Playlist now has a framework-independent `PlaylistEngine` with an optional playback
adapter. It emits state snapshots, maintains structural and mixer revision counters, and wraps
continuous edits in begin/commit/abort transactions
([PlaylistEngine.ts](https://github.com/naomiaro/waveform-playlist/blob/fb81b68d66f678ef2489a93655841f8e7add61c0/packages/engine/src/PlaylistEngine.ts#L26-L171)). Its adapter boundary keeps audio scheduling out of the edit
model
([types.ts](https://github.com/naomiaro/waveform-playlist/blob/fb81b68d66f678ef2489a93655841f8e7add61c0/packages/engine/src/types.ts#L3-L69)).

Its React UI also splits very wide waveforms into mounted Canvas chunks and only keeps visible
chunks. That is a useful rendering strategy for long tracks, although the dashboard should index
tick intervals and generate zoom-dependent summaries rather than audio peaks.

Do not reuse its sample/seconds domain or audio clip rules. Borrow its transaction, revision and
adapter ideas.

### WebAV and Moveable: different layers

WebAV provides `IClip`, temporal/spatial Sprite properties, an OffscreenCanvas compositor and an
interactive AV canvas. It does not provide the NLE timeline needed here. Its microsecond media time
is appropriate inside a future playback/export adapter, but must not replace server ticks in the
Episode model.

Moveable transforms one or more actual DOM/SVG targets. Its group feature is a geometric bounding
box around DOM targets, not a persisted Episode Group. Creating invisible DOM guides for every
action/segment edge would defeat Canvas virtualization. Use Moveable only if a future world/frame
preview needs draggable boxes, crop regions or other spatial annotations.

## Recommended packages and dependency direction

Keep package boundaries deep enough to own real decisions, but do not create an interface for
every file before a second implementation exists:

```text
packages/
  dataset-domain/
    src/play.ts             # source identity and modality availability
    src/episode.ts          # Episode, Placement, Segment, Label, Group
    src/commands.ts         # create/resize/split/group/label operations
    src/validation.ts       # explicit invariants; no silent repair of committed data

  timeline-model/
    src/coordinate.ts       # TickRange and tick <-> viewport math
    src/projection.ts       # domain -> disposable tracks/intervals/markers
    src/interval-index.ts   # visible range queries
    src/selection.ts        # typed multi-selection, primary and anchor
    src/snapping.ts         # semantic snap candidate providers
    src/history.ts          # Episode command transactions only

  timeline-canvas/
    src/engine.ts           # imperative lifecycle and render scheduling
    src/layout.ts           # shared draw/hit-test geometry
    src/draw.ts             # ordered Canvas passes
    src/hit-test.ts         # pixels -> semantic TimelineHit union
    src/interaction.ts      # ephemeral gesture previews -> command intent
    src/renderers/          # player, action, world, segment and label projections

  timeline-vue/
    src/DatasetTimeline.vue
    src/TimelineToolbar.vue
    src/TrackHeaderColumn.vue
    src/TimelineInspector.vue
    src/useTimelineEngine.ts

apps/dashboard/
  src/features/datasets/
  src/features/episodes/
  src/features/labelling/
```

`dataset-client` should not be added until there are two real data sources. Initially, an
`EpisodeRepository` interface can live beside the feature with one in-memory fixture
implementation. When the OpenAPI contract arrives, add generated HeyAPI code behind a mapper; do
not leak generated DTOs into Canvas renderers or Vue component props.

The authoritative flow is one-way:

```text
transport DTO -> validated domain -> disposable projection -> Canvas
                                             ^                |
                                             | command        | semantic intent
                                             +----------------+

TimelineSession (viewport/hover/selection/gesture) never enters persisted Episode data.
```

### Core types

```ts
type Tick = bigint

interface TickRange {
  start: Tick
  end: Tick // exclusive
}

interface TimelineViewport {
  origin: Tick
  pixelsPerTick: number
  scrollX: number
  scrollY: number
  width: number
  height: number
}

type TimelineHit =
  | { kind: 'placement'; placementId: PlacementId }
  | { kind: 'segment-body'; segmentId: SegmentId }
  | { kind: 'segment-edge'; segmentId: SegmentId; edge: 'start' | 'end' }
  | { kind: 'label'; assignmentId: LabelAssignmentId }
  | { kind: 'action'; playId: PlayId; sequence: bigint }
  | { kind: 'world-version'; playId: PlayId; versionId: bigint }
  | { kind: 'empty'; trackId: TrackId; at: Tick }
```

Use `bigint` or a branded integer at the domain boundary because protobuf `int64` can exceed safe
JavaScript integer precision. The current samples fit in `number`, but the type must not encode an
accidental limit. Canvas conversion should subtract the viewport origin before converting the
small relative delta to `number`.

### Commands and interactions

The first command set should be narrow:

```ts
type EpisodeCommand =
  | { type: 'placement/add'; play: PlayRef; source: TickRange; at: Tick }
  | { type: 'placement/move'; placementId: PlacementId; to: Tick }
  | { type: 'segment/create'; range: TickRange }
  | { type: 'segment/resize'; segmentId: SegmentId; range: TickRange }
  | { type: 'segment/split'; segmentId: SegmentId; at: Tick }
  | { type: 'label/assign'; targetIds: SegmentId[]; labelId: LabelId }
  | { type: 'group/create'; members: TimelineEntityRef[] }
```

A pointer gesture creates an ephemeral interaction with original and preview values. Pointer-up
validates it and emits one command. The command reducer produces an inverse command or patch for
undo. Raw plays and projections are never copied into the history stack.

Snap candidates should be semantic and prioritized: play/coverage boundaries, segment edges,
playhead, actions, player intersections and world markers. Define the threshold in screen pixels
and convert it to ticks for the current zoom, so interaction feel stays stable.

## Rendering and scale strategy

A dense timeline needs both horizontal and vertical culling. Canvas alone is not a performance
strategy if every frame still scans 20,000 entity versions or millions of raw events.

Use three levels of detail:

| Zoom | Projection | Examples |
| --- | --- | --- |
| Overview | fixed-width density buckets | player activity, action kinds, entity churn, coverage/missing modality |
| Medium | merged spans and important markers | movement/action spans, intersections, segment/label overlays |
| Detail | visible raw records | packet-derived actions, player state changes, individual entity/version boundaries |

Projection work should be cacheable by source revision, visible range and zoom bucket. Move heavy
aggregation into a Worker once real data requires it; do not introduce a worker abstraction before
the first synchronous projection establishes the message shape.

Use DOM for toolbar, track headers, inspector, menus and accessible summaries. Use one or a small
number of Canvas surfaces for dense lanes and overlays. Keep semantic selection and focus outside
Canvas so equivalent keyboard commands and inspector actions remain available even when individual
markers are not DOM elements.

## Vue integration

The Vue adapter should be intentionally thin and use Composition API with `<script setup>`:

- create the imperative engine once in `onMounted` using a `shallowRef` canvas/engine handle;
- subscribe to semantic intents and session changes;
- watch stable projection/domain revisions, not deeply reactive interval arrays;
- call targeted engine setters instead of recreating the engine;
- cancel animation frames, observers and subscriptions in `onUnmounted`;
- expose only necessary focus/fit/scroll commands, not the entire engine instance.

The component contract should be controlled for domain data and separate session events:

```vue
<DatasetTimeline
  :episode="episodeDraft"
  :projection="projection"
  :session="timelineSession"
  @command="dispatchEpisodeCommand"
  @session-change="patchTimelineSession"
/>
```

Do not put source plays, Episode documents or the Canvas engine in a global singleton store. A
feature-scoped composable may own one editor instance; user preferences such as snap enablement or
default track heights may be persisted separately.

## Histoire visualization plan

Histoire is appropriate for the timeline because it supports Vue `.story.vue` files, named
variants, full-width single layouts, interactive controls and global setup files. The official
configuration reference documents a browser/server split setup file, which is useful if a Canvas
or Worker dependency is browser-only
([Histoire configuration](https://histoire.dev/reference/config)).

Follow AIRI's useful conventions: a dedicated package-level config, `HstVue()`, hash routing,
global browser/server setup, explicit tree groups and `story:dev`/`story:build` scripts. Do not copy
its Cloudflare vendor-chunk workaround unless this project has the same deployment constraint.

Stories must use deterministic in-memory fixtures, not read `artifacts/` or call gRPC. Derive
fixture shapes and counts from real captures, but keep them small or generated on demand:

```ts
createTimelineFixture({
  durationTicks: 36_000n,
  players: 8,
  actionDensity: 'bursty',
  sceneVersionCount: 100_000,
  reconnectGaps: true,
  missingModalities: ['audio', 'lighting'],
})
```

Recommended story groups and variants:

| Group | Stories |
| --- | --- |
| Primitives | ruler zoom levels, track header, density lane, action markers, coverage gaps |
| Interaction | create/resize/split Segment, snapping, marquee/multi-select, grouping, undo transaction |
| Composition | one Play, reconnect gap, simultaneous players, manual placement, Episode with overlapping Segments |
| Data states | loading, empty, incomplete Play, unprocessed modalities, sensitive Scene data, stale revision conflict |
| Scale | 30-minute Play, one million generated observations, many players, extreme zoom in/out |
| Accessibility | keyboard navigation, focus handoff, accessible selection summary, reduced motion |

Use stable variant IDs because Histoire URLs otherwise depend on variant order. The official Vue
story guide documents `Story`, `Variant` and full-width layouts
([Histoire stories](https://histoire.dev/guide/vue3/stories.html)); state controls can vary zoom,
density, player count and missing modalities without talking to a server
([Histoire controls](https://histoire.dev/guide/vue3/controls)).

The first vertical story should be `EpisodeTimeline / two reconnecting plays`: it can reproduce the
current sample's two connections and 67-tick gap, add synthetic simultaneous players, and exercise
selection, Segment creation and missing-derived-output presentation in one reviewable surface.

## Suggested implementation sequence

1. Resolve the open Episode/Segment/Group semantics and write a small domain decision document.
2. Add deterministic fixture builders and Histoire setup before backend integration.
3. Implement tick/range primitives, viewport math and a visible interval index with unit tests.
4. Build read-only ruler, player/activity tracks, coverage gaps and playhead in Canvas.
5. Add semantic hit testing, selection and keyboard navigation.
6. Add Segment create/resize/split as preview -> command transactions with undo.
7. Benchmark Canvas Timeline's core against the same projection and fixtures; compare command API,
   interaction correctness, long-range performance and MPL obligations.
8. Keep the owned tick/query/render model unless that benchmark proves a narrow unmodified core
   dependency is both useful and maintainable.
9. Add the generated client and repository mapper after the grpc-gateway/OpenAPI surface exists.
10. Add WebAV or Moveable only when concrete media-preview or spatial-annotation requirements
    appear.

## Recommendation

Build the first dashboard timeline as a dataset-specific, tick-native Canvas projection with a thin
Vue shell and Histoire fixtures. Do not directly adopt vue-clip-track, AiCut, Canvas Timeline,
WebAV, Moveable or Waveform Playlist. Run a focused Canvas Timeline benchmark after the domain and
projection types exist; it is the closest architectural reference, but its current React binding,
linear queries, full-state worker transfer and snapshot history are the wrong production data path
for recorder-scale captures.

The next decision is not which pixels to draw. It is the persisted meaning of Episode placement,
Segment overlap/split and Group membership. Once those are explicit, the renderer can remain a
replaceable detail instead of becoming the dataset model.
