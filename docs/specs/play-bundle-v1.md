# Portable Play Bundle V1

Portable Play Bundle V1 is the immutable, self-contained interchange format for
one completed player connection. A published bundle is sufficient for offline
inspection with the standalone local viewer; it does not depend on recorder
capture directories, Dataset V2, the dashboard, or the original server.

Dataset V2, Scene Store V1, replay catalogs, render jobs, and capture epochs are
recorder intermediates. They remain outside the published bundle hierarchy.

## Publication path and archive layout

The publisher writes bundles beneath the configured artifacts root:

```text
artifacts/v1/
  <server-name>--<server-instance-id>/
    players/
      <player-name>--<player-uuid>/
        plays/
          <started-at>--<connection-id>/
            <bundle-id>.mcplay.zip
```

Display names may change. `server_instance_id`, player UUID, and connection ID
are canonical identities. `minerec init` generates one server-instance UUID and
persists it in `recorder.toml`.

The ZIP root has no wrapper directory:

```text
metadata.json
actions.jsonl
scene.sqlite3
replays/
  000000--<segment-id>.zip
  000001--<segment-id>.zip
renders/                              # optional; absent means not rendered
  fpv.mp4
  fpv.timeline.jsonl
```

Every bundle requires actions, a complete Scene Store V2, and at least one
Flashback replay segment. Rendering is derived data and is never required for
publication.

## Identity, integrity, and revisions

`metadata.json` is canonical UTF-8 JSON with sorted keys, compact separators,
no non-finite values, and one trailing newline. It declares exactly:

- format name and version, deterministic `bundle_id`, sensitivity, and known
  modality gaps;
- server name and instance UUID, player name and UUID, session ID, and
  connection ID;
- canonical UTC start/end, inclusive server-tick range, and 20 Hz tick rate;
- ordered replay descriptors with bundle-local and original source ordinals,
  exact segment IDs, hashes, sizes, and selected frame coverage;
- an optional render descriptor; and
- a path-sorted inventory containing SHA-256, byte size, and media role for
  every non-metadata archive entry.

A contributing replay can have no directly selected frame when it supplies
initialization or world state across a rotation boundary. Such a descriptor has
null selected server/replay tick ranges; the aligned descriptors must still
cover every connection tick without a gap.

The bundle ID is SHA-256 over the canonical metadata value with `bundle_id`
removed. Therefore identical identity and artifact hashes reuse the same
published path. Adding or changing a render changes the ID and creates another
immutable revision; it never mutates the renderless bundle.

Finalization accepts only the full join-to-leave envelope produced by the
connection snapshot pipeline. It rereads the original capture beneath the
configured capture root, verifies its session manifest and every retained
epoch prefix, requires one ordered join and leave, regenerates the consumer
epoch manifests, and byte-reconstructs canonical `states.jsonl` and
`actions.jsonl`. Dataset hashes cannot authenticate a fabricated or partial
connection by themselves. The leave event may occur one tick after the final
post-tick state; bundle tick bounds always describe the exact contiguous
frame/state range.

## Reconstructed actions

`actions.jsonl` is the verified reconstructed semantic action stream for this
connection. Every row is bound to the bundle session, player UUID, connection
ID, source epoch, source event hash, and inclusive tick range. Rows are ordered
by `(server_tick, sequence)`. Exactly one structurally valid `control_state`
row is required per connection tick; discrete applied actions retain their
original sequence and apply-barrier position.

This stream models accepted controls and serverbound actions. It is not raw
keyboard, mouse, controller, packet-arrival, or packet-byte telemetry.

## Scene Store V2

`scene.sqlite3` is a portable SQLite interchange database. Its logical schema
and ordinary base-table queries are defined with SQLAlchemy Core using portable
types and application-assigned keys. A future PostgreSQL adapter can implement
the same logical model without changing canonical payload bytes.

| Table | Function |
| --- | --- |
| `schema_info` | Schema name and version sentinel. |
| `scene_meta` | Connection identity, tick bounds, sensitivity, replay descriptors, and extraction provenance. |
| `blobs` | Content-addressed zlib payloads whose uncompressed bytes are canonical JSON. |
| `frames` | One complete client-visible scene frame per server tick, including replay alignment and subject position. |
| `player_states` | Exactly one authoritative post-tick player state per frame. |
| `section_versions` | Immutable, interval-addressed world-section versions. |
| `entity_versions` | Immutable, interval-addressed entity versions. |
| `block_entity_versions` | Immutable, interval-addressed block-entity versions. |

`player_states` links each tick to the active player entity version. Frequently
queried values are typed columns: dimension; position, velocity, and rotation;
alive/on-ground/pose and movement flags; use state; health, absorption, armor,
food, air, XP, game mode, selected slot; and the action-apply barrier. The full
canonical player payload, including inventory, effects, abilities, vehicle,
and passengers, remains in a content-addressed blob. Death and respawn may
relink later state rows to a new player entity instance.

Entity and block-entity versions use explicit application-assigned `BIGINT`
IDs. No logical relationship depends on SQLite `rowid`. The SQLite adapter adds
R-tree acceleration, triggers, PRAGMAs, immutable opening, and durable atomic
publication; these are adapter details rather than portable schema semantics.

Publication requires exact one-to-one frame/state tick coverage, complete scene
coverage for every connection tick, valid player-entity links, canonical
row/blob agreement, valid content hashes, and R-tree/base-table parity.

Host-local paths are not portable provenance. Replay references inside the
store use the bundle-relative `replays/` paths, while other source filesystem
paths are redacted. Segment identity, original ordinal, hash, size, format, and
extraction evidence remain intact.

## Flashback replay segments

Each nested ZIP is the exact original Flashback archive used by scene
extraction. The finalizer resolves segments through the verified replay
catalog, rechecks size and SHA-256, verifies the archive's embedded recorder
identity and extraction provenance, and copies its bytes unchanged.
Independently rotated archives are never merged or rewritten. The production
reader cross-binds bundle metadata, Scene Store V2 provenance, and each nested
archive's `arcade_replay_meta.json` identity.

## Optional FPV render

When present, `renders/fpv.mp4` contains one H.264/yuv420p video stream,
constant 20 FPS, positive dimensions, fast-start metadata, and no audio. It has
exactly one frame for every inclusive connection tick.

`renders/fpv.timeline.jsonl` has one ordered record per video frame. Each record
contains integer `frame_index` and PTS, global `server_tick`, exact replay-local
tick, and the exact Scene Store V2 frame ID. Frame indexes start at zero, PTS
values are the exact MP4 frame timestamps in the video stream's time-base units,
and server ticks are contiguous. The reader requires both declared frame rates
to equal 20 FPS, adjacent MP4 PTS values to differ by exactly 1/20 second, and
every timeline PTS to equal the corresponding probed MP4 frame PTS. It resolves
every replay tick and scene frame against `scene.sqlite3`; the format does not
interpolate missing data or compress gaps invisibly.

## ZIP profile and hostile-input validation

The publisher enables ZIP64. SQLite, nested replay ZIPs, and MP4 are stored
without recompression; JSON entries are deflated. Publication happens in a
private staging directory and is accepted only after reopening the candidate
through the production reader, verifying hashes and semantic contracts,
flushing files and parent directories, and atomically renaming it into place.

Readers fail closed on:

- missing, extra, undeclared, duplicate, encrypted, symlink, device, or other
  non-regular entries;
- absolute paths, traversal, backslashes, invalid POSIX components, or Unicode
  and case-folding collisions;
- malformed canonical metadata, identity conflicts, unsafe segment IDs, hash
  or size mismatch, and incomplete action/scene/replay coverage;
- archive, central-directory, entry-count, line-size, expanded-size,
  compression-ratio, or nested replay limits; and
- malformed Flashback archives, timelines, SQLite stores, or declared render
  media. Render validation uses `ffprobe` during both finalization and local
  viewer import.

An interrupted or failed publication cannot replace an existing valid bundle.
Published ZIPs are never migrated in place. A format or content upgrade creates
a newly validated bundle revision.

## Standalone local viewer

Build and launch the dashboard-independent Vue viewer with:

```sh
pixi run build-viewer
pixi run minerec viewer [optional-bundle.mcplay.zip]
```

The locked Pixi runtime is the supported macOS ARM and Linux x64 distribution:
it supplies Python, SQLite support, and FFmpeg/`ffprobe`. The validated wheel
contains the compiled Vue assets, but a plain wheel installation must also put
`ffprobe` on `PATH` to open bundles that contain an FPV render. Renderless
bundles do not invoke `ffprobe`.

The command binds a random loopback port, generates a per-launch token, opens
the local application, and accepts one dragged bundle at a time. The browser
sends the `File` directly without calling `arrayBuffer()`. Upload progress is
reported, and validation creates a staged candidate. The active bundle changes
only after an explicit commit, so cancellation or validation failure preserves
the previously open bundle.

The bridge keeps JSONL behind private SQLite byte-offset indexes, opens the
scene database read-only and immutable, bounds every query/response, and serves
optional MP4 with single, suffix, and multipart HTTP Range support. It exposes
metadata/integrity, tick state, paged actions, bounded trajectory and scene
slices, replay descriptors, and optional render/timeline data. It exposes no
dashboard controls, authentication UI, render queue, or full 3D renderer.

Only exact loopback hosts and origins are accepted. The API has no CORS,
requires the launch token, emits a restrictive CSP and no-store responses for
sensitive API data, and removes temporary data on shutdown.

## Known modality boundaries

V1 metadata declares these gaps explicitly:

- `raw_device_input_not_captured`: reconstructed controls are semantic, not
  original physical input;
- `audio_not_captured`: the bundle has no synchronized audio modality;
- `particle_lifecycle_not_captured`: particle events/lifetimes are not a
  materialized timeline; and
- `unopened_container_contents_unknown`: client-visible scene data cannot prove
  the private contents of a container that the player never opened.

Lighting is not listed as an absent modality because a viewer can derive a
display approximation from blocks, dimension, time, and scene context. V1 does
not claim byte-exact reproduction of client light arrays or original rendered
pixels. Unknown/unloaded cells always remain distinct from covered air.
