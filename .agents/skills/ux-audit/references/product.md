# Product notes

Facts the audit needs that the code does not state. Domain terms follow `docs/TERMS_AND_CONCEPTS.md`; this file does not redefine them. Update it when an audit finds it wrong.

## Who uses the dashboard

Researchers and operators who inspect completed Plays and world sessions: what each player did, saw, and could have known, aligned on the Server tick timeline. The dashboard is read-side only; capture, processing, and rendering happen elsewhere.

## Class and comparables

A multi-stream recording inspector with a timeline. Compare with:

- **Foxglove Studio** and **Rerun Viewer**: synchronized panels over recorded streams, saved layouts, a shared time cursor, links to a time.
- **Video editors** (DaVinci Resolve, Premiere): the timeline as the spine, collapsible tracks, markers on the ruler.

Capabilities this class usually has: a layout that persists, a link to "this source at this time", one time readout shared by all panels, markers for notable moments, collapsible empty tracks, recent sources.

## Surfaces and their main questions

| Surface | Main question |
| --- | --- |
| Editor workspace (default layout) | "What in this session is worth looking at, and where?" |
| Resources | "Which Plays and world sessions exist, which are usable, and which am I inspecting?" Secondary: preview a Play and add only the range worth keeping. |
| Timeline | "When did things happen, for whom, and how do the streams line up?" |
| Monitor / Preview | "What did this player see at this tick?" |
| Inputs | "What was each player pressing, holding, and carrying at this tick, and in what state (health, food, effects)?" |
| Event log | "What happened around this tick, in order?" |
| Evidence | "Where does what a player could observe diverge from the world, and on what evidence?" |

## Core objects

| Object | Created by | In the dashboard |
| --- | --- | --- |
| Play | Recorder mod | Read-only. Listed, added to the timeline. |
| World session | Recorder mod | Read-only. Adds its linked Plays together. |
| Session alignment, perception | Processors | Read-only evidence files; may be missing. |
| Timeline composition (Episode draft) | The user, in the dashboard | An output: editable (add, add a range, cut, trim, delete, reorder, undo) but not yet saved, exported, or linkable. |

Decided 2026-10-12: the composition is an output, not a view. Agent runs stall or misbehave, and the user cuts those stretches out to improve the dataset. Cuts are per player: each kept segment keeps its source Server tick range, so players stay aligned without rippling the others, and a session-wide cut is a convenience on top. Dataset generation itself is out of the dashboard's scope; the dashboard owes the user a durable result (save, export, or link), which is why `no-inert-action` still fires. Stall candidates on the ruler (no input with a still camera, frozen frames, known gaps) are the expected way to find what to cut.
