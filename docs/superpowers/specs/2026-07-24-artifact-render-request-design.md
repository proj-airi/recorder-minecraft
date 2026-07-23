# Artifact Render Request Design

## Goal

Allow an operator to request RGB rendering directly from a completed
Flashback replay artifact in the Dashboard. The operator must not run
`minerec export` first, wait for a recorder epoch rotation, or stop Minecraft.

The Dashboard remains an artifact viewer and render-pipeline operator. It does
not start, stop, inspect, or command Minecraft or Docker Compose, and it does
not launch a GUI renderer.

## Current Ground Truth

The filesystem already contains enough identity to join the two recording
outputs:

- recorder capture records carry `session_id`, `player_uuid`, and
  `connection_id`;
- completed Flashback archives embed the same identity plus `segment_id` and
  `segment_ordinal`;
- `player_leave` closes the selected connection in the append-only capture;
- a completed Flashback archive proves ServerReplay has published immutable
  replay bytes for that connection.

The current Dashboard can create a render job only from an existing
`*.dataset`. `minerec export` creates that dataset, but it only reads finalized
epoch envelopes. A disconnected connection can therefore have a completed
Flashback archive while its recorder records remain in
`events.jsonl.inprogress` until the next periodic epoch rotation.

This dependency is an exporter implementation constraint, not a property of
the recorded data. A consumer can take a verified prefix snapshot without
mutating the append-only source.

## User Workflow

The `Flashback replay ZIP` table exposes render settings and a `Render RGB`
action for archives with an exact `session_id`, `player_uuid`, and
`connection_id`.

One click creates an artifact render request. The request progresses through:

1. `preparing_dataset`;
2. `queued`;
3. `rendering`;
4. `complete`, `partial`, `failed`, or `canceled`.

The operator sees these stages in the existing `Renders` view. Dataset
publication remains visible in the `Datasets` artifact section, but it is no
longer a prerequisite the operator must produce manually.

## Components

### Dashboard

The Dashboard:

- lists verified Flashback artifacts;
- accepts `POST /api/v1/replay-artifacts/{artifact_id}/render`;
- validates resolution, presentation settings, archive identity, and catalog
  scan completeness;
- persists an artifact render request;
- displays preparation and GUI render state.

It does not call the exporter in an HTTP request handler.

### Render request store

`RenderQueueStore` remains the single durable state store for the render
pipeline. A request may begin with:

- `source_artifact_id`;
- source `session_id`, `player_uuid`, and `connection_id`;
- render settings;
- a null `dataset_id`;
- state `preparing_dataset`.

Only a request with a verified `dataset_id` can enter `queued` and become
eligible for RabbitMQ publication. The existing publication and GUI lease
fencing continue to apply after that transition.

Preparation claims use a separate lease owner and expiry from GUI render
attempts. A process crash returns an expired `preparing_dataset` request to
the preparation queue without publishing it to RabbitMQ.

### Headless render preparer

`minerec render-preparer` runs as a Compose service. It:

1. leases one artifact render request;
2. revalidates the exact Flashback artifact;
3. creates a read-only prefix snapshot of the matching capture session;
4. exports one connection-scoped dataset to a deterministic output name;
5. verifies the published dataset with `DatasetViewer`;
6. binds its opaque `dataset_id` to the request and changes the request state
   to `queued`.

The existing `render-dispatcher` then publishes the request, and a logged-in
host `render-worker` performs GUI rendering.

The preparer does not need Minecraft, Java, Gradle, or a display.

## Append-Only Capture Snapshot

Add a focused capture snapshot module. It never renames, truncates, locks, or
writes inside the recorder capture root.

For every finalized `events.jsonl`, the snapshot verifies the existing
manifest envelope and copies the verified bytes.

For an `events.jsonl.inprogress` candidate, it:

1. opens the regular non-symlink file;
2. captures its device, inode, and byte size;
3. reads at most that observed size;
4. discards bytes after the final newline;
5. parses every retained non-empty line as one JSON object;
6. verifies session identity, epoch identity, monotonic sequence, and
   nondecreasing server tick;
7. requires a `player_join` and `player_leave` for the selected connection;
8. hashes and counts the retained prefix;
9. rereads the same prefix from the same source path and rejects any byte,
   device, inode, or size regression;
10. writes a synthetic finalized envelope only inside a private staging
    snapshot.

Bytes appended after the initially observed size do not invalidate the
snapshot. Any rewrite, replacement, truncation, malformed complete line, or
missing connection boundary fails preparation.

The snapshot manifest records, for every source:

- original root-relative path;
- observed prefix byte count;
- SHA-256;
- record count;
- first and last sequence;
- first and last server tick;
- whether the source was a finalized file or an active-prefix snapshot.

The dataset manifest embeds this snapshot provenance. Consumers can verify
what bytes produced the dataset without depending on a retained temporary
snapshot directory.

## Dataset Publication

Each artifact request exports exactly one player connection. The output name
is deterministic:

```text
<session_id>-<connection_id>.dataset
```

Repeated equivalent requests reuse a dataset only after `DatasetViewer`
verifies it and its selection exactly matches the requested session, player,
and connection. A corrupt or mismatched existing output fails closed; it is
not overwritten implicitly.

The exporter continues to use atomic staging-directory publication. Source
references use snapshot segment hashes rather than claiming the active source
was finalized by the recorder.

## API

Add:

```text
POST /api/v1/replay-artifacts/{artifact_id}/render
```

Request:

```json
{
  "width": 1280,
  "height": 720,
  "fps": 20,
  "no_gui": false
}
```

Response is the persisted render request. Duplicate active requests with the
same artifact and render settings return the existing request. Invalid
artifact IDs return 404; incomplete catalog scans and source conflicts return
409; invalid render settings return 400.

The existing dataset render endpoint remains available for explicit,
pre-exported datasets.

## Failure And Retry

Preparation failures are stored on the render request and shown in the
`Renders` view. Retry:

- revalidates the Flashback archive;
- discards private staging data from the failed attempt;
- takes a fresh source prefix snapshot;
- reuses an already published dataset only after full verification.

Canceling a request before GUI publication prevents preparation from advancing
it. Existing cancellation fencing applies once GUI rendering starts.

No failure writes to capture or replay artifacts.

## UI

`ArtifactBrowser` adds shared resolution and `Hide GUI` controls to the
Flashback section. Each exact-bound replay row exposes an icon/text
`Render RGB` command. Unbound or invalid replay artifacts cannot submit.

`RenderPipeline` distinguishes preparation from GUI rendering through the
request state and progress text. It does not introduce Minecraft server
status, live player state, Seal, epoch rotation, or Docker Compose controls.

## Contract Changes

Update:

- `docs/specs/dataset-v2.md` with append-only snapshot provenance;
- `docs/specs/render-transfer-v1.md` with artifact render preparation states;
- README workflow documentation.

No compatibility behavior for the old render queue database is required. A
schema mismatch must produce the existing actionable remove-and-restart
error.

## Verification

- Snapshot tests cover concurrent append, incomplete final lines, rewrite,
  replacement, truncation, malformed complete lines, wrong identity, and
  missing join/leave boundaries.
- Export tests prove a connection in an active recorder file produces a
  valid connection-scoped Dataset V2 with authenticated snapshot provenance.
- Queue tests cover preparation leases, crash expiry, dataset binding,
  publication exclusion before binding, deduplication, cancellation, and
  retry.
- Dashboard HTTP and service tests cover artifact-scoped render submission
  without Minecraft or Compose behavior.
- Vue typecheck and production build cover the replay-row action and render
  preparation states.
- An end-to-end local check uses the current completed Flashback archive and
  still-open capture epoch to publish a dataset and queue a render request.
- Python checks, focused mod builds, `git diff --check`, and a browser smoke
  test pass before completion.
