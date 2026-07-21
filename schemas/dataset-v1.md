# Dataset sample v1

Dataset sample v1 is the dependency-free JSONL export of a validated source
session. It preserves multiplayer/connection identity, action order, state
barriers, explicit modality validity, and source hashes.

## Export layout

```text
artifacts/exports/<session-id>.dataset/
  manifest.json
  samples.jsonl
  states.jsonl
  actions.jsonl
  modalities.jsonl
```

Only epochs whose record count, byte count, and SHA-256 match a sealed manifest
are read. Active epochs are reported and skipped. `manifest.json` hashes every
output file and records the session-manifest hash, all source epoch hashes,
selection filters, tick semantics, and modality status.

The repeatable `--player` and `--connection` filters select exact UUIDs. When
both are present, they are conjunctive with the inclusive `--from-tick` and
`--to-tick` bounds: a subject row must match every configured filter. This
keeps reconnects isolated while retaining other players as peer context inside
each selected transition. The normalized connection UUIDs are recorded in
`manifest.json` under `selection.connections`.

## Canonical sample

The logical sample key is:

```text
(session_id, server_tick, player_uuid, connection_id)
```

`epoch_index` is retained as source partition/provenance. `connection_id` is
required because the same player UUID may reconnect during one session.

Each `samples.jsonl` object contains:

| Field | Meaning |
| --- | --- |
| `sample_key` | The four-part key above. |
| `epoch_index` | Epoch containing `state`. A valid transition may cross an epoch boundary. |
| `state` | Authoritative post-tick player state at tick `t`. |
| `action.reconstructed_control` | Exported `control_state` associated with tick `t+1`, including source provenance. |
| `action.ordered_packets` | Applied semantic packet actions through the next state barrier, ordered by `apply_sequence` (with source `sequence` as a deterministic fallback). |
| `action.after_apply_sequence` | `state_barrier_apply_sequence` for `state`. |
| `action.through_apply_sequence` | `state_barrier_apply_sequence` for `next_state`. |
| `action.assigned_packet_count` / `excluded_packet_count` | Audit counts for tick-assigned packets and packets rejected from the authoritative barrier interval. |
| `next_state` | Authoritative post-tick state at tick `t+1`. |
| `peers.state` / `peers.next_state` | Other recorded players' post-tick states at `t` and `t+1`, with connection identity and provenance. |
| `modalities` | Per-sample voxel and RGB availability/reference objects. |
| `transition_valid` | True only when all transition invariants hold. |
| `transition_invalid_reasons` | Machine-readable reasons such as a missing barrier/control or non-consecutive ticks. |
| `source` | Event sequence and epoch hashes for state, control, ordered packets, and next state. |
| `source_manifest_sha256` | Hash of the source session manifest. |

The transition is:

```text
post_state[t] + ordered server-observed actions after barrier[t]
              and through barrier[t+1]
  -> post_state[t+1]
```

Actions describe what the server decoded and applied. Persistent movement
flags are sampled at 20 Hz, and camera deltas describe accepted rotation
between ticks. They are not original key-down timestamps or raw mouse deltas.
Only `packet_apply` rows inside the two state barriers enter
`action.ordered_packets`. Tick-assigned packets outside that interval are
excluded, counted, and make `transition_valid` false.

## Supporting streams

`states.jsonl` contains selected `player_state` rows with the full source
envelope (except `record_type`), `source_schema_version`, and source epoch/event
hash reference.

`actions.jsonl` contains:

- one `control_state` row per selected player/tick; and
- ordered `packet_apply` rows with `sequence`, `apply_sequence`, semantic
  `action_type`, normalized payload, player UUID, and connection ID.

`packet_arrival` records remain in the immutable source but are not exported as
training actions because arrival is not authoritative application.

`modalities.jsonl` contains one row per selected player state. Attached RGB and
voxel records use validated absolute artifact references and include both the
source-index SHA-256 and artifact SHA-256/byte size. Voxel records also expose
origin, shape, covered/total cell counts, and `coverage_complete`. Missing
modalities are explicit objects with
`available: false`, `valid: false`, a null reference, and a reason. Missing
chunks are never encoded as known air and missing images are never encoded as
black frames.

A structured-only export is therefore a complete, valid dataset even when all
RGB and voxel entries are unavailable. Rendering and re-export may attach those
optional modalities later, but consumers must continue to decide availability
per sample from the modality objects rather than from the dataset directory
name or the presence of another sample's artifact.

## Large-export viewer contract

`samples.jsonl` is a streaming JSONL contract and may be hundreds of megabytes;
consumers must not assume it can be loaded into browser memory as one document.
The V1 dashboard builds a server-side SQLite byte-offset index keyed by the
verified export manifest and its declared file hashes, then exposes paginated
summaries and on-demand sample details through opaque IDs. A changed manifest or
declared hash invalidates that index.

RGB and voxel bytes are served only after their references remain contained
within the configured export root and their declared size/hash still match.
Symlinks, missing artifacts, and escaping paths are invalid. Voxel viewers must
honor the coverage bitset: uncovered slice cells are unknown, not air. The V1
dashboard renders axis-selectable 2D slices and does not define a full 3D voxel
interface.

## Modality status

RGB and voxel outputs inherit a replay integrity envelope. Job preparation
records the stable replay byte size and SHA-256; the renderer verifies both
before opening and after materialization, and the launcher rehashes the replay
before accepting the completed result. Remote GUI rendering additionally uses
the path-free request, hash-indexed bundle, and canonical server import defined
by [portable render transfer v1](render-transfer-v1.md).

### Structured state and actions

Implemented. The exporter emits canonical transition samples, state/action
supporting streams, multiplayer peer states, and source provenance as JSONL.
Parquet, WebDataset, Zarr, and framework-specific adapters are not part of V1.

### Voxels

Implemented as an optional local replay-client conversion. A render job with
positive `voxel_horizontal_radius` and `voxel_vertical_radius` materializes one
crop centered on the recorded player for every selected replay tick. Both radii
must be zero (disabled) or positive; V1 limits each to 64 blocks and each crop
to 2,000,000 cells.

Each `voxel_<server-tick>.json.gz` object contains:

- session, player, connection, global server tick, replay tick, and dimension;
- center, origin, shape, and `x_fastest_then_z_then_y` linear order;
- a palette of canonical block-state names whose properties are sorted;
- little-endian `uint16` or `uint32` palette indexes encoded as base64; and
- an LSB-first base64 coverage bitset plus covered/total counts.

`voxels.jsonl` indexes the gzip artifacts by exact sample identity. Uncovered
cells have a placeholder index but a false coverage bit, so they must not be
treated as known air. `coverage_complete` is true only when every crop cell was
available in the replay client world. Block entities are not materialized in
V1; the immutable replay remains their source when client-visible.

Dataset modality `valid: true` means the voxel artifact and coverage mask were
validated and attached; it does not imply `coverage_complete: true`. Consumers
must apply the coverage bitset when constructing tensors or losses.

Pass a completed render directory or `voxels.jsonl` to the repeatable exporter
`--voxels` option. The exporter validates identity, shape, coverage counts,
contiguous ticks, gzip/JSON bounds, palette indexes, little-endian buffers,
coverage-bitset semantics, referenced artifacts, and index/artifact hashes
before attaching it. The artifact dimension must also match the authoritative
`player_state`. Absolute, escaping, missing, or symlinked referenced paths are
rejected.

### RGB

Implemented as a separate local-client render stage for Flashback archives.
The V1 renderer:

- selects a player UUID and `connection_id`;
- aligns replay ticks to global server ticks from
  `mc_recorder:timeline/v1` markers;
- enters Flashback's replay-server spectate mode for the recorded player and
  waits for the server-confirmed camera switch before export;
- tracks the recorded player's head in first person while Flashback forwards
  the recorded nine-slot hotbar, selected slot, food, saturation, and
  experience state to the HUD;
- renders the recorded first-person hand/item and full recorded client HUD by
  default; and
- emits one PNG per tick at exactly 20 FPS plus `frames.jsonl` and an atomic
  `result.json`.

The render request and result record `no_gui`. New jobs use `false`; omission
is retained only for legacy V1 requests and means the historical HUD-free
mode. The HUD can include hotbar, crosshair, health, hunger, titles, boss bars,
action bar, and scoreboard packets. Chat packets are not present in the
configured ServerReplay source, and client-only inventory/crafting screens
cannot be reconstructed.

A client-only camera reassignment is not sufficient for GUI rendering: it does
not activate Flashback's replay-server first-person synchronization and leaves
the camera entity with only tracked equipment. GUI exports therefore use the
normal replay `spectate <player UUID>` command path and do not begin until its
camera acknowledgement is visible on the client.

Synchronized GUI results also record
`presentation_contract: "flashback_server_spectate_v1"`. A `no_gui: false`
attachment without that exact marker was produced before replay-server HUD
synchronization was enforced and is classified as
`legacy_gui_unsynchronized`, not `full_client`. The dashboard offers an
explicit RGB re-render for that state. The replacement uses a new immutable
render-job identity and is attached only after verification and atomic dataset
re-export; the previous verified import remains available as audit provenance.

`frames.jsonl` rows include frame number, global `server_tick`, replay tick,
session ID, connection ID, player UUID, and image path. The rendered image is a
reconstruction from server-visible replay packets, not the original client
framebuffer.

Pass a completed render directory, `result.json`, or `frames.jsonl` to the
repeatable exporter `--frames` option. The exporter accepts only a complete
20 FPS result with a contiguous index and joins frames by exact
`(session_id, player_uuid, connection_id, server_tick)`. Samples without a
matching attached frame remain explicitly unavailable. Referenced paths and
every image are containment-checked and hashed. PNG chunk ordering, CRCs,
IHDR/IDAT/IEND presence, and dimensions are validated against `result.json`;
absolute, escaping, missing, or symlinked references are rejected.
The dataset manifest's frame-attachment provenance preserves `no_gui` and the
presentation contract, so consumers can distinguish synchronized full-client
RGB, unsynchronized legacy GUI RGB, and historical HUD-free RGB. The dashboard
labels these states rather than inferring pixel fidelity from frame count
alone.

Legacy local renderer results identify their replay and output with absolute
local paths. An imported `mc-recorder-render-result-v2` instead uses contained
relative artifact references and an opaque replay segment identity. The
exporter supports both forms, preserves request/segment/cutoff provenance for
v2, and never resolves a worker-produced path during server-side attachment.
