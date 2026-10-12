---
name: ux
description: Rules for how the dashboard must read and behave. Each rule names one defect shape (`no-*`) with the evidence that proves it, the shapes that do not count, and the fix. Use when building, changing, or reviewing any dashboard surface (panel, timeline lane, inspector, list, empty/loading/error state, shortcut). A full audit of one surface is `ux-audit`.
---

# ux

The dashboard is a read-side instrument over recorded evidence. Its users make claims from what it shows: "Airi never saw the chest", "the divergence happened at tick 7211". So the bar is not "looks fine". The bar is: **what the screen says is true, the important part reads first, and the context the user built survives.**

The rules are defect shapes, not a checklist of features. A rule fires only when its evidence is in hand. Instances from this repository live in [references/cases.md](references/cases.md), keyed by rule name; the rules themselves stay free of file names.

## Principles

| Principle | Means | Rules |
| --- | --- | --- |
| **Truthful** | Unknown, missing, failed, approximate, and absent are different statements. Never print one as another. | `no-unknown-as-absent`, `no-silent-drop`, `no-unmarked-estimate`, `no-shared-failure`, `no-dead-end-state` |
| **Prioritized** | The surface answers its main question first. Space, size, weight, and color follow information value. | `no-flat-hierarchy`, `no-buried-answer`, `no-equal-weight-noise`, `no-indistinct-identity` |
| **Continuous** | Selection, composition, position, and view settings survive refresh, reload, and moving between panels. Two panels never disagree about one fact. | `no-lost-context`, `no-inconsistent-readout` |
| **Consequential** | Every affordance has a durable effect, reports its real outcome, and does not steal input meant for something else. | `no-inert-action`, `no-false-feedback`, `no-input-hijack` |

When two rules conflict, prefer Truthful over Prioritized over Continuous over Consequential. A clean, prioritized screen that states something false is worse than a cluttered true one.

## Evidence layers

A finding is valid only from a layer that can see it.

- **L1 code**: missing branches, swallowed errors, state that is never persisted.
- **L2 render**: hierarchy, space, color, identity, what a state actually looks like. Requires the running dashboard with real data.
- **L3 drive**: transitions, timing, focus, keys, forced failures.

Each rule lists its minimum layer. A rule whose minimum layer did not run is reported as **not checked**, never as passing.

## Rules

### no-unknown-as-absent

**Defect.** A loading, failed, missing, or unreadable source is shown as empty, zero, "No items", or a negative fact ("never visible", "no events").

**Prove it.** L1: the path from the failure or 404 to the render, ending in `[]`, `0`, or an empty-state component. L3: force the failure and capture the screen.

**High signal.** A loader that returns `[]` when its file is missing. `?? []` or `?? 0` before an empty check. A placeholder that never resolves to an error. A summary filter that drops `null` entries.

**Not a finding.** The source really loaded and really contains nothing, and the copy says so.

**Fix.** Give each source a result type that keeps `loading | ready | missing | error` apart through to the render. Name the cause in the copy: "perception.jsonl missing", not "No items".

### no-silent-drop

**Defect.** Records are skipped, truncated, capped, or hidden by a default filter, and the count or list reads as complete.

**Prove it.** L1: a skip, cap, or default filter with no consumer that reports how much it removed.

**High signal.** A counter such as `invalidLineCount` that nothing renders. "N / M" where M excludes rejected lines. A row cap stated only in a tooltip.

**Not a finding.** A user-set filter whose state is visible next to the count.

**Fix.** Show what was removed and why ("3 lines unreadable", "+18 more"). Use one policy for line-based files: skip the bad line and report the count.

### no-unmarked-estimate

**Defect.** A derived or approximate value is shown with the same certainty as a measured one.

**Prove it.** L1: a value computed by assumption (fixed rate, nearest neighbor, fallback default) rendered without a marker.

**High signal.** Converting video seconds to server ticks with a constant when a timing manifest exists. A distance or duration computed across a known gap.

**Fix.** Use the measured mapping when one exists. Otherwise mark the value ("≈", "timing unknown") and say what it assumes.

### no-shared-failure

**Defect.** One failing source blanks, replaces, or poisons data that does not depend on it.

**Prove it.** L1: an error from one source written to state that gates other sources, or a backend batch that fails as a whole. L3: break one source and capture the others.

**High signal.** One invalid Play failing the summary request for every Play. A failed refresh replacing an already loaded list with a page-level error. A render error in one lane taking down the panel.

**Fix.** Scope the failure to its source. Keep already loaded data and show "refresh failed, showing data from <time>". Batch endpoints return per-item errors.

### no-dead-end-state

**Defect.** A loading or failed state has no way out: no timeout, no retry, or a retry that cannot succeed.

**Prove it.** L1: a load with no timeout; an error cached with no path back to loading; Retry offered for a deterministic failure (404, invalid file).

**Fix.** Time out slow reads and show elapsed time. Offer Retry for transient failures only; for deterministic ones, name the fix (the missing file or the command that produces it).

### no-flat-hierarchy

**Defect.** Text, panels, and marks carry the same size, weight, and color regardless of importance, so the eye has no entry point.

**Prove it.** L2 only. Measure it: the share of visible text per font size and weight, and each panel's share of the viewport ([ux-audit probes](../ux-audit/references/probes.md)). Code that sets `text-[10px]` is a hint, not proof.

**High signal.** Over 80% of visible text in one or two sizes within 2px of each other at regular weight. Every panel with identical chrome. The largest area holding the least information at the default state.

**Fix.** Use three text levels (title, body, metadata) and reserve the smallest for metadata. Give the timeline, the spine of the tool, the largest area. Open fewer panels by default; the rest are one action away.

### no-buried-answer

**Defect.** The surface does not lead with the answer to its main question. The user has to hunt for the finding the data exists to show.

**Prove it.** L2: write the surface's main question (see `ux-audit` product notes), then locate its answer on the default screen. Report where it is, how large it is, and how many actions it takes to reach.

**High signal.** "2 observation divergences" appearing only as small text in a details pane. An inspector whose default state says "select something on the timeline" while a short list of findings exists.

**Fix.** Put a summary first (counts and key moments), make each item jump to its moment, and mark the moments on the ruler.

### no-equal-weight-noise

**Defect.** Low-value records get the same space and treatment as signal: empty lanes, zero-count rows, and runs of identical events.

**Prove it.** L2: count empty or zero rows and repeated runs on the default screen against rows with findings.

**High signal.** A lane labelled "0 · No items" styled the same as a lane with the one divergence. Four identical consecutive events shown as four two-line rows. A panel that lists every raw record by default (each event, each key and slot cell) instead of what matters at the current moment. One fact printed in several places (the server name on every tile and clip, several time readouts for one playhead). Copy that restates what the visuals already show ("No keys held" under unlit keys, "Slot 4 · held · Empty" under a highlighted empty slot).

**Fix.** Collapse empty lanes behind a count. Group repeated events ("× 4"). Style rows by importance, not by type alone. Lead with a summary of the current moment and keep the full record one action away. Print each fact once, at the level it belongs to. Show text only when it adds something the visual cannot.

### no-indistinct-identity

**Defect.** Entities cannot be told apart, or raw identifiers appear where a human name exists.

**Prove it.** L2: two entities sharing one color or marker in the same view. L1: rendering an id, enum key, or coordinate tuple when a name is available in the loaded data.

**High signal.** Every player with the same dot color. `set_carried_item`, `player 40f5db53`, or `container (-32, 96, 15)` used as a label.

**Fix.** Give each entity in a view a distinct color and keep it across panels. Show names and readable labels; keep raw ids as secondary text or behind copy.

### no-lost-context

**Defect.** Work the user built is discarded by reload, refresh, or navigation, and a moment cannot be shared.

**Prove it.** L1: state held only in memory with no persistence and no URL encoding. L3: build state, reload, and capture.

**High signal.** A timeline composition, view settings, or layout that resets on reload. A single route with no query state, so "this Play at this tick" cannot be linked.

**Fix.** Encode the shareable part (Plays, tick, selection) in the URL. Persist per-viewer settings (layout, filters, modes) in guarded local storage.

### no-inconsistent-readout

**Defect.** Two surfaces show different values for the same fact at the same moment.

**Prove it.** L1: two code paths that compute the same quantity differently. L3: both readouts captured at one playhead.

**High signal.** Preview and Monitor deriving the tick of one frame by different models. A count in a header that disagrees with the list below it.

**Fix.** Compute the value once and share it, or label the one that is approximate (`no-unmarked-estimate`).

### no-inert-action

**Defect.** An affordance changes something that does not persist and has no downstream effect.

**Prove it.** L1: trace the action to its end: no persistence, no export, no request, nothing another surface reads.

**High signal.** Cut, trim, delete, and undo on a composition that cannot be saved, exported, or linked.

**Not a finding.** Ephemeral view controls such as zoom or scroll.

**Fix.** Give the work an outcome (save, export, link), or remove the editing affordance and present the surface as read-only.

### no-false-feedback

**Defect.** The message about an action's outcome does not match what happened.

**Prove it.** L1: the branch that produces the message and the condition that actually occurred.

**High signal.** "Already on the timeline" when the add was rejected for invalid ticks. A rescan whose failure is swallowed while the list reloads as if it succeeded.

**Fix.** Report the real outcome and reason, including partial results ("added 1 of 2; 1 has no end tick").

### no-input-hijack

**Defect.** A global shortcut consumes a key that the focused control or the platform needs.

**Prove it.** L1: a window-level handler that calls `preventDefault` without checking the focused element's role. L3: focus the control, press the key, and observe both effects.

**High signal.** Space toggling playback while a button has focus. Backspace deleting a timeline segment while focus is in a tree or list.

**Fix.** Scope shortcuts to the surface that owns them, and skip them when focus is on a button, link, tree, list, or text field. When shortcuts yield to focused buttons, release focus after pointer clicks; otherwise the last clicked button swallows the next shortcut (Space presses "Go to start" again instead of playing).

## Adding or changing a rule

Add a rule only when a defect generalizes beyond one panel **and** no existing rule covers it. Prefer widening an existing rule's high-signal shapes. Keep the set small enough to read in one pass; merge before adding.

A rule must keep this shape: **Defect** (one sentence), **Prove it** (minimum layer and the evidence), **High signal**, optional **Not a finding**, **Fix**. Keep file paths and Play names out of rules; put them in [references/cases.md](references/cases.md) under the rule's name.
