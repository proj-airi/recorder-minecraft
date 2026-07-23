# Artifact-First Dashboard Design

## Goal

Refactor the dashboard from a Minecraft server control surface into:

- a viewer for filesystem artifacts;
- a monitor and operator for the independent RGB render pipeline.

The dashboard must not model Minecraft server lifecycle, live player
connections, recorder heartbeats, Seal, or `render-ready` as user-facing
objects.

## Current Ground Truth

The current UI presents server start/stop, live capture metrics, connection
rows, “Seal & generate”, datasets, and render jobs in one operations view.
Those concepts do not share one lifecycle:

- `recorder-mod` writes sidecar capture sessions under the capture root;
- ServerReplay writes Flashback archives under the replay root;
- `minerec export` writes structured datasets under the export root;
- render workers write and upload RGB results through the render queue;
- the dashboard only reads or operates these components after they exist.

The current server start/stop dashboard methods already reject every request
and instruct the operator to use Docker Compose. “Seal & generate” also does
not seal an active recorder: it rejects active connections and exports already
finalized source epochs.

ServerReplay owns Flashback archive publication. A completed archive normally
appears after disconnect, server shutdown, or ServerReplay duration rotation.
Neither the dashboard nor `recorder-mod` has an API that can command
ServerReplay to publish an archive immediately.

## Design Boundary

### Dashboard owns

- discovery and inspection of capture-session directories;
- discovery and inspection of saved Flashback archives;
- the existing verified dataset catalog and sample viewer;
- render-job submission for an already exported dataset;
- render-job cancellation and retry;
- render-worker and render-job monitoring.

### Dashboard does not own

- Minecraft server start, stop, health, or player presence;
- recorder heartbeat or writer queue monitoring;
- connection lifecycle as a top-level UI model;
- source epoch finalization;
- dataset generation;
- ServerReplay archive rotation;
- `render-ready` control files.

Dataset generation remains an explicit CLI or offline processing operation.
The dashboard discovers the resulting dataset when its directory is
atomically published.

## Artifact Catalog

Add a focused Python module that scans configured filesystem roots and returns
four distinct artifact collections. It must not invent one shared lifecycle
for them.

### Capture sessions

Scan direct children of `paths.captures` with the existing capture inspection
functions. Return:

- `session_id`;
- root-relative path;
- byte size;
- observed source state: `open`, `complete`, `incomplete`, or `empty`;
- epoch count;
- published epoch count;
- open/incomplete epoch count;
- observed first and last server ticks.

The API and UI use “published epoch” rather than “sealed epoch”. The persisted
source manifest remains unchanged in this refactor.

### Flashback replay archives

Recursively scan regular, non-symlink `.zip` and `.mcpr` files under
`paths.replays`. A valid archive must:

- be contained by the configured replay root without symlink traversal;
- contain one `metadata.json`;
- contain Flashback payload data;
- contain one `arcade_replay_meta.json`;
- contain `arcade_replay_meta.json.mc_recorder` identity metadata;
- remain stat-stable while metadata and SHA-256 are read.

Return:

- content-derived artifact ID;
- root-relative path;
- byte size and SHA-256;
- `session_id`, `segment_id`, `segment_ordinal`;
- `player_uuid` and optional `connection_id`;
- capture-contract metadata.

Invalid candidates are returned as bounded catalog issues, not silently
treated as valid archives.

### Datasets

Reuse `DatasetViewer` and `DatasetIndex`. Dataset validation, opaque IDs,
sample queries, frame resolution, and scene slicing remain unchanged.

### Render output

Render jobs and workers remain projections from `RenderQueueStore`. Imported
RGB files continue to be reached through dataset/sample APIs and render-job
results; the browser never receives direct filesystem access.

## API

Retain:

- `GET /api/v1/artifacts`;
- `GET /api/v1/datasets`;
- existing dataset metadata, connections, trajectory, samples, frame, and
  scene-slice routes;
- `GET /api/v1/render-jobs`;
- `GET /api/v1/render-jobs/{job_id}`;
- `GET /api/v1/render-workers`;
- `POST /api/v1/datasets/{dataset_id}/render`;
- `POST /api/v1/render-jobs/{job_id}/cancel`;
- `POST /api/v1/render-jobs/{job_id}/retry`.

Remove:

- `GET /api/v1/status`;
- `GET /api/v1/recordings`;
- `GET /api/v1/jobs/{job_id}`;
- `POST /api/v1/server/start`;
- `POST /api/v1/server/stop`;
- `POST /api/v1/recordings/{recording_id}/generate`;
- `POST /api/v1/recordings/{recording_id}/render`.

The dataset render request accepts:

```json
{
  "player_uuid": "optional when the dataset has one subject",
  "connection_id": "optional when the dataset has one subject",
  "width": 640,
  "height": 360,
  "fps": 20,
  "no_gui": false,
  "replace_legacy_rgb": false
}
```

The service resolves the selected subject from the verified dataset index.
When the dataset has multiple `(player_uuid, connection_id)` pairs, both
identity fields are required. Tick bounds come from that subject’s dataset
samples, not from a live connection ledger.

Before creating a job, the service scans valid Flashback artifacts for the
same `session_id`, `player_uuid`, and `connection_id`. At least one saved
archive must exist. Absence is a modality-availability error, not a recording
state.

## Render Dispatch

The persistent render queue becomes dataset-scoped:

- job payloads use `dataset_id` as their primary source identity;
- the RabbitMQ task message carries `dataset_id`, not `recording_id`;
- retry revalidates the dataset and replay artifacts from the original job
  payload.

The dispatcher remains part of the render pipeline, but no longer reads
`control/render-ready`. It publishes queued jobs from a durable SQLite outbox:

1. a new job starts with no publication timestamp;
2. the dispatcher publishes its bounded RabbitMQ task;
3. after successful publish it atomically marks the job published;
4. publication failure leaves the job pending for a later scan;
5. lease expiry clears publication state before the job becomes queued again.

Duplicate RabbitMQ delivery is harmless because worker claim is fenced by the
SQLite job state and lease.

Render RPC resolves saved archives by scanning and validating archive metadata
under the replay root. It no longer requires a replay-segment control ledger.

## Recorder Mod

Remove the runtime-only `RecorderControlPlane` and its filesystem outputs:

- `status.json`;
- connection ledgers;
- replay-segment ledgers;
- `render-ready/*.json`.

`ReplaySegmentTracker` remains, but only to:

- allocate `segment_id` and `segment_ordinal`;
- bind ServerReplay recorder instances to the capture session, player, and
  connection;
- inject that identity into the saved archive metadata.

It no longer publishes snapshots or handles ServerReplay save events.

`CaptureCoordinator` continues to generate connection IDs and write them into
source records and replay timeline markers. It no longer publishes heartbeat
or connection state outside the capture session.

The source writer’s internal epoch finalization remains. It flushes and
fsyncs bytes, atomically renames `events.jsonl.inprogress`, and publishes the
hash/count/tick manifest consumed by export, scene extraction, render, and
retention. This is a file-publication invariant, not a Dashboard operation.

## Dashboard UI

Use two addressable views:

- `Artifacts`: capture sessions, Flashback archives, and exported datasets;
- `Renders`: render workers, render jobs, attempts, progress, and errors.

The artifact view opens with the existing dataset/sample inspection workflow.
Capture and replay lists show filesystem facts and provenance identifiers.
They do not show live-player or server controls.

A dataset can submit RGB rendering from its detail view. If it has multiple
subjects, the UI requires a subject selection. If no matching saved Flashback
archive exists, RGB is shown as unavailable with the archive constraint.

Replace `OperationsView` with focused artifact and render components. Keep
route-level components as composition surfaces and use typed API contracts in
the composable instead of `any`.

## Error Handling

- A missing artifact root produces an empty collection.
- A root that is a symlink or non-directory produces a bounded catalog issue.
- Invalid capture entries or replay archives are reported independently; one
  bad artifact does not hide valid siblings.
- A replay file changing during inspection is rejected for that scan.
- A render request with no exact replay match returns HTTP 409.
- A dataset with ambiguous subjects returns HTTP 409 until the caller selects
  one.
- Dataset integrity failures retain the existing DatasetViewer status codes.
- Broker publication failure leaves a visible queued job pending publication.
- Render cancellation and retry retain lease and state fencing.

## Migration And Compatibility

Existing capture sessions, Flashback archives, datasets, render queue
databases, and completed render results remain readable.

The render queue migration adds dataset identity and publication state without
deleting existing rows. Legacy rows may still be displayed, but only new
dataset-scoped jobs can be submitted or retried through the Dashboard.

The removed Dashboard routes intentionally return 404. They are not retained
as deprecated aliases because doing so would preserve the control-plane
contract this refactor removes.

## Verification

- Python unit tests cover artifact containment, metadata parsing, stable
  hashing, API route removal, dataset-scoped render submission, retry, and
  durable broker publication.
- Kotlin tests cover archive metadata identity without a control spool and
  coordinator shutdown without control-plane publication.
- Dashboard typecheck and production build cover typed API contracts and both
  views.
- Python formatting, lint, typecheck, and tests pass.
- Recorder mod tests and build pass.
- `git diff --check` passes.
- A browser smoke test verifies that the Dashboard contains no Minecraft
  server, Seal, live connection, or `render-ready` controls and that datasets,
  replay archives, render workers, and render jobs remain visible.
