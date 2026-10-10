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
          alignments/                           # optional session alignments
            <name>.jsonl
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

A session alignment contains generated `SessionAlignmentRecord` ProtoJSON
lines for one world session and several Plays. It is not part of a Play. The
reserved location is `world/sessions/<session-dir>/alignments/<name>.jsonl`.
See [Session alignment](#session-alignment) below.

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
Assets are served with fixed media types: `.jsonl` as `application/x-ndjson`,
`.json` as `application/json`, `.sqlite3` as `application/vnd.sqlite3`, `.mp4`
as `video/mp4`, `.zip` as `application/zip`, and `.png` as `image/png`.

The catalog lists world sessions beside players (`GET /api/v1/world-sessions`,
`GET /api/v1/server-instances/{id}/world-sessions/{dir}`, and
`ServerInstance.world_sessions`). A world session whose metadata is missing,
invalid, or still open stays visible with a `validation_error`. A Play links to
the world session named by its `world_container_truth`; without that
reference, it links to the only world session of the same server instance with
its `session_id`. The Replay resource carries URLs for `perception.jsonl`,
`actions.jsonl`, `scene.sqlite3`, and `renders/fpv_frames/frames.jsonl` only
when those files exist.

The server reuses one walk of the artifacts root for up to two seconds.
`POST /api/v1/catalog:refresh` discards it and walks the root again.

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

## Session alignment

A session alignment joins one world session with several Plays of the same
`session_id` on the shared server tick timeline. It answers, for each actor
and container: "since this actor last observed this container, did the
world contents differ from what it observed, and from when to when?" It
records observations and differences only. It never states what an actor
knows, expects, or thinks; consumers interpret these facts.

```sh
WORLD='artifacts/v1/<server>--<instance>/world/sessions/<start>--<session>'
ALICE='artifacts/v1/<server>--<instance>/players/alice--<uuid>/plays/<start>--<connection>'
BOB='artifacts/v1/<server>--<instance>/players/bob--<uuid>/plays/<start>--<connection>'

go run ./cmd/recorder-minecraft session align \
  --world-metadata "$WORLD/metadata.json" \
  --world-events "$WORLD/world-events.jsonl" \
  --play "metadata=$ALICE/metadata.json,events=$ALICE/capture/events.jsonl,perception=$ALICE/perception.jsonl" \
  --play "metadata=$BOB/metadata.json,events=$BOB/capture/events.jsonl" \
  --output out/session-alignment.jsonl
```

| Property | Session alignment |
| --- | --- |
| Scope | `session`: one world stream plus the named Plays. World records have no actor; every other record names its actor by `connection_id` |
| Provenance | `deterministic transform`: computed from the inputs only; equal inputs give equal output |
| Causality | Hindsight: `uses_future_context` is `true` (see below) |
| Owner | `recorder-minecraft session align`; replaceable with `--overwrite` |

The participant set is the caller's choice, so the caller names the output
path. To publish an alignment in the catalog, write it to
`world/sessions/<session-dir>/alignments/<name>.jsonl` of the world session it
aligns. `<name>` is any non-empty file name that does not start with `.`; one
session can hold several alignments with different participant sets. The
catalog lists every regular `*.jsonl` file there and reads only its header
line. A header that is not a schema version 1 header of the same `session_id`
is reported as a validation error of that alignment. Each Play is given
explicitly with `metadata`, `events`, and an optional `perception`. The
processor does not discover Plays. Every Play must have the world session's
`session_id`, and a Play may appear only once. A perception file must name
the same session, player, and connection, and its header must record the
SHA-256 of the given `capture/events.jsonl`. Inputs are only read. The
output is staged and published atomically.

Every line is the ProtoJSON form of one `SessionAlignmentRecord`. The first
line is the `header`. Then come the `event` lines in tick order and the
`divergence` lines in start tick order.

The header has the processor name and version, scope, provenance,
`uses_future_context`, a `future_context` statement, the world coverage, the
lineage (path, SHA-256, size) of every input, the assumptions, and the
known limitations. It lists each participant: player UUID and name,
connection id, Play coverage, terminal reason, perception coverage and
sampling interval when given, and the numbers of world containers the actor
observed and never observed. An actor is one Play, not one player. Two connections of the
same player are two actors, and observations do not carry across them.

Records reference sources instead of copying them. A `RecordRef` names the
stream (world events, capture metadata, capture events, or perception), the
actor's `connection_id` for actor streams, the server tick, and the stream
`sequence` for world and capture events. Messages are reused from other
contracts where possible: `BlockPosition`, `TickRange`, and `ArtifactFile`
from common, `InventorySlot` and `ContainerViewKind` from events, and
`PerceptionProcessor` and `PerceptionInput` from perception.

### Aligned events

The event index is bounded to records that the divergences can join:

- `WORLD_CONTAINER_SNAPSHOT` and `WORLD_CONTAINER_REMOVED`, only for
  containers that some participant observed. Other world records remain in
  `world-events.jsonl`.
- `ACTOR_JOINED` and `ACTOR_LEFT` at the Play start and end ticks, from
  capture metadata.
- `ACTOR_CONTAINER_VIEW` for every container-backed `container_view` except
  `CARRIED`, and `ACTOR_CONTAINER_CLICK` for applied container clicks while
  such a menu was open. `block_pos` is the menu's `source.block_pos`.
- `ACTOR_BLOCK_ENTITY_VISIBILITY` for observed containers and
  `ACTOR_ENTITY_VISIBILITY` for `minecraft:player` entities, from
  perception samples. An event is written only when the state changes to
  `VISIBLE`, `NOT_VISIBLE`, or `UNDETERMINED`. Other entities stay in
  `perception.jsonl`.

Within a tick, joins come first, then world records, then container views
and clicks, then visibility, then leaves.

### Observations and divergences

An actor observes a container when the server sends the actor:

- a `CONTENTS` view of a menu whose `source` is that container: its complete
  contents, or
- a `SLOT` view of one of its slots in such a menu, applied to the last
  observed contents. A `SLOT` view without an earlier `CONTENTS` view of
  the container is not an observation.

Menu slot `i` below `container_slot_count` is container slot `i`. For a
double chest, the first half of these slots belongs to `source.block_pos`
and the second half to `source.secondary_block_pos`. Each half is a separate
container. Menu slots at or above the world `container_size` are not
compared.

Vanilla does not send a `SLOT` view for a change that the actor's client
already predicted. When the actor clicks in the menu, the server adopts the
client's predicted slots and sends only the slots that still differ. So an
applied `container_click` while a container-backed menu is open sets the
actor's observation to the world contents at the end of that tick. Captured
click records carry no container id, so the click is attributed to the menu
that was open when it was applied.

The truth for a container is its latest world `container_snapshot` at or
before the tick. Truth is unknown before the first snapshot, after
`container_removed`, and for `LOOT_UNGENERATED` contents. Stacks are equal
when `item_id`, `count`, and `damage` match, and `components_snbt` matches
when both records carry it.

Observation and truth are compared at the end of each tick, after all world
records of that tick and then the actor's records in stream order. Only
ticks inside both the Play and the world stream are evaluated.

A divergence starts at the first tick where the truth is known and differs
from the actor's last observed contents. The actor must have observed the
container before. A container the actor never observed has no divergence;
the participant's `unobserved_container_count` counts these containers. The
divergence ends with one of these `end` values:

| End | Meaning | `end_tick` |
| --- | --- | --- |
| `REOBSERVED` | The actor observed the container again | Tick of that observation |
| `TRUTH_MATCHES` | The world contents changed back to the observed contents | Tick of that snapshot |
| `CONTAINER_REMOVED` | `container_removed`: chunk unloaded or block destroyed | Tick of the removal |
| `TRUTH_UNDETERMINED` | The world contents became undetermined | Tick of that snapshot |
| `ACTOR_COVERAGE_END` | The Play ended first | Absent |
| `WORLD_COVERAGE_END` | The world stream ended first | Absent |

`start_tick` is the first and `last_tick` the last tick at whose end the
difference held. `end_source` refers to the record that ended it. For a
coverage end, `end_tick` and `end_source` are absent, because the state
after `last_tick` is unknown. A divergence does not extend past either
coverage. If a re-observation still differs from the truth, a new
divergence starts at the same tick.

A divergence also has:

- `observed`: the last observed contents before the start, with the
  records that produced them (the `CONTENTS` view, then any `SLOT` views, or
  the click and the world snapshot it confirmed).
- `truth`: the world contents at the end of `start_tick`, with the
  snapshot reference.
- `truth_changes`: the number of world snapshots of the container after
  the start and inside the interval.

Slot numbers in `observed` and `truth` are container slots. The diagnostic
`components_debug` field is omitted.

### Co-presence evidence

`co_presence` copies facts from the observer's own `perception.jsonl` at
the divergence start:

- `status` is `PERCEPTION_NOT_PROVIDED` when no perception was given (this
  is unknown, not false), `NO_SAMPLE` when no sample is at or less than one
  sampling interval before the start tick, and `SAMPLED` otherwise.
- `sample` refers to the sample that was used: the latest at or before the
  start tick.
- `container` is the visibility of the container block entity in that
  sample: `VISIBLE`, `UNDETERMINED`, or `NOT_VISIBLE`.
- `entities` lists the visible and undetermined entities of the sample with
  UUID and type. For a player entity whose Play is a participant and covers
  the start tick, `participant_connection_id` names that Play, and
  `participant_container_open` tells whether that participant had a menu
  backed by this container open at the start tick. `participant_menu`
  refers to that menu's `OPENED` view.

These are facts with no conclusion. For example, an actor can have a
divergence while the actor who changed the container was visible beside it
with the container open. Deciding what that means belongs to the consumer.

### Causal and hindsight fields

The processor is hindsight. Only `last_tick`, `end_tick`, `end`,
`end_source`, and `truth_changes` of a divergence read records after its
`start_tick`. `start_tick`, `observed`, `truth`, and `co_presence` use only
records at or before the start tick. Every aligned event uses only its own
record and earlier records of the same stream. A later live processor can
therefore emit aligned events and divergence starts causally, and close
the intervals as the ends occur.

### Limitations

The header lists the known limitations. The main ones:

- A click is attributed to the menu that was open, because captured click
  records carry no container id.
- Entity inventories (chest boats, minecarts, donkeys) are not in the world
  stream, and ender chests have no container source. They are never
  observed or compared.
- A `SLOT` view sent in the tick after the world change gives a one-tick
  divergence that ends `REOBSERVED`.
- Co-presence copies perception verdicts, which keep all the perception
  assumptions and limitations.
- There is no tick selection. The output covers the whole world stream and
  every named Play.
