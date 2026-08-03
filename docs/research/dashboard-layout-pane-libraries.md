# Dashboard layout and pane libraries

Research snapshot: 2026-08-03. This section evaluates Vue-native or explicitly Vue-supported options against a video-editor workspace: dockable panels, tabs, split resizing, optional floating panes, persistence, and reliable resize delivery to a Canvas timeline.

## Vue-first candidates

### Recommendation

Use **`dockview-vue` for the first integration spike**. Its model is the closest to Final Cut Pro, Resolve, or an IDE: a panel can become a tab, move into another group, split a group at an edge, float over the workspace, or open in another browser window. The same API serializes and restores the resulting layout. These are library primitives rather than application-specific conventions ([Dockview introduction](https://dockview.dev/docs/overview/introduction/), [core concepts](https://dockview.dev/docs/core/overview/), [saving](https://dockview.dev/docs/core/state/save/), [loading](https://dockview.dev/docs/core/state/load/)).

Keep the initial spike deliberately narrow:

1. Register the timeline, preview, inspector, and media browser as Vue panels.
2. Enable tab, split, and in-page floating-group operations; postpone cross-window popouts.
3. Persist only Dockview's layout JSON, versioned separately from editor/domain state.
4. Verify that a hidden-then-reactivated timeline and a continuously resized timeline both redraw at the correct device-pixel ratio.

Use **GridStack only if the intended product is instead a dashboard of rectangular cards on a cell grid**. It is a strong, actively maintained grid arranger, but tabs and edge-docking are not its abstraction. Muuri and the Vue grid-layout forks are weaker matches for an editor workbench.

### Capability comparison

| Candidate | Vue status and maintenance | Move / resize model | Tabs / dock / float | Persistence | Assessment |
| --- | --- | --- | --- | --- | --- |
| [`dockview-vue`](https://www.npmjs.com/package/dockview-vue) | First-party Vue 3 binding; v7.0.4 requires Vue `^3.4.0`; v7.0.4 released 2026-07-22 and the repository remained active in August 2026. MIT for `dockview-vue` and the open-source core; a separate `dockview-enterprise` package has a commercial licence ([v7.0.4 package](https://github.com/mathuo/dockview/blob/08097bd22495af8db171698355dffde93b9f5a88/packages/dockview-vue/package.json#L1-L92)). | Resizable split groups and drag/drop of panels or groups. | Yes: tabs, split/edge docking, floating groups and browser popout windows are in the open-source edition ([feature overview](https://dockview.dev/docs/overview/introduction/)). | `api.toJSON()` / `api.fromJSON()` plus layout-change events. | **Best match; spike first.** |
| [`gridstack`](https://www.npmjs.com/package/gridstack) Vue wrapper | GridStack v13 added an official Vue wrapper in July 2026. It uses Composition API, provide/inject and Teleport; Vue component instances survive cross-grid reparenting ([official Vue README](https://github.com/gridstack/gridstack.js/blob/c1b2246553ca4b849169eded086461ff48a17581/vue/README.md#L1-L108), [13.0 changelog](https://github.com/gridstack/gridstack.js/blob/c1b2246553ca4b849169eded086461ff48a17581/doc/CHANGES.md#L158-L174)). MIT and actively maintained. | Drag and resize rectangular widgets on a column/row grid; responsive columns, collision/compaction, nested grids, cross-grid moves. | No editor-style tabs, drop-to-split group tree, or floating windows. Its `float` option controls grid compaction; it is not a desktop floating pane. | Core `save()` / `load()`; Vue wrapper adds widget-state serializers ([official Vue README](https://github.com/gridstack/gridstack.js/blob/c1b2246553ca4b849169eded086461ff48a17581/vue/README.md#L54-L68)). | Strong dashboard alternative, not the first choice for an NLE workspace. |
| [`vue-ts-responsive-grid-layout`](https://www.npmjs.com/package/vue-ts-responsive-grid-layout) | Vue 3 + TypeScript fork; v2.0.1 released 2026-07-21; peer dependency Vue `^3.0.0`; MIT ([v2.0.1 package](https://github.com/gwinnem/vue-responsive-grid-layout/blob/f80681dfd6545be44ba35228db80d3cafa2dba09/package.json#L1-L24)). Currently active, unlike the original. | React-grid-layout-like draggable/resizable responsive cell grid. Supports serialization and several resize handles. | No docking groups, tabs, or floating/popout pane model. | The layout array is application-owned and serializable. | Viable small Vue-only grid, but GridStack has a broader engine and now an official Vue wrapper. |
| [`vue-grid-layout`](https://www.npmjs.com/package/vue-grid-layout) | Original Vue component; latest v2.4.0 was published 2022-08-03 and its repository's last push was 2024-05-09. MIT. Its stable package predates first-class Vue 3 support. | Draggable/resizable responsive cell grid. | No docking, tabs, or floating windows. | Serializable layout array. | Do not start a new Vue 3 integration on it; prefer the maintained Vue 3 fork or GridStack. |
| [`muuri`](https://www.npmjs.com/package/muuri) with `vuuri` / `vuuri-unleashed` | Muuri itself is framework-neutral MIT; latest 0.9.5 dates to 2021. Vue wrappers are third-party rather than maintained by Muuri: `vuuri` 0.4.6 and `vuuri-unleashed` 0.5.2 were last published in late 2024. | Excellent animated packing, ordering, filtering, cross-grid sending and drag autoscroll; items may have different dimensions ([official site](https://muuri.dev/), [official API](https://docs.muuri.dev/)). It does **not** supply pane resize handles. | No tabs, split tree, docking overlay, floating windows, or workbench semantics. | Must serialize application item order/grid ownership yourself. | Keep for sortable card/masonry surfaces, not the dashboard shell. |

### Dockview details and integration risks

`DockviewVue` receives a component registry and emits `ready` after mounting; panels are then added through `event.api`. The official Vue quickstart imports `dockview-vue/dist/styles/dockview.css` and gives the root an explicit width and height ([Vue quickstart](https://dockview.dev/docs/overview/quickstart/?framework=vue)). Its binding also deliberately stores the imperative Dockview API with Vue `markRaw`, avoiding deep proxying of a non-Vue object ([binding source](https://github.com/mathuo/dockview/blob/08097bd22495af8db171698355dffde93b9f5a88/packages/dockview-vue/src/dockview/dockview.vue#L361-L397)). Application code should follow that ownership boundary: Dockview owns layout geometry; Pinia owns editor/domain state.

Dockview uses `ResizeObserver` by default. It also supports `disableAutoResizing` plus explicit `api.layout(width, height)` when its host does not generate usable observer notifications ([sizing documentation](https://dockview.dev/docs/core/sizing/)). For the timeline panel:

- continue observing the panel's own content element rather than deriving Canvas size from the global window;
- redraw during `ResizeObserver` updates, preferably coalesced to one animation frame;
- redraw again when `panel.api.onDidVisibilityChange` makes a tab visible, because a hidden Canvas can have measured as zero-sized;
- do not persist the Canvas bitmap or timeline domain state inside Dockview's layout JSON.

Dockview is browser/DOM infrastructure, so SSR should render a stable shell and instantiate it only on the client. Popout windows add further browser constraints: popup blocking, same-origin/CSP stylesheet handling, and routing editor state into another `Window`. None is needed to validate in-page docking, so popouts should be a later spike.

Licensing needs one precise boundary. The `dockview-vue`, `dockview`, and `dockview-core` v7.0.4 packages declare MIT. The same monorepo now contains `dockview-enterprise`, which is explicitly proprietary. The first spike should depend only on `dockview-vue` and should not import enterprise entry points.

### GridStack details and integration risks

GridStack is the best comparison if “随意调整 layout” means moving and resizing dashboard tiles rather than rearranging an editor workbench. It supplies responsive column rules, nested grids, drag handles, collision behavior, cross-grid moves, `save()` / `load()`, and many resize/drag events. The new official Vue wrapper renders registered components and uses Vue Teleport so moving a widget between grids does not destroy and recreate it ([official Vue README](https://github.com/gridstack/gridstack.js/blob/c1b2246553ca4b849169eded086461ff48a17581/vue/README.md#L70-L80)). That is useful for expensive Canvas/WebGL children.

Two cautions apply as of this snapshot:

- the Vue wrapper is only weeks old; it was introduced in v13.0.0, and v13.0.1 immediately fixed missing Vue files in the package;
- npm's `latest` tag currently resolves to 13.0.2 even though 13.1.2 is published. A spike should pin an explicit tested version rather than accept `latest`.

GridStack initialization is DOM-dependent. Its older Vue integration guide explicitly waits until Vue has rendered before initializing the grid ([official Vue demo note](https://gridstackjs.com/demo/vue3js.html)); the v13 wrapper performs initialization on mount. Treat it as client-only for SSR. For a Canvas child, consume `resize` for live feedback only if needed, then perform the authoritative backing-store resize on `resizestop`; otherwise continuous DPR-sized Canvas reallocations can make pane dragging janky. The wrapper exposes `resize`, `resizestart`, and `resizestop` events ([wrapper source](https://github.com/gridstack/gridstack.js/blob/c1b2246553ca4b849169eded086461ff48a17581/vue/projects/lib/src/gridstack.ts#L234-L248)).

### Why Muuri is not a pane manager

Muuri remains a good answer to “make cards pack, sort, animate, and move between grids.” It includes drag autoscroll and permits custom layout functions. It is not a replacement for Splitpanes or Dockview: item resizing, splitter constraints, tab stacks, docking targets, focus/visibility lifecycle, and layout persistence would all remain application code. Adding a Vue wrapper does not change that abstraction, and the available wrappers lag current Vue/tooling releases. Using Muuri here would recreate the hard pane-manager behavior around a sophisticated sorting engine.

### Decision rule

- Choose **Dockview Vue** when panels should behave like an editor/IDE workspace.
- Choose **GridStack Vue** when panels should behave like dashboard widgets on a responsive grid.
- Choose **Muuri** only inside a panel for masonry/sortable cards.
- Keep **Splitpanes** if only one fixed split hierarchy needs resize; replacing it has value only once runtime docking/rearrangement is a real requirement.

## React and framework-neutral references

| Candidate | What it demonstrates | Why it is not the first Vue integration |
| --- | --- | --- |
| [`flexlayout-react`](https://github.com/caplin/FlexLayout) | The broadest React workbench reference: splitters, tabs/tabsets, edge docking, border tabsets, maximize, popout windows, nested submodels, and JSON model/actions. MIT and actively released. | Its model and view layers cross-import each other and its panel factory returns React components. Treat its behavior as a benchmark, not as a reusable core or React island inside this Vue application. |
| [`react-mosaic-component`](https://github.com/nomcopter/react-mosaic) | A compact serializable split/tabs tree and controlled update model. The public tree types are small enough to inform an owned framework-neutral model ([layout types](https://github.com/nomcopter/react-mosaic/blob/3991eec660ce719474f9c9c5e165932ac398c37f/libs/react-mosaic-component/src/lib/types.ts#L60-L105)). | Pointer and drop interaction is tied to React DnD. Borrow the model shape if an owned renderer eventually becomes necessary; do not port the React component layer now. |
| [`react-grid-layout`](https://github.com/react-grid-layout/react-grid-layout) | Mature React reference for responsive, draggable/resizable dashboard widgets, collision, packing, overlap, and serialization. | Like GridStack, it models `x/y/w/h` cells rather than tabs and a docking tree. Its renderer is React-specific even though some layout utilities are separately exported. |
| [`Allotment`](https://github.com/johnwalley/allotment) | A polished React split-view based on VS Code's SplitView, with min/max/preferred sizes, snapping, nesting, and visibility. | It still has no pane rearrangement, tabs, or edge docking. Replacing Splitpanes with it would not unlock the requested capability. |
| [`@lumino/widgets` DockPanel](https://lumino.readthedocs.io/en/stable/api/modules/widgets.DockPanel.html) | Battle-tested JupyterLab-style tabs, split docking, drop overlays, and precise widget resize messages. It is active and BSD-3-Clause. | Lumino owns a Widget/message-loop lifecycle. Vue integration must bridge attach/detach/dispose and encode widget instances into stable pane IDs for JSON persistence. This is a strong low-level fallback, but substantially more integration work than `dockview-vue`. |
| [`golden-layout`](https://golden-layout.github.io/golden-layout/) | Framework-neutral IDE docking and browser popouts. Its Virtual Components mode explicitly supports keeping a Vue component tree outside Golden Layout's DOM ownership ([binding modes](https://golden-layout.github.io/golden-layout/binding-components/)). | Virtual binding requires application-owned bind/unbind, geometry, visibility, and z-index plumbing, while the npm package has not released since 2022. Keep it as an API reference, not a new production dependency. |

## Fit with the current dashboard

Dockview should be an **outer workbench shell**, not a replacement for every internal split. A useful first layout is:

- preview / player;
- timeline editor;
- media or project browser;
- inspector / properties.

Keep `TimelineTrackHeaders` and `TimelineCanvas` together inside the existing `TimelineEditor` panel. They share vertical scroll state, virtualized track geometry, and a sticky Canvas viewport; turning them into independently dockable panels would create a synchronization protocol without a user-facing benefit. The internal Splitpanes divider can remain responsible for the track-header width.

Use Dockview's `renderer: 'always'` mode for the timeline panel during the spike. Dockview documents that the default `onlyWhenVisible` mode removes hidden panel DOM, producing zero-sized measurements and inactive `ResizeObserver`s, while `always` preserves DOM-specific state such as scroll position ([panel rendering modes](https://dockview.dev/docs/core/panels/rendering/)). The timeline should still pause playback/redraw work while hidden via `panel.api.onDidVisibilityChange`, then force a size sync and redraw when it becomes visible.

Persist the Dockview JSON as versioned workspace preference state, separately from Pinia episode/editor state. Dockview owns pane geometry and visibility; Pinia owns media, selection, playback, and edits. This separation also lets a corrupt or obsolete layout fall back to a known default without touching the recording domain state.
