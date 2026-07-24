# Dataset V2

Dataset V2 is the recorder's verified processing and training intermediate. It
combines the authoritative state/action timeline with an optional immutable,
random-access client-visible Scene Store V1 and optional first-person RGB
frames. It is not the portable offline interchange format; a complete
connection is finalized as [Portable Play Bundle V1](play-bundle-v1.md) with
Scene Store V2 and the exact contributing Flashback archives. Dataset V1 and
the replay-client voxel-crop format are not accepted.

## Directory contract

```text
DATASET.dataset/
  manifest.json
  samples.jsonl
  states.jsonl
  actions.jsonl
  modalities.jsonl
  scene/
    scene-v1.sqlite3       # present only when a scene is attached
```

`manifest.json` has `schema_version: 2`, `owner: "mc-recorder"`, and
`format: "mc-recorder-jsonl-v2"`. Its `files` object contains the SHA-256 and
byte size of every file other than the manifest, including the scene store.
Readers must reject missing, extra, symlinked, escaping, or hash-mismatched
files. Publication and replacement are atomic directory operations.

## Capture source snapshots

A connection-scoped export may consume a verified prefix of an append-only
`events.jsonl.inprogress` after that connection has both `player_join` and
`player_leave` records. Such an export records
`manifest.source.snapshot.format: "append_prefix_v1"` and binds the selected
player and connection explicitly.

Every snapshot segment records:

- its source epoch index and root-relative source path;
- `kind`, either `finalized` or `active_prefix`;
- the source size observed before reading and the retained prefix byte count;
- SHA-256 and non-empty record count for the retained prefix;
- first and last global sequence;
- first and last server tick.

The consumer reads no bytes beyond the initially observed size and drops a
final unterminated line. It rejects malformed complete lines, source
replacement, truncation, prefix mutation, identity mismatch, non-monotonic
sequence/tick values, and a selected connection without both boundaries.
Bytes appended after the observed size are allowed. The consumer creates
finalized envelopes only in a private staging snapshot; it never renames or
writes recorder source files. `rotation_reason: "consumer_snapshot"` therefore
describes consumer-generated evidence and must not be interpreted as recorder
finalization.

The dataset manifest embeds the complete snapshot envelope. Per-row source
references use hashes from the private verified segment, while
`source.snapshot.segments[*].sha256` binds those bytes to their original
append-only source prefix.

## Timeline and samples

`states.jsonl` contains one post-tick player state per selected subject and
tick. `actions.jsonl` contains reconstructed controls and ordered applied
packets. A row in `samples.jsonl` represents:

```text
post_state[t]
  + reconstructed_control[t+1]
  + packets where barrier[t] < apply_sequence <= barrier[t+1]
  -> post_state[t+1]
```

The key is `(session_id, player_uuid, connection_id, server_tick)`. Samples do
not cross a connection boundary. Invalid or incomplete transitions remain in
the dataset with `transition_valid: false` and explicit reasons.

Tick-oriented viewers use `states.jsonl` and `modalities.jsonl` as the
canonical observation index, then join a `samples.jsonl` transition when one
exists. The terminal state of a connection therefore remains randomly
accessible (including scene/RGB modalities) with no fabricated action or next
state; a one-tick connection has one viewable observation and zero transitions.

Each state/sample has this modality envelope:

```json
{
  "scene": {
    "available": true,
    "valid": true,
    "coverage_complete": true,
    "reference": "scene/scene-v1.sqlite3",
    "frame_id": "...",
    "reason": null
  },
  "rgb": {
    "available": false,
    "valid": false,
    "reference": null,
    "reason": "..."
  }
}
```

An absent scene uses false for all three booleans, null references, and
`reason: "scene_not_attached"`. Consumers must require `available`, `valid`,
and `coverage_complete` before treating a scene as training input.

## Scene Store V1

`scene-v1.sqlite3` is a read-only SQLite value (`PRAGMA user_version = 1`) for
exactly one `(session_id, player_uuid, connection_id)`. It contains:

- a frame row for every selected `player_state` tick;
- content-addressed, zlib-compressed blobs;
- palette-encoded 16 by 16 by 16 block sections;
- half-open section, entity, and block-entity version intervals;
- temporal/spatial indexes for duration-independent entity and block-entity
  crop/slice lookups;
- frame dimension, subject position, replay tick, completeness, and metadata;
- canonical source-replay integrity envelopes, the extraction policy, and the
  complete verified terminal extraction result.

Frames are logical full client-visible snapshots. Physical storage uses
structural sharing, so unchanged sections and entities are not duplicated.
Random access must not depend on replaying earlier frames. Areas that were not
loaded for the recorded client are unknown, never implicit air.

Scene extraction is server-only and uses the exact Minecraft/registry packet
codecs. At every `mc_recorder:timeline` marker it applies all preceding replay
actions and emits a frame. Segment overlap must resolve to identical state;
gaps, source mutation, an unknown state-affecting packet, identity mismatch, or
missing selected tick fail the extraction. Partial stores are never attached.
The selected subject pose is not inferred from ServerReplay's sampled local
player. It is copied from the exact sealed, hash-verified `player_state` epoch
bytes into a job-owned bounded JSONL stream. Its envelope records the stream
hash/size/tick range and every contributing epoch hash/size/count. The
extractor requires contiguous identity-matched records, overlays that
authoritative pose before frame and overlap hashing, and re-verifies the pose
file before and after extraction.

Export and dataset discovery also bind every attached frame back to the exact
canonical `player_state` at the same session, player, connection, and tick.
The frame dimension and subject position must match that state. Coordinates on
both sides are persisted decimal numbers decoded as IEEE-754 binary64 values,
so comparison uses exact equality after finite-number validation; no epsilon
may associate a nearby but different pose with the training sample.

Every source archive and matching replay-segment ledger row must declare
`flashback_capture_contract: "client_visible_scene_v1"`; archives from before
that capture contract are not scene sources. The contract preserves explicit
chunk unload, player correction, minecart movement, and entity motion packets
that upstream replay optimizations otherwise omit.
The terminal result must exactly match the job identity, policy, replay
envelopes, subject-pose envelope, capture contract, selected frame count,
frames/changes byte sizes and SHA-256 hashes, and referenced canonical blob
count/bytes/digests. Compaction accepts a frozen verified envelope and rechecks
the spool before reading and before publication.

The default and currently supported scope is `client_visible`; no source world
is mounted. Scene V1 includes block states, entities, block entities, and full
captured packet metadata. It does not define meshes, textures, particles,
audio, lighting renders, or original client pixels.

### Known scene and modality gaps

Scene Store V1 does not persist block-light or sky-light arrays. Block states
may support an approximate lighting recomputation when additional dimension,
time, weather, and light-engine assumptions are supplied, but exact lighting
observed by the recorded client cannot be recovered from `scene-v1.sqlite3`
alone.

Particle and sound events are not materialized as Dataset V2 timelines, and
the RGB renderer publishes image frames without an audio track. A source
Flashback archive may retain relevant packets depending on its capture policy,
but Dataset V2 does not currently guarantee their presence, coverage, or
interpretation. Future particle or audio modalities must declare their own
timeline, coverage, integrity, and replay-source provenance.

Block-entity payloads contain only data exposed to the recorded client.
Player-private container updates are not materialized by the current scene
extractor, so an unopened chest or other container has unknown contents rather
than an empty inventory. Consumers must not interpret absent inventory data as
proof that a container was empty. A future authoritative-container modality
would require explicit server-side capture or a clearly scoped record of
player-observed container updates.

### Scene-slice viewer response

The authenticated dataset viewer exposes a bounded two-dimensional projection
of a scene frame. Block states are deduplicated into a response-local palette;
cells never repeat the block-state object:

```json
{
  "palette": [
    { "name": "minecraft:stone", "properties": {} }
  ],
  "cells": [
    {
      "world_position": [8, 64, 8],
      "covered": true,
      "palette_index": 0,
      "color": [69, 62, 163]
    },
    {
      "world_position": [9, 64, 8],
      "covered": false,
      "palette_index": null,
      "color": null
    }
  ]
}
```

Cells are row-major and `width * height` must equal their count. Every covered
cell references an in-range palette entry; every uncovered cell uses
`palette_index: null`. Unknown cells remain distinct from air. `color` is a
deterministic display aid derived from the referenced block state and is not a
training feature. The HTTP server rejects any serialized JSON response larger
than 64 MiB before writing response headers or body bytes.

## Privacy and provenance

Scene stores using `metadata_policy: "full_packet_metadata"` are marked
`sensitive: true`. Text-bearing entity and block-entity metadata present in the
replay is retained; chat remains absent when capture was configured to ignore
it. The dataset manifest records the contained store hash/size and the exact
source replay segment IDs, ordinals, hashes, sizes, format, and verified
terminal result. Local source/store/stream paths are provenance strings only:
they may no longer exist after atomic publication and must never be dereferenced
instead of the contained hash-verified store.

RGB remains a separately derived client render. Absolute RGB artifact paths are
allowed only after the renderer index and every image have passed containment,
identity, timeline, and integrity validation.
