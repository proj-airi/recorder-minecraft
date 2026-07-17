# Dataset sample v1

The canonical sample key is `(session_id, epoch_id, server_tick, player_id)`.
Structured columns are exported to Parquet when an Arrow backend is available,
with JSONL as the dependency-free interchange form.

Required fields:

- `server_tick` and `player_id`.
- `action`: persistent movement controls, accepted camera delta, and an ordered
  list of discrete actions.
- `state`: the authoritative post-tick player state.
- `entities`: other captured players and nearby entities when available.
- `voxel_ref`, `voxel_coverage_ref`, and `voxel_valid`.
- `rgb_ref` and `rgb_valid`.
- `source_manifest_sha256` and per-modality provenance.

Missing modalities must use validity fields. A missing chunk is never encoded
as known air, and a missing frame is never emitted as a black image.

The dependency-free v1 exporter emits one JSON object per sample. Binary RGB
and voxel payloads are sharded separately and referenced by stable sample key;
future WebDataset, Zarr, and framework-specific adapters consume the same
logical schema.
