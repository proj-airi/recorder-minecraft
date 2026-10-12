# Cases

Instances of each `ux` rule in this repository. ❌ is a violation, ✅ is a case worth copying. Each entry gives the baseline it was observed at; line numbers move, so re-check before citing.

Paths are relative to `apps/dashboard/src/` unless they start with `internal/` or `packages/`.

Seeded from the first audit of the editor workspace (`origin/main @ ac9b4e5`, 2026-10-12; L1 and L2). Updated after #21 (`origin/main @ a550069`, 2026-10-12; L1, L2, and L3 on a two-player Airicraft world session). Entries marked `ac9b4e5` were not re-checked at `a550069`. Track selection and reordering were verified on `neko/feat/timeline-track-select` (based on `a09b839`, 2026-10-12; L1, L2, and L3 on the same session).

## no-unknown-as-absent

- ❌ `ac9b4e5`: when the world session fails to load, the visibility lane keeps a placeholder whose load returns `[]`; the error never appears. `features/extensions/evidence/providers.ts` (`visibilityDescriptors` placeholder)
- ✅ `a550069`: data tracks have a `missing` state separate from `empty`. A provider throws `DataSourceMissingError(<file>)`, the header shows "missing", and the lane reads "<file> missing · unknown, not empty". Applied to `world-events.jsonl` and to `perception.jsonl` (both uncataloged and a cataloged URL that 404s). L3: removing `world-events.jsonl` from a copy of the artifacts showed this instead of "0 · No items". `features/timeline/data-tracks/errors.ts`, `composables/useTimelineDataTracks.ts`, `data-tracks/projection.ts` (`laneMessage`), `features/extensions/evidence/providers.ts`
- ✅ Evidence copy separates unknown from absent: "unknown, not absent", "not evidence of absence". 404/410 maps to `missing`, not `error`. `features/extensions/evidence/components/DivergenceDetails.vue`, `components/VisibilityDetails.vue`, `jsonl.ts`
- ✅ The resource browser shows four distinct states (loading, service unavailable with the start command, empty with rescan, no match with clear filters) and checks the error before the empty case. `features/resources/components/ResourceBrowserPanel.vue`
- ✅ `a550069`: an item or texture icon that does not load (no client jar configured, unknown item) falls back to the item name, never to an empty cell. `features/items/PixelImage.vue`, `features/inputs/components/InventorySlot.vue`

## no-silent-drop

- ❌ `ac9b4e5`: `invalidLineCount` is computed for `events.jsonl`, but nothing renders it; "N / M events" looks complete. `features/event-log/captureEvents.ts`
- ❌ `a550069`: when only some of one player's Plays have a perception file, the visibility lane loads the files that exist and reads as complete. `features/extensions/evidence/providers.ts` (`loadSummaries`)

## no-unmarked-estimate

- ❌ `ac9b4e5`: Preview converts `currentTime × 20` to a tick and ignores the video timing manifest. `features/media/composables/useReplayPlayback.ts`
- ✅ Monitor pauses while timing is unknown and badges "timing unknown" or "last frame held". `features/monitor/components/MonitorVideoTile.vue`
- ✅ `a550069`: the Play range preview prints "≈ Video position estimated at 20 ticks/s" when there is no timing file. `features/resources/components/PlayRangePreview.vue`

## no-shared-failure

- ❌ `ac9b4e5`: one unreadable Play fails the whole summary request; the dashboard shows "Summaries failed" for every Play. `internal/models/catalog/catalog.go`, `internal/grpc/services/artifactsv1/service.go`
- ❌ `ac9b4e5`: a failed refresh sets `error`, and the error branch replaces the loaded tree with "Artifact service unavailable". `features/resources/components/ResourceBrowserPanel.vue`, `features/resources/composables/useArtifactCatalog.ts`
- ✅ Each timeline data track loads, fails, and retries on its own and aborts stale loads. L3 `a550069`: aborting the `perception.jsonl` requests showed "Failed to load" with Retry on those lanes only. `features/timeline/composables/useTimelineDataTracks.ts`
- ✅ Event log keeps old rows during a reload and reports each failed Play separately with Retry. `features/event-log/useCaptureEvents.ts`, `components/EventLogPanel.vue`

## no-dead-end-state

- ❌ `ac9b4e5`: an evidence load that ends in error is never restarted (the comment says it will be); only a page reload recovers. `features/extensions/evidence/sources.ts`
- ❌ `ac9b4e5`: perception worker, JSONL, timing, and events fetches have no timeout and show no elapsed time. `features/extensions/evidence/sources.ts`, `features/monitor/videoTime.ts`, `features/event-log/useCaptureEvents.ts`
- ❌ `ac9b4e5`: a broken video gives a black tile; `<video>` has no error handler. `features/monitor/components/MonitorVideoTile.vue`

## no-flat-hierarchy

- ❌ `ac9b4e5` L2 at 1600×1000 with one world session and two Plays: 88–93% of visible text is 9–12px at regular weight; about 4% is medium or bold. Monitor holds 42–43% of the viewport and shows "Waiting for clip" at the default tick; the timeline holds 19%; Preview 2%. #21 changed panel chrome (26px headers, 11px titles) but not panel sizes; not re-measured.

## no-buried-answer

- ❌ `ac9b4e5`: the Evidence panel's default state says "Select an observation divergence… on the timeline" instead of listing the divergences. Not changed in #21.
- ✅ `a550069`: a session added to an empty timeline opens fitted, and seeks from other panels scroll the playhead into view. Before, an 8 px/s zoom floor capped the view at about 1:44 at 830px, so divergences at 6:00 were unreachable even after six zoom-outs. `features/timeline/core/adapter.ts` (`minZoomScale`), `composables/useTimelineSession.ts` (`zoomToFit`, `revealTick`)
- ✅ `a550069`: the resource row names the finding: "2 divergences" as labelled text instead of an unexplained yellow "2". `features/resources/components/ResourceTreeRow.vue`

## no-equal-weight-noise

- ✅ `a550069`: loaded data tracks with no items fold into "+N empty" on their parent row; missing and failed tracks stay visible because their state is a finding. 17 → 11 rows on the reference session. `features/timeline/layout.ts`, `components/TimelineTrackHeaders.vue`
- ❌ `ac9b4e5`: Event log shows consecutive identical rows ("Select hotbar slot 4" × 4), two lines each, every field at equal weight, 156 rows (14.9 screens) by default. Not changed in #21.
- ✅ `a550069`: the Inputs summary line appears only when it adds what the key cluster and hotbar cannot show (keys down, the name of the held item); "No keys held · empty hand" and the "Hotbar 4 · held · Empty" readout were removed because they restated the visible state. `features/inputs/components/PlayerInputCard.vue`
- ✅ `a550069`: survival stats beyond health and food (armor, air, absorption, experience) appear only when non-zero; the backpack folds behind "Backpack · N stacks". `features/inputs/components/PlayerStatusBar.vue`, `PlayerInputCard.vue`

## no-indistinct-identity

- ❌ `a550069`: Event log still gives every player the same pink dot; it should adopt `playerLaneColors` and `PlayerAvatar` from `features/players/`. (The timeline adopted the colors on `timeline-track-select`.)
- ❌→✅ `timeline-track-select`: identity colors were assigned by the current lane order, so reordering two lanes swapped the players' colors in Inputs. L3: dragging Tester above Airi turned Tester pink and Airi blue. Colors now follow the order in which lanes first joined the episode, and the timeline headers show the same color dot as the Inputs card. `features/players/colors.ts` (`playerLaneColors`), `features/timeline/components/TimelineTrackPanel.vue`
- ❌ Raw identifiers as labels: `set_carried_item`, `client_information`, `world_entities_not_recorded`, `container (-32, 96, 15)`; "All targets" shows `player 40f5db53` although the alignment has names. `features/extensions/evidence/providers.ts`
- ✅ `a550069`: players carry an avatar (Mojang face for online-mode v4 UUIDs, a deterministic generated face for offline v3 UUIDs) and a distinct lane-order color in Inputs. `features/players/`
- ✅ `a550069`: inventory cells show the item icon rendered from the operator's client jar, with the full name as fallback and tooltip, instead of names truncated to four letters or two-letter monograms. `features/items/`, `internal/models/itemicons/`
- ✅ `a550069`: every timeline data track has an icon for its kind (divergence, interactions, visibility, world events) instead of one generic chart icon, and every dock panel has a title (the Timeline tab was empty). `features/extensions/evidence/labels.ts`, `features/editor/components/EditorWorkspace.vue`

## no-lost-context

- ❌ `ac9b4e5`: the timeline composition lives only in memory; reload discards it. `features/timeline/stores/episode.ts`
- ❌ `ac9b4e5`: one route with no query state, so a Play at a tick cannot be linked. `main.ts`
- ❌ `ac9b4e5`: Dockview layout, event-log filters, evidence row mode, and Inputs All/Selected reset on reload. `features/editor/components/EditorWorkspace.vue`, `features/event-log/components/EventLogPanel.vue`, `features/inputs/components/InputMonitorPanel.vue`
- ✅ `a550069`: a rebuilt timeline engine carries the measured viewport, zoom, and scroll over. Before, every edit or data-track load clamped the zoom against a 1000px default width and snapped a fitted view back. `features/timeline/core/adapter.ts`
- ✅ `a550069`: the Inputs backpack open/closed choice persists per viewer in guarded local storage. `features/inputs/components/PlayerInputCard.vue`

## no-inconsistent-readout

- ❌ `ac9b4e5`: Preview and Monitor compute the tick of one frame with different models (see `no-unmarked-estimate`).
- ✅ `timeline-track-select`: one "selected player" is shared by the timeline and Inputs. Clicking a player's track header selects the lane (its row and data tracks are highlighted, with the identity color on the row's edge), selecting a clip selects its lane, and Inputs "Selected" follows that lane. Undo or delete that removes the lane clears the selection. `features/timeline/composables/useTimelineSession.ts` (`selectedTrackId`), `components/TimelineTrackHeaders.vue`, `features/inputs/components/InputMonitorPanel.vue`

## no-inert-action

- ❌ `a550069`: cut, trim, delete, reorder, undo, and redo edit an Episode draft that still cannot be saved, exported, or linked. The product decision is made (the composition is an output, see `ux-audit` product notes); the outcome is not built. `features/timeline/stores/episode.ts`

## no-false-feedback

- ❌ `ac9b4e5`: a Play with a missing or invalid end tick is rejected by `insertReplay`, but the announcement says "Already on the timeline." `features/timeline/replay.ts`, `features/resources/components/ResourceBrowserPanel.vue`
- ❌ `ac9b4e5`: Rescan swallows the backend failure and reloads as if it succeeded. `features/resources/composables/useArtifactCatalog.ts`

## no-input-hijack

- ✅ `a550069`: editor shortcuts yield Space, Backspace/Delete, and arrows to the focused control's own use of them (text fields, buttons and tabs for Space, composite widgets such as the resource tree for arrows and deletion); the timeline canvas (`role="application"`) keeps every shortcut. L3: Space on a focused button no longer toggles playback, Backspace in the tree no longer deletes the selected segment. `features/editor/composables/useEditorKeyboardControls.ts` (`focusOwnsKey`)
- ✅ `a550069`: buttons release focus after mouse clicks (`detail > 0`) and keep it for keyboard activation. Without this, yielding Space to a focused button made the next Space press "Go to start" again instead of toggling playback, and Chrome drew a focus ring on it. Dialogs and popovers keep their focus. `App.vue`
- ✅ `timeline-track-select`: track reordering lives on the row. A mouse press that moves less than 4px selects; a drag anywhere on a player or session row reorders it. There is no separate mouse grip: the row's type icon stays visible, the row carries the grab cursor and the "drag to reorder" tooltip, and the icon is the keyboard handle (Enter or Space selects, Alt+↑/↓ moves one step, `aria-keyshortcuts`), swapping to a grip only under keyboard focus. On touch, a swipe keeps scrolling the list and a 300 ms long press lifts the row. L3: hovering shows the type icon, not a grip; keyboard focus shows the grip; Space on the focused handle selects the track without toggling playback; Alt+↓ kept focus on the moved handle. `features/timeline/components/TimelineTrackHeaders.vue`
- ✅ `timeline-track-select`: a row drag shows what is moving and where it will land: a lifted, shadowed copy of the row follows the pointer (grab point kept, "N data tracks move with it"), the row stays in place dimmed, and an insertion line marks the landing edge (above the target when moving up, below the target's whole group when moving down). Hovering a lane's data rows targets that lane, so the line does not drop out while crossing them. Near the list's top or bottom edge the list scrolls every frame. L2/L3: mid-drag screenshot shows ghost, line, and placeholder; holding the ghost at the bottom edge scrolled the list 0 → 200px (its maximum) with the canvas in sync. Before, a drag only dimmed the row and outlined the target, and nothing followed the pointer. `features/timeline/components/TimelineTrackHeaders.vue`
- ✅ The resource tree handles its own arrow keys and stops propagation so they do not seek the timeline. `features/resources/components/ResourceBrowserPanel.vue`
