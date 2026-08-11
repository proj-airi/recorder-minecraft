# Play Extensions V1: Catalog and Dashboard

## Problem Statement

A Play can contain primitive capture files and recorder-minecraft derived outputs, but there is no standard way for another producer to attach typed data to that Play. The catalog cannot describe this data. The dashboard cannot show it as a timeline track or open a type-specific view.

The first extension is `airicraft.planner`. It contains planner model calls aligned to the Play by recorder Server tick. A user must be able to inspect planner context beside the primary Play track and FPV video when FPV is available.

A future extension such as `llm.annotation` could analyze a completed Play and narrate salient player actions or world events. It is out of scope, but the extension surface must allow this type without a redesign.

## Solution

Add a Play Extensions V1 contract to Artifacts V1. Each extension type has one optional instance inside a Play. Its directory name is its type. A small manifest binds the extension to the Play and lists its typed assets.

Extend the catalog Play representation with extension descriptors and asset URLs.

Change the dashboard composition model so that one Play placement owns the time mapping for its primary track and extension tracks. A shipped dashboard extension module converts a supported extension into one timeline track and one specialized editor view. The first module supports `airicraft.planner`.

## User Stories

1. As a dashboard user, I want to see which supported extensions belong to a Play, so that I know what additional data I can inspect.

2. As a dashboard user, I want an Airicraft planner track beside the primary Play track, so that I can correlate planning activity with player behavior.

3. As a dashboard user, I want to select a planner call, so that I can inspect its request, response, tools, timing, and outcome.

4. As a dashboard user, I want planner evidence to follow the shared playhead, so that it stays synchronized with FPV and other Play data.

5. As a dashboard user, I want a Play without FPV to keep its data-backed primary track and extension tracks, so that FPV remains optional.

6. As a dashboard user, I want all tracks for one Play to stay aligned when I move, trim, reorder, or remove the Play, so that extension data does not become detached.

7. As an extension producer, I want one stable directory for my extension type, so that I can attach data without changing recorder-owned inputs.

8. As an extension producer, I want to identify my payload schema, so that a compatible dashboard module can read it.

9. As an extension producer, I want to use absolute recorder Server ticks, so that my data joins to the authoritative Play timeline.

10. As a dashboard developer, I want one extension module interface, so that a new supported type does not require changes throughout the editor.

11. As a future annotation developer, I want `llm.annotation` to use the same manifest, catalog, timeline, and view seams, so that it does not require a new extension system.

## Implementation Decisions

- The canonical term is **Play extension**. It is optional producer-owned data attached to one Play without changing primitive capture ownership.

- The dashboard adds the term **Play placement**. A Play placement is one occurrence of a cataloged Play in an Episode draft. It owns the source interval and the conversion from Play Server ticks to Episode ticks.

- Each extension type has zero or one instance in a Play. V1 has no extension instance ID, instance list, or run selection.

- Extension types use lowercase dot-separated identifiers. The initial type is `airicraft.planner`.

- The extension type is its directory name:

  ```text
  <play>/
    extensions/
      airicraft.planner/
        manifest.json
        planner-calls.jsonl
  ```

- Recorder-owned `metadata.json`, event data, and replay data do not change.

- The generic manifest contains:

  - Manifest version
  - Extension type
  - Play identity
  - Time domain
  - Asset role, path, media type, and schema

- Extension payload schemas are producer-owned. The catalog treats asset roles and schemas as opaque values.

- Time-bearing extension data uses absolute recorder Server ticks. It does not use client ticks or wall-clock time for Play alignment.

- The catalog Play representation gains a list of extension descriptors. Each descriptor contains the extension type and asset URLs with their roles, media types, and schemas.

- A missing or unsupported extension does not prevent use of the Play. An unsupported type remains visible in Play information but does not create a specialized track or view.

- Dashboard extension modules are shipped with the dashboard. The dashboard does not load executable UI code from a Play.

- One dashboard registry resolves extension types to extension modules.

- A V1 extension module provides one timeline track and one specialized editor view.

- The module converts producer records into normalized timeline items. An item is either a Server-tick point or a Server-tick interval.

- The timeline shell converts Server ticks to Episode positions through the shared Play placement. Extension modules do not calculate independent Episode positions.

- The primary track and all extension tracks for one Play refer to the same Play placement. Moving, trimming, reordering, or removing the placement affects the tracks as one group.

- FPV remains optional. A Play without FPV can have a primary data track and extension tracks.

- The editor extension context provides the Play placement, extension descriptor, shared Play-local Server tick, selected extension item, and asset access.

- The first dashboard module supports the `airicraft.planner` asset schema.

- A planner-call record contains:

  - Stable call identity and sequence
  - Planner attempt information
  - Submitted, completed, and optional applied Server-tick anchors
  - Model identity
  - Canonical request messages and tools
  - Outcome, assistant content, usage, and tool calls

- A planner call appears as an interval from submission through completion. Application appears as a marker when present.

- The planner view shows the selected call's context, model output, tool calls, outcome, timing, and timeline anchors.

- The existing Airicraft evaluator `llm-calls.jsonl` is not used directly because its records can be written before their later completion state is available. The Play extension needs final planner-call records.

- This repository implements the generic Play extension contract, catalog support, dashboard module seam, and `airicraft.planner` dashboard support. Airicraft production and publication of the extension are separate work against this contract.

## Out of Scope

- Producing or publishing `airicraft.planner` data from Airicraft.
- Implementing `llm.annotation`.
- Multiple instances of one extension type.
- A remote extension registry.
- Runtime-loaded or Play-supplied dashboard modules.
- Changing primitive capture files.
- Moving existing actions, scenes, renders, or summaries into extensions.
- Renaming the existing catalog `Replay` message.
- Extension-specific editing or mutation.
- Authentication and authorization for extension data.

## Further Notes

`llm.annotation` is a design check only. It may be produced after a Play is complete and may describe point events or intervals. It must be able to use the same manifest, catalog descriptor, Play placement, normalized timeline items, and dashboard module registry.

This specification is a local design artifact. It must not be committed or published as part of the implementation.
