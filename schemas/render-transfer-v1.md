# Portable render transfer v1

Portable render transfer v1 moves only a pinned replay segment to a GUI-capable
worker and moves verified derivative artifacts back to the recorder host. The
server-authored request is immutable and path-free. A worker's filesystem paths
are provenance only and are never authoritative after import.

## Portable request

The exact bytes of a persisted request are bound by SHA-256. Its top-level
`request_type` is `mc-recorder-portable-render-request-v1`; it contains:

- a UUID `request_id`;
- source episode session ID and manifest SHA-256;
- exact player UUID and connection UUID;
- requested and observed global tick ranges at 20 Hz;
- `range_policy`, either `exact` or `intersection`, and an optional
  `newer_cutoff` for overlap ownership;
- an opaque replay `segment_id`, monotonic `segment_ordinal`, Flashback format,
  stable byte size, and SHA-256; and
- bounded resolution, 20 FPS, first-person-head camera, and optional voxel
  radii.

Requests contain no paths, URLs, commands, JVM flags, or environment values.
The request schema rejects unknown fields so a worker cannot smuggle executable
or filesystem inputs through an otherwise valid request.

`exact` requires the renderer result to cover the entire requested tick range.
`intersection` permits any non-empty contained subrange. When the archive has
no matching timeline coverage, the worker may return `status: "no_coverage"`
with no frame payload. If `newer_cutoff` is present, the cutoff tick and all
later ticks belong to the newer segment.

## Local materialization

The GUI worker verifies the replay ZIP structure, byte size, and SHA-256 before
materializing a legacy `mc-recorder-first-person-render-v1` job. That local job
contains machine-specific absolute paths required by the Java client, but it
also records the portable request ID and exact request-byte SHA-256. The local
paths never appear in the portable request or canonical imported result.

## Upload bundle

An upload directory contains:

```text
bundle.json
payload-files.jsonl
worker-result.json
frames/frames.jsonl                 # complete coverage only
frames/frame_<number>.png           # complete coverage only
frames/voxels.jsonl                 # optional
frames/voxels/voxel_<tick>.json.gz  # optional
```

`bundle.json` has type `mc-recorder-render-bundle-v1` and binds the authoritative
request ID and SHA-256, replay identity, result status and actual coverage, and
the payload-index SHA-256/count/byte total. `payload-files.jsonl` is sorted and
declares the relative POSIX path, byte size, and lowercase SHA-256 of every
payload file. The worker's raw `result.json` is renamed `worker-result.json`.

The transfer layer rejects absolute paths, `.`/`..`, backslashes, case-folding
collisions, unknown files, empty or oversized rows, symlinks, hardlinks,
devices, missing files, unstable files, size/hash mismatches, and configured
file-count or total-byte limit violations. A `no_coverage` bundle contains only
`worker-result.json` as payload.

## Server import and canonical result

Import always receives the server's saved request and authoritative replay
path separately. It rehashes both, verifies every uploaded file, copies through
no-follow file descriptors into a hidden staging directory, and validates PNG,
index, identity, timeline, voxel, and coverage contracts. The Mac result's
`replay`, `output`, and `voxel_index` paths are ignored.

The importer derives `result.json` with schema version 2 and type
`mc-recorder-render-result-v2`. Its artifact references are contained relative
paths (`frames`, `frames/frames.jsonl`, and optional
`frames/voxels.jsonl`). Provenance records the portable request, segment ID and
ordinal, range policy, cutoff, and actual/requested tick ranges. The original
worker result remains immutable as non-authoritative provenance.

An `import-manifest.json` inventories all imported bytes. Promotion is an
atomic directory rename. Reimporting the same request and bundle reuses a fully
rehash-verified destination; an invalid or conflicting destination is never
overwritten automatically.
