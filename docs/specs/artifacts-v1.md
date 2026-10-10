# Artifacts V1 Pipeline

Artifacts V1 is the shared, filesystem-native pipeline. The recorder and later
processors operate on the same play directory, but have separate ownership.
It is not a ZIP bundle, publisher, importer, dataset format, or custom file
extension.

The Recorder mod may run with a dedicated server or with an integrated server
hosted by a game client. Server deployment does not change the hierarchy,
authority, metadata, event stream, or processor inputs. A client connected only
to a remote server does not create local artifacts.

## Canonical hierarchy

```text
artifacts/v1/
  <server-name>--<server-instance-uuid>/
    world/                                      # recorder; optional
      sessions/
        <started-at-utc>--<session-uuid>/
          metadata.json                         # world session ProtoJSON
          world-events.jsonl                    # world stream
    players/
      <player-name>--<player-uuid>/
        plays/
          <started-at-utc>--<connection-uuid>/
            metadata.json                       # recorder ProtoJSON
            capture/                            # recorder
              events.jsonl
              replay.zip
            actions.jsonl                       # optional post-process result
            scene.sqlite3                       # optional post-process result
            perception.jsonl                    # optional post-process result
            renders/                            # optional render result
              render-job.json
              result.json
              fpv_frames/
                frames.jsonl
                frame_*.png
            extensions/                         # optional producer-owned data
              <extension-type>/
                manifest.json
                <extension-assets>
```

Every player connection has exactly one play directory. Names are non-empty
NFC Unicode without separators or control characters. UUIDs use canonical
lowercase spelling. Start time uses `YYYYMMDDTHHMMSS[.fraction]Z`. No alternate
nesting is valid.

The instance UUID is generated once when recorder configuration is initialized
and reused across restarts. The server name is an editable display label. An
integrated-server recording profile retains the same instance identity when it
opens different save worlds. A new connection UUID is generated for every
join. Multiple players and overlapping connections create independent plays.

`world/` holds server-instance-wide data that is not owned by one player.
In V1 it contains only world sessions. A world save or seed is not part of V1,
and processors must not infer one from `world/`.

## World sessions

Each recorder session (one server start, identified by `session_id`) writes
one world session directory beside `players/`. The session directory name
uses the same start time form as a play and the session UUID.

| Property | World stream (`world-events.jsonl`) |
| --- | --- |
| Scope | `world`: server state, independent of any player |
| Provenance | `engine-reported`: read from server block entities |
| Records | `container_snapshot`, `container_removed` |
| Owner | Recorder; one stream per `session_id` |

The world stream records server state whether or not a player observed it.
Do not treat a world record as player knowledge. Join a world record to a play
by `session_id` and `server_tick`; both streams use the same tick clock.

Every line is the ProtoJSON form of one generated `WorldEvent`. Its
`WorldEventIdentity` has `schema_version` `1`, `session_id`, `server_tick`,
`sequence` (strictly increasing in the stream), `recorded_at_ns`, and
`recorded_at_unix_ms`. Ticks do not decrease. There are no player fields.

`container_snapshot` gives the complete contents of one container block
entity at the end of its tick:

- `dimension`, `block_pos`, and `block_entity_type` identify the container.
  `block_pos` is a `BlockPosition` of integers, the same type as
  `container_view.source.block_pos` in a play's `events.jsonl`.
- `reason` is `SESSION_START` for containers already loaded when the stream
  started, `LOADED` when a container enters a loaded chunk (chunk load or
  placement), and `CHANGED` after the container reported a change.
- `contents_state` is `KNOWN` or `LOOT_UNGENERATED`. `KNOWN` lists every
  non-empty slot in `slots`; omitted slots are empty. `LOOT_UNGENERATED` means
  the loot table has not been rolled. The contents are undetermined, not
  empty, `slots` is empty, and `loot_table` names the table. The recorder never
  reads slots of such a container, because reading them rolls the loot.
- A `CHANGED` snapshot is written at most once per position per tick, and only
  when the contents differ from the previous snapshot at that position.

`container_removed` ends the validity of the last snapshot at a position.
`cause` is `CHUNK_UNLOADED` when the chunk left memory (the contents remain in
the save) or `DESTROYED` when the block entity was removed from a loaded chunk.
The latest contents are written before the removal record.

The world session `metadata.json` is the ProtoJSON form of
`WorldSessionMetadata`. It has `scope` `world`, `provenance`
`engine-reported`, the start time and tick, `events`, and `known_gaps`. The
recorder writes the end time, `end_server_tick`, and `terminal_reason` last.
If the end tick is absent, the stream did not close and readers reject it.

A world stream failure does not stop Play capture. The recorder then writes
`terminal_reason` `stream_failure`, the failure text in `stream_failure`, and
the last covered tick as `end_server_tick`. Plays that start after the failure
do not reference the stream. A Play capture failure stops the world stream,
because the world stream uses the Play tick clock.

When a world stream is healthy at connection start, play `metadata.json`
contains `world_container_truth` with the server-instance-relative paths of
the world session `metadata` and `events`. That play then declares
`world_entities_not_recorded` in place of
`unopened_container_contents_may_be_unknown`. World coverage ends at the world
session end tick, which can be earlier than the play end tick.

Known gap `world_entities_not_recorded`: the world stream contains only
container block entities. Item entities, chest and hopper minecarts, and
entity inventories such as donkeys are not recorded.

## Stage 1: recorder

At player join on either server deployment, the recorder creates the play and
starts writing only:

```text
metadata.json
capture/events.jsonl
capture/replay.zip
```

The generated ProtoJSON-lines stream is single, buffered, and connection-local. ServerReplay uses
a timestamped working child under `capture/replay/`; after it closes, the
recorder moves the archive unchanged to `capture/replay.zip` and removes the
empty working parent. The recorder writes the metadata end tick last. There is
no explicit seal step. A non-null end tick is the handoff marker for
post-processing.

See [Primitive Capture V1](capture-v1.md) for the persisted fields and gaps.

## Stage 2: post-processing and rendering

Processors are explicit file-to-file tools. They do not discover a play,
construct the hierarchy, download data, or mutate recorder-owned inputs. The
caller passes exact inputs and chooses an exact output, conventionally in the
same play:

```sh
PLAY='artifacts/v1/<server>--<instance>/players/<player>--<uuid>/plays/<start>--<connection>'

go run ./cmd/recorder-minecraft actions extract \
  --metadata "$PLAY/metadata.json" \
  --events "$PLAY/capture/events.jsonl" \
  --output "$PLAY/actions.jsonl"

go run ./cmd/recorder-minecraft scene extract \
  --metadata "$PLAY/metadata.json" \
  --events "$PLAY/capture/events.jsonl" \
  --replay "$PLAY/capture/replay.zip" \
  --output "$PLAY/scene.sqlite3"

go run ./cmd/recorder-minecraft perception extract \
  --metadata "$PLAY/metadata.json" \
  --events "$PLAY/capture/events.jsonl" \
  --scene "$PLAY/scene.sqlite3" \
  --output "$PLAY/perception.jsonl"

go run ./cmd/recorder-minecraft render \
  --metadata "$PLAY/metadata.json" \
  --events "$PLAY/capture/events.jsonl" \
  --replay "$PLAY/capture/replay.zip" \
  --output "$PLAY/renders"
```

All commands validate that metadata has an end tick and that the event/replay
identities match it. `--from-tick` and `--to-tick` select a bounded interval.
`--prepare-only` leaves a scene/render job for inspection. `--overwrite` replaces
only an output already recognized as owned by that processor.

Processor scratch, locks, subject-pose streams, player-state staging, and the
private Scene Store V1 spool live under `.recorder/minecraft/runtime/`. They are not
part of Artifacts V1. Durable results alone are written to the explicit output.

Independent workers can copy complete plays with SSH/rsync, process them, and
copy back only `actions.jsonl`, `scene.sqlite3`, `perception.jsonl`, or
`renders/`. Coordination and
dataset assembly are deliberately outside V1.

## Play extensions

A Play extension is optional producer-owned typed data attached to one Play.
It does not change `metadata.json` or files under `capture/`. Each extension
type has zero or one directory. The type is a lowercase dot-separated
identifier and is also the directory name.

The extension manifest is `extensions/<extension-type>/manifest.json`. It is
`PlayExtensionManifest` ProtoJSON with manifest version `1`. Its Play identity
must match the server instance, player, and connection that own the directory.
Its time domain must be `PLAY_EXTENSION_TIME_DOMAIN_SERVER_TICK`.

Each listed asset has a producer-owned role and schema, a media type, and a path
relative to the extension directory. The asset path must name a regular file
inside that directory. The catalog treats role and schema values as opaque.

The initial extension type is `airicraft.planner`:

```text
extensions/airicraft.planner/
  manifest.json
  planner-calls.jsonl
```

Its planner call asset uses role `planner_calls`, media type
`application/x-ndjson`, and schema `airicraft.planner-call.v1`. Each line is a
final planner-call record. Decimal strings represent 64-bit ticks, sequences,
and Unix times. A record contains stable call identity and sequence, planner
attempt information, model identity, canonical request messages and tools,
outcome data, and these timeline anchors:

- `timeline.submitted.serverTick` is required.
- `timeline.completed.serverTick` closes a completed, failed, or cancelled call.
- `timeline.applied.serverTick` is optional and marks application to runtime state.

The planner-call payload schema belongs to Airicraft. Recorder-minecraft only
owns the generic manifest and catalog descriptor.

## Derived outputs

`actions.jsonl` contains generated `PlayerAction` ProtoJSON records reconstructed from authoritative
`packet_apply` records and 20 Hz `control_state`. It excludes diagnostic
`packet_arrival`, physical keyboard events, raw mouse samples, and raw bytes.

`perception.jsonl` contains generated `PerceptionRecord` ProtoJSON lines: one
header, then one sample per sampled server tick. See
[Perception](#perception) below.

`renders/render-job.json` and `renders/result.json` are generated `RenderJob`
and `RenderResult` ProtoJSON messages. `renders/fpv_frames/` contains PNG frames
plus generated `RenderFrameIndex` ProtoJSON lines mapping every frame to server
tick, replay tick, player/connection identity, and replay ID. The terminal
result binds the frame-index digest, byte size, frame count, replay integrity,
identity, requested/actual tick ranges, resolution, and frame rate. Renders are
optional; absence means not rendered.

`renders/fpv.mp4` is the optional H.264/YUV420p playback derivative composed
from a complete `fpv_frames/` result. The render command publishes it atomically
after `ffmpeg` succeeds; `--frames-only` retains the image-sequence-only
workflow. The video is derived and may be regenerated without mutating the
capture inputs.

## Read API and media serving

`recorder-minecraft serve` exposes the read-only Artifacts V1 catalog over gRPC
and a grpc-gateway HTTP API. Catalog traversal validates the server, player, and
play directory identities against their metadata before returning them.
`/assets/` serves only regular files contained below the configured artifacts
root and supports HTTP byte ranges so browser decoders can seek in MP4 files.
Symlinks and path traversal outside that root are rejected.

`scene.sqlite3` is Scene Store V2. It requires exact frame/player-state tick
coverage and contains typed player state plus the full inventory/effect/ability
payload in compressed content-addressed blobs. Its logical base tables are
`schema_info`, `scene_meta`, `blobs`, `frames`, `player_states`,
`section_versions`, `entity_versions`, and `block_entity_versions`. Explicit
application-assigned `BIGINT` version IDs avoid SQLite `rowid` dependence.
SQLite-only R-tree tables, triggers, PRAGMAs, immutable reads, and atomic
replacement stay in the SQLite adapter so a future storage adapter can preserve
the logical model.

Scene data has the same client-visible limits as the replay. Unknown cells and
unopened-container contents remain unknown in the scene; they are never
fabricated as air or empty inventories. Container truth comes from the world
stream, not from the scene.

## Perception

`perception.jsonl` answers, for one Play, "could this player see entity E or
block entity B at tick T?". It climbs one step past `container_view`: that
record says what was sent to the client, while a perception sample says what
was inside the player's view and not hidden behind blocks.

| Property | Perception (`perception.jsonl`) |
| --- | --- |
| Scope | `actor perception`: what the recorded player could see |
| Provenance | `reconstructed`: recomputed from `scene.sqlite3`, never observed on the player's computer |
| Causality | `uses_future_context` is `false`: a sample at tick T reads only scene state valid at T and settings recorded at or before T |
| Owner | `recorder-minecraft perception extract`; replaceable with `--overwrite` |

The processor reads `scene.sqlite3` (read-only) for the observer pose,
entities, block entities, and blocks, and reads `metadata.json` plus
`capture/events.jsonl` for identity and view distance. The first line is a
`header`: processor name and version, Play identity, sampled tick range,
SHA-256 and size of every input, every assumption, and the known
limitations. Every later line is a `sample`, in increasing `server_tick`.

A sample holds the observer eye position, yaw, pitch, and pose, and four
lists: `visible_entities`, `visible_block_entities`,
`undetermined_entities`, and `undetermined_block_entities`. Entities carry
the Scene Store `instance_id`, network id, UUID when known, and type. Block
entities carry `dimension`, a `BlockPosition`, and type, so they join the
world stream and `container_view` by position. Each target has a
`RaySupport`: sample points, points in view, and clear, blocked, and unknown
rays. A sample lists the full sets, not changes, so one sample answers a
question about its tick without replaying earlier lines. A target absent from
all lists was determined not visible.

Visibility is target-centric. For each entity and block entity in the
observer's dimension, within the distance limit:

1. Sample points are the target box center and its 8 corners pulled toward
   the center (factor 0.8 for entity boxes, 0.75 for a block entity cell).
2. Only points inside the view frustum are cast. The frustum is the vanilla
   perspective projection: vertical FOV, aspect, and near plane 0.05.
3. Each ray walks the block grid from the eye (voxel DDA) until it enters
   the target box. The eye's own cell is skipped.
4. The target is visible when at least one ray is clear. It is undetermined
   when no ray is clear and at least one ray met an unknown cell first.
   Otherwise it is not visible and is omitted.

Defaults, all recorded in the header and overridable by flags:

| Assumption | Default | Flag |
| --- | --- | --- |
| Sampling interval | every 4 server ticks | `--interval-ticks` |
| Vertical FOV (vanilla FOV option) | 70 degrees, about 102.4 degrees horizontal at 16:9 | `--fov` |
| Aspect ratio | 16:9 | `--aspect` |
| Distance limit | 64 blocks, capped by min(client, server) view distance x 16 | `--max-distance` |
| Eye height | standing 1.62, crouching 1.27, swimming/fall_flying/spin_attack 0.4, sleeping 0.2, otherwise 1.62 | none |
| Occluder | opaque full cube under vanilla 1.21.8 `BlockState.isSolidRender()`, plus lava and powder snow | none |

The occluder table is generated from the vanilla block registry by
`hack/generate-perception-occluders`. Glass, leaves (fancy graphics), water,
plants, and every non-full block let rays pass. Slabs block only as double
slabs, snow only at 8 layers, pistons only when retracted. A block missing
from the table, such as a mod block, blocks rays.

A cell whose section has no version at the sample tick is unknown: never
sent, or unloaded. It is not air and it is not an occluder. A ray that
reaches an unknown cell before any occluder stops with an unknown outcome.
An unknown ray never counts as clear, so it can make a target undetermined
but never visible. This keeps missing scene data distinct from evidence of
absence.

Known limitations are listed in the header. The main ones: FOV and aspect
are assumed because they never reach the server; dynamic FOV is ignored;
rotation is sampled once per server tick; partial block shapes, cutout
texels, entities, lighting, and invisibility do not affect sight; the scene
is client-visible, so cells outside it are unknown.
