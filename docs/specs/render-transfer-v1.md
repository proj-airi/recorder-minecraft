# Portable render transfer v1

Portable render transfer v1 moves only a pinned replay segment to a GUI-capable
worker and moves verified derivative artifacts back to the recorder host. The
server-authored request is immutable and path-free. A worker's filesystem paths
are provenance only and are never authoritative after import.

## Dashboard queue and RabbitMQ one-shot worker

The dashboard creates one durable RGB queue job only after the deterministic
structured dataset for a disconnected connection is verified. The job fixes the
recording, session, player, connection, dataset selection, renderable sample
tick range, dataset identity, resolution, and 20 Hz output rate. The renderable
range is the dataset's first through last sample tick and may be narrower than
the ledger's join/disconnect selection bounds; attachment preserves those
original selection bounds. A worker receives none of those values from
browser-controlled paths or command strings.

The recorder mod emits a bounded JSON spool file under
`control/render-ready/<connection-id>.json` after a disconnected connection has
at least one saved ServerReplay segment. `mc-recorder render-dispatcher` scans
that spool and publishes matching queued dashboard jobs to RabbitMQ. By default,
`mc-recorder render-worker` consumes one RabbitMQ message, claims that exact job,
runs one Java client in one ephemeral workspace, finalizes the result, and exits.

The worker heartbeats while downloading, rendering, and uploading. A reclaimed
or canceled lease cannot publish a result, even if an older client later
resumes. If a replay input is not yet immutable, the server defers that job for
a 30-second eligibility cooldown. The dispatcher waits for another mod-emitted
ready file before publishing more work for that connection.

A failure after a claim marks/fences that attempt and the process exits before
another message is consumed. This fail-stop boundary prevents a broken Java
runtime, Gradle installation, disk, or renderer build from failing remaining
queue messages. If server-side plan preparation fails after leasing, `claim`
returns `reason: "claim_failed"`, the failed job identity, and a bounded error.
A lost or invalid `claim` RPC response is fail-stop because the server may
already have leased or failed a job even when the worker did not receive the
response.

Workers advertise `ephemeral: true`, `portable_request_no_gui: true`,
`full_client_presentation_contract: "flashback_server_spectate_structured_hud_v1"`, and
`structured_claim_failure: true` before claiming. The server rejects workers
without the portable-request capability or current presentation contract so a
strict pre-extension V1 worker cannot claim a GUI request whose pixels require
the replay-server spectate synchronization path. The `register` response
advertises both `server_capabilities.structured_claim_failure: true` and the
same full-client presentation contract. All workers fail closed when the
contract is absent or mismatched. For a worker that does not advertise
structured claim failures, a server-side plan failure still fences the attempt
but returns a nonzero RPC error so an older one-shot worker cannot misreport it
as an empty queue.

The local recorder runtime is the worker's authority boundary. RPC actions are
direct in-process calls with bounded, action-specific JSON schemas. Replay reads
and bundle writes are limited to verified descendants of the configured
workspace roots. HTTP Basic-auth credentials, arbitrary commands, browser paths,
JVM flags, and environment values are not queue fields.

The worker cache stores replay archives and structured HUD sidecars by declared
SHA-256 and verifies stable size and digest before every reuse. Per-attempt
render workspaces carry an owner marker and are deleted after success or failure
unless the operator explicitly retains one. Removing a workspace never removes
either content-addressed cache.

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
- bounded resolution, 20 FPS, first-person-head camera, explicit `no_gui`, and
  the GUI `presentation_contract`.

Current GUI requests also carry a path-free `structured_hud` envelope. It binds
the exact dataset ID, dataset-manifest and `samples.jsonl` hashes, session,
player, connection, contiguous tick range, record count, byte size, and sidecar
SHA-256. The sidecar itself is generated server-side by streaming the already
verified dataset. Each row contains the authoritative health, absorption,
air, food, saturation, experience, selected slot, and every non-empty inventory
stack for one tick. `stack_snbt` is the authoritative item payload; item, count,
and damage fields are retained for diagnostics. Missing ticks, duplicate ticks,
identity mismatches, missing SNBT, or a changing dataset fail the render plan.

New requests set `render.no_gui: false`, binding full recorded client-HUD pixels
and the normal first-person hand/item view into the request hash. A missing
field is accepted only for persisted V1 compatibility and means the historical
HUD-free behavior (`no_gui: true`). Client-only screens such as inventory
and crafting menus are not replay state and cannot be reconstructed.

For `no_gui: false`, the renderer enters Flashback's replay-server spectate mode
and waits for the server-confirmed camera switch. At each recorder timeline
payload it then applies the hash-bound structured state before rendering. This
override is required because an older ServerReplay inventory capture path could
serialize a live mutable `ItemStack` after it changed. New GUI requests bind
`presentation_contract: "flashback_server_spectate_structured_hud_v1"` into
their request hash. Historical GUI results without this exact marker, including
`flashback_server_spectate_v1`, remain legacy rather than evidence of faithful
full-client HUD state.

Requests contain no paths, URLs, commands, JVM flags, or environment values.
The request schema rejects unknown fields so a worker cannot smuggle executable
or filesystem inputs through an otherwise valid request.

`exact` requires the renderer result to cover the entire requested tick range.
`intersection` permits any non-empty contained subrange. When the archive has
no matching timeline coverage, the worker may return `status: "no_coverage"`
with no frame payload. If `newer_cutoff` is present, the cutoff tick and all
later ticks belong to the newer segment.

## Multi-segment composition

The server resolves only `saved` replay-ledger entries whose embedded
`arcade_replay_meta.json` identity matches the selected session, player, and
connection, then pins and hashes those archives before returning transfer
locations. A worker processes their monotonic segment ordinals newest-first.
After a complete segment reports its effective first tick, that tick becomes
the `newer_cutoff` for older segments. An older segment may therefore render
only ticks strictly before newer coverage; overlap never creates two candidate
frames for one sample. A `no_coverage` result does not advance the cutoff.

The imported complete ranges must be ordered and non-overlapping. They are not
required to be contiguous: missing replay timeline coverage remains an explicit
RGB gap. No frames are interpolated, duplicated, or synthesized to make a range
look complete.

## Local materialization

The GUI worker verifies the replay ZIP and HUD sidecar byte sizes and SHA-256
before materializing a legacy `mc-recorder-first-person-render-v1` job. The
sidecar is copied to the owned job's canonical `hud-states.jsonl`; its local job
entry records the type, absolute path, hash, byte size, record count, and tick
range. That local job contains machine-specific absolute paths required by the
Java client, but it also records the portable request ID and exact request-byte
SHA-256. Local paths never appear in the portable request or canonical imported
result. The worker result and canonical imported result repeat the effective
`no_gui` policy and presentation contract so an artifact cannot claim a
different pixel presentation than its request.

When the sidecar is loaded, both results also repeat a path-free
`structured_hud` envelope containing its schema and format, type, SHA-256, byte
size, record count, full sidecar tick range, dataset ID, dataset-manifest and
`samples.jsonl` hashes, session, player, and connection. The launcher, bundle
verifier, canonical importer, attachment workflow, and exporter compare that
complete envelope with the request/job. Attachment also re-verifies that the
dataset manifest and samples hashes are unchanged immediately before promotion.
The sidecar range may cover more ticks than one replay-segment intersection,
but it must cover every rendered tick; no result may add the current
presentation marker or sidecar provenance when the request omitted them.

An episode-only local `render prepare` job has no verified exported-dataset
identity from which to author this sidecar. It may still render the historical
spectate view, but it does not claim the structured-HUD presentation contract.

## Upload bundle

An upload directory contains:

```text
bundle.json
payload-files.jsonl
worker-result.json
frames/frames.jsonl                 # complete coverage only
frames/frame_<number>.png           # complete coverage only
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
index, identity, timeline, and coverage contracts. The Mac result's `replay`
and `output` paths are ignored.

The importer derives `result.json` with schema version 2 and type
`mc-recorder-render-result-v2`. Its artifact references are contained relative
paths (`frames` and `frames/frames.jsonl`). Provenance records the portable request, segment ID and
ordinal, range policy, cutoff, and actual/requested tick ranges. The original
worker result remains immutable as non-authoritative provenance.

An `import-manifest.json` inventories all imported bytes. Promotion is an
atomic directory rename. Reimporting the same request and bundle reuses a fully
rehash-verified destination; an invalid or conflicting destination is never
overwritten automatically.

## Dataset attachment

Canonical imports are durable children of
`paths.exports/render-jobs/<queue-job-id>/`. Finalization accepts only imports
bound to the queue job's exact session, player, connection, requested tick
range, replay segment, and portable request hash. It first validates the
existing deterministic structured dataset and its opaque viewer identity. A
missing, tampered, or differently selected dataset is a hard failure rather
than permission to replace it.

Complete imports are attached through the normal exact-key exporter. The
resulting dataset directory is promoted atomically, preserving the prior
verified export if promotion fails. A job is `complete` only when every selected
sample has an attached RGB frame; otherwise a successfully verified attachment
is `partial`, and uncovered samples retain explicit unavailable RGB modality
objects. The changed manifest and declared hashes invalidate the dashboard's
SQLite byte-offset index on its next catalog refresh.
