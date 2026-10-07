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
copy back only `actions.jsonl`, `scene.sqlite3`, or `renders/`. Coordination and
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
