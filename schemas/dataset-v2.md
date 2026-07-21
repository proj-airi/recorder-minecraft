# Dataset V2

Dataset V2 is the only supported exported dataset contract. It combines the
authoritative recorder state/action timeline with an optional immutable,
random-access client-visible scene store and optional first-person RGB frames.
Dataset V1 and the replay-client voxel-crop format are not accepted.

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
