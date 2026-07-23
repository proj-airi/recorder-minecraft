# Precise Current-Flow Mermaid Design

## Goal

Replace the existing conceptual current-flow diagram with one precise, end-to-end Mermaid architecture diagram that can be compared with the implementation during maintenance and incident diagnosis.

The deliverable remains a self-contained HTML file at:

`.superpowers/brainstorm/2230-1784706143/content/current-flow-mermaid.html`

## Diagram Structure

Use one horizontal Mermaid `flowchart LR`. Divide nodes into four explicit runtime boundaries:

1. Client: Minecraft client and browser.
2. Docker Minecraft: Minecraft server, ServerReplay, and recorder-mod.
3. Recorder host: mounted artifacts, control spool, dashboard HTTP/API, dashboard background job, render queue, per-invocation `render-rpc` CLI, attempt workspace, durable render imports, structured dataset, and DatasetViewer.
4. GUI worker host: `render-worker`, content-addressed cache, per-attempt workspace, renderer-mod, and Flashback client.

The diagram must not depict `render-rpc` as a daemon. It is a CLI action launched for each SSH request.

## Required Data Flows

Number and label the following paths in execution order:

1. Capture: the Minecraft server produces ServerReplay archives, recorder JSONL epochs, and control status/connection/replay ledgers through mounted directories.
2. Seal and generate: the browser calls the authenticated Dashboard API; the dashboard job writes a `seal_connection` request, waits for the recorder response, validates sealed coverage, and calls `export_episode`.
3. Queue and claim: the browser submits Render RGB through the Dashboard API, which writes the persistent render queue. The GUI worker registers and claims through SSH JSON RPC.
4. Prepare and transfer: `render-rpc` resolves the exact replay segments, validates identity and hashes, hardlinks pinned replay files into the attempt directory, and conditionally generates a structured-HUD sidecar. The worker downloads only pinned replay files and the optional HUD sidecar via rsync; the full plan stays on the recorder host. Portable render requests travel as bounded SSH JSON RPC responses.
5. Render and finalize: the worker launches one local renderer client, packages PNG and optional voxel artifacts into integrity-bound bundles, uploads them into the attempt upload directory, and calls finalize. The recorder host verifies and imports bundles under `artifacts/exports/render-jobs/<job-id>/`, attaches exact-key modalities, and atomically re-exports the dataset.
6. View: the browser accesses datasets, samples, frames, and render state only through the Dashboard API and DatasetViewer. It never reads SQLite databases or dataset files directly.

## Paths and Configuration

Show default paths while marking the runtime root as configurable through `paths.runtime`:

- `.mc-recorder/control`
- `.mc-recorder/dashboard.sqlite3`
- `.mc-recorder/render-queue.sqlite3`
- `.mc-recorder/render-rpc/attempts/<attempt-id>`
- `artifacts/captures/<session-id>`
- `artifacts/replays/...`
- `artifacts/exports/<selection>.dataset`
- `artifacts/exports/render-jobs/<job-id>/<segment-id>`

The dashboard endpoint should be labeled `0.0.0.0:8765` by default, not `localhost:8765`. Its connection is Basic Auth HTTP, with a note that mutating API calls also require same-origin and CSRF checks.

## Visual Language

Use Mermaid `classDef` styles to distinguish:

- clients;
- runtime services and processes;
- persistent stores and artifact directories;
- worker-side components;
- integrity and ownership boundaries.

Use edge labels and distinct link styles to differentiate:

- HTTP/API;
- SSH JSON RPC;
- rsync transfer;
- local calls and file access.

Include a compact HTML legend below the diagram. Keep the existing dark presentation but allow a wider canvas and responsive horizontal scrolling so the detailed graph remains readable.

## Failure and State Boundaries

Show only failure states that materially affect the architecture:

- a replay still being saved causes `replay_pending` and queue defer;
- attempt heartbeat and lease fencing prevent stale workers from publishing;
- a claimed worker failure is fail-stop before another claim;
- finalize verification failure marks the job failed without replacing the verified dataset;
- verified incomplete coverage produces a `partial` job and explicit missing RGB samples.

Do not expand every exception or queue transition into a full state machine.

## Verification

After editing:

1. Inspect the Mermaid source for the required nodes, labels, paths, and runtime boundaries.
2. Render or open the HTML and check for Mermaid syntax errors, clipped labels, overlapping nodes, and unreadable edge routing.
3. Confirm the diagram does not imply direct browser access to datasets or queues, direct worker access to replay roots, plan download, or a persistent `render-rpc` daemon.
4. Run the relevant dashboard, render queue, RPC, transfer, worker, attach, and CLI Python tests.
5. Run `git diff --check` without modifying unrelated working-tree changes.
