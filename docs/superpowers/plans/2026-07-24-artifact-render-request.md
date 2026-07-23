# Artifact Render Request Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the Dashboard queue RGB rendering directly from a completed Flashback artifact while a headless preparer snapshots append-only recorder bytes and publishes the required connection-scoped dataset.

**Architecture:** Extend the durable render queue with a pre-dataset preparation state and lease. A new capture snapshot module materializes a verified private episode from finalized files plus a stable prefix of an active file; `render-preparer` exports and verifies the dataset, binds its opaque ID to the request, and hands the job to the existing RabbitMQ/GUI pipeline.

**Tech Stack:** Python 3.13, `unittest`, SQLite, Vue 3 Composition API, TypeScript, Vite, Docker Compose, Pixi.

---

## File Map

- Create `apps/minerec/src/minerec/processing/capture/snapshot.py`: read-only append-prefix verification and private snapshot materialization.
- Create `apps/minerec/tests/test_capture_snapshot.py`: snapshot integrity, identity, and concurrent-append tests.
- Modify `apps/minerec/src/minerec/processing/capture/exporter.py`: accept authenticated snapshot provenance in Dataset V2 output.
- Modify `apps/minerec/tests/test_episodes_export.py`: export a disconnected connection from an active source snapshot.
- Modify `apps/minerec/src/minerec/render/control/queue.py`: persist and lease pre-dataset artifact render requests.
- Modify `apps/minerec/tests/test_render_queue.py`: preparation state, lease, deduplication, cancellation, retry, and publication tests.
- Create `apps/minerec/src/minerec/render/control/preparer.py`: convert one artifact request into a verified dataset-scoped queued job.
- Create `apps/minerec/tests/test_render_preparer.py`: preparer success, reuse, source conflict, and failure tests.
- Modify `apps/minerec/src/minerec/cli.py`: add the long-running `render-preparer` command.
- Modify `apps/minerec/tests/test_render_cli.py`: parser and loop wiring tests.
- Modify `deploy/docker-compose.yml`: run one headless preparer service with capture/replay/export/runtime mounts.
- Modify `apps/minerec/src/minerec/serve/dashboard/service.py`: submit artifact-scoped requests.
- Modify `apps/minerec/src/minerec/serve/dashboard/server.py`: expose the artifact render POST route.
- Modify `apps/minerec/tests/test_dashboard_service.py` and `test_dashboard_http.py`: service and HTTP contracts.
- Modify `apps/dashboard/src/components/ArtifactBrowser.vue`: add replay render settings and action.
- Modify `apps/dashboard/src/composables/useDashboard.ts` and `types/dashboard.ts`: typed request and preparation states.
- Modify `apps/dashboard/src/components/RenderPipeline.vue`: display dataset preparation progress.
- Modify `docs/specs/dataset-v2.md`, `docs/specs/render-transfer-v1.md`, `README.md`, and `apps/minerec/README.md`: persisted provenance and operator workflow.

### Task 1: Append-Only Connection Snapshot

**Files:**
- Create: `apps/minerec/src/minerec/processing/capture/snapshot.py`
- Create: `apps/minerec/tests/test_capture_snapshot.py`

- [ ] **Step 1: Write the failing finalized-plus-active snapshot test**

Create a sealed epoch and one `events.jsonl.inprogress` containing
`player_join`, two complete player-state ticks, `player_leave`, and a partial
JSON tail. Assert:

```python
with snapshot_connection(
    episode,
    runtime,
    player_uuid=PLAYER,
    connection_id=CONNECTION,
) as snapshot:
    active = snapshot.provenance["segments"][-1]
    self.assertEqual("active_prefix", active["kind"])
    self.assertEqual(hashlib.sha256(expected_prefix).hexdigest(), active["sha256"])
    self.assertTrue((snapshot.episode / "epochs/epoch-000001/events.jsonl").is_file())
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```sh
pixi run python -m unittest apps.minerec.tests.test_capture_snapshot -v
```

Expected: import failure for `minerec.processing.capture.snapshot`.

- [ ] **Step 3: Implement the snapshot data types and bounded reader**

Expose:

```python
@dataclass(frozen=True)
class CaptureSnapshot:
    episode: Path
    source_episode: Path
    session_id: str
    player_uuid: str
    connection_id: str
    provenance: dict[str, Any]

@contextmanager
def snapshot_connection(
    episode: Path,
    runtime_root: Path,
    *,
    player_uuid: str,
    connection_id: str,
) -> Iterator[CaptureSnapshot]:
    ...
```

Read exactly the initially observed file size, retain bytes through the final
newline, validate every complete line, and reread the retained prefix before
publishing the private snapshot.

- [ ] **Step 4: Add failing source-integrity tests**

Cover:

- append after the observed size succeeds;
- inode replacement fails;
- truncation fails;
- retained-prefix mutation fails;
- malformed complete JSON fails;
- wrong session or epoch index fails;
- non-monotonic sequence or server tick fails;
- missing selected `player_join` or `player_leave` fails;
- symlinked source files fail.

- [ ] **Step 5: Complete private snapshot publication**

Copy verified finalized bytes and the verified active prefix into a staging
episode. Generate finalized epoch manifests with byte count, record count,
SHA-256, sequence/tick bounds, and `rotation_reason: "consumer_snapshot"`.
Copy the session manifest without changing the capture root. Atomically rename
the private staging directory and remove it when the context exits.

- [ ] **Step 6: Run focused tests**

```sh
pixi run python -m unittest apps.minerec.tests.test_capture_snapshot -v
```

Expected: all snapshot tests pass.

- [ ] **Step 7: Commit**

```sh
git add apps/minerec/src/minerec/processing/capture/snapshot.py apps/minerec/tests/test_capture_snapshot.py
git commit -m "feat(capture): snapshot disconnected append-only connections"
```

### Task 2: Snapshot-Provenance Dataset Export

**Files:**
- Modify: `apps/minerec/src/minerec/processing/capture/exporter.py`
- Modify: `apps/minerec/tests/test_episodes_export.py`
- Modify: `docs/specs/dataset-v2.md`

- [ ] **Step 1: Write a failing active-connection export test**

Use `snapshot_connection()` around an active source and call:

```python
result = export_episode(
    snapshot.episode,
    output,
    players=[PLAYER],
    connections=[CONNECTION],
    source_snapshot=snapshot.provenance,
)
manifest = json.loads((result.output / "manifest.json").read_text())
self.assertEqual("append_prefix_v1", manifest["source"]["snapshot"]["format"])
self.assertEqual(CONNECTION, manifest["selection"]["connections"][0])
```

Also assert the state rows carry the synthetic segment hashes and the dataset
contains no other connection.

- [ ] **Step 2: Run the export test and verify RED**

```sh
pixi run python -m unittest apps.minerec.tests.test_episodes_export.EpisodeExportTest.test_exports_active_connection_snapshot -v
```

Expected: `export_episode()` rejects the unknown `source_snapshot` argument.

- [ ] **Step 3: Add authenticated source provenance**

Add an optional keyword-only `source_snapshot: dict[str, Any] | None = None`.
Validate its format, source path, session, player, connection, segment hashes,
byte counts, and record counts before writing the dataset manifest. When
present, write:

```python
"source": {
    "episode": source_snapshot["source_episode"],
    "manifest_sha256": session_manifest_sha,
    "sealed_epochs": len(verified_epochs),
    "active_epochs_skipped": 0,
    "epochs": [epoch.manifest_entry() for epoch in verified_epochs],
    "snapshot": source_snapshot,
}
```

The normal CLI export path remains unchanged when no snapshot is supplied.

- [ ] **Step 4: Test provenance rejection**

Assert export rejects snapshot session, connection, hash, byte-count, and
segment-count mismatches before atomic dataset publication.

- [ ] **Step 5: Update Dataset V2**

Document `source.snapshot.format = "append_prefix_v1"`, required segment
fields, and that `consumer_snapshot` describes consumer-generated evidence,
not recorder finalization.

- [ ] **Step 6: Run export and snapshot suites**

```sh
pixi run python -m unittest \
  apps.minerec.tests.test_capture_snapshot \
  apps.minerec.tests.test_episodes_export -v
```

- [ ] **Step 7: Commit**

```sh
git add apps/minerec/src/minerec/processing/capture/exporter.py apps/minerec/tests/test_episodes_export.py docs/specs/dataset-v2.md
git commit -m "feat(dataset): export authenticated capture snapshots"
```

### Task 3: Durable Preparation State

**Files:**
- Modify: `apps/minerec/src/minerec/render/control/queue.py`
- Modify: `apps/minerec/tests/test_render_queue.py`

- [ ] **Step 1: Write failing artifact-request tests**

Assert:

```python
request = store.create_artifact_request(artifact_payload())
self.assertEqual("preparing_dataset", request["state"])
self.assertIsNone(request["dataset_id"])
self.assertEqual([], store.pending_publication())

lease = store.claim_preparation("preparer-a", lease_seconds=30)
self.assertEqual(request["id"], lease["job"]["id"])
queued = store.bind_dataset(
    lease["job"]["id"],
    lease["lease_token"],
    dataset_id="d" * 32,
    start_tick=10,
    end_tick=40,
)
self.assertEqual("queued", queued["state"])
```

- [ ] **Step 2: Run queue tests and verify RED**

```sh
pixi run python -m unittest apps.minerec.tests.test_render_queue -v
```

Expected: `create_artifact_request` and preparation lease methods do not
exist.

- [ ] **Step 3: Replace the queue schema**

Make `dataset_id` nullable and add:

```sql
source_artifact_id TEXT;
preparer_id TEXT;
preparation_lease_token TEXT;
preparation_lease_expires_at REAL;
```

Add `preparing_dataset` to active states. Keep `pending_publication()` limited
to `state = 'queued' AND dataset_id IS NOT NULL`.

- [ ] **Step 4: Implement preparation methods**

Add:

```python
def create_artifact_request(self, payload: dict[str, Any]) -> dict[str, Any]: ...
def claim_preparation(self, preparer_id: str, *, lease_seconds: int = 60) -> dict[str, Any] | None: ...
def heartbeat_preparation(self, job_id: str, lease_token: str, *, message: str) -> dict[str, Any]: ...
def bind_dataset(self, job_id: str, lease_token: str, *, dataset_id: str, start_tick: int, end_tick: int) -> dict[str, Any]: ...
def fail_preparation(self, job_id: str, lease_token: str, error: str) -> dict[str, Any]: ...
```

Fingerprint artifact requests from artifact identity plus render settings.
Expire preparation leases back to `preparing_dataset`; never set
`published_at` during preparation.

- [ ] **Step 5: Add lifecycle regression tests**

Cover duplicate active clicks, expired preparation lease recovery, stale-token
fencing, cancel before dataset binding, retry after preparation failure,
schema mismatch, and unchanged GUI worker lifecycle after binding.

- [ ] **Step 6: Run queue tests**

```sh
pixi run python -m unittest apps.minerec.tests.test_render_queue -v
```

- [ ] **Step 7: Commit**

```sh
git add apps/minerec/src/minerec/render/control/queue.py apps/minerec/tests/test_render_queue.py
git commit -m "feat(render): persist artifact preparation requests"
```

### Task 4: Headless Render Preparer

**Files:**
- Create: `apps/minerec/src/minerec/render/control/preparer.py`
- Create: `apps/minerec/tests/test_render_preparer.py`
- Modify: `apps/minerec/src/minerec/cli.py`
- Modify: `apps/minerec/tests/test_render_cli.py`
- Modify: `deploy/docker-compose.yml`

- [ ] **Step 1: Write a failing preparer success test**

Build a valid artifact catalog fixture and active capture fixture, then:

```python
prepared = RenderPreparer(config).run_once()
self.assertEqual(request["id"], prepared["id"])
self.assertEqual("queued", prepared["state"])
self.assertRegex(prepared["dataset_id"], r"^[0-9a-f]{32}$")
self.assertTrue((exports / f"{SESSION}-{CONNECTION}.dataset").is_dir())
```

- [ ] **Step 2: Run the test and verify RED**

```sh
pixi run python -m unittest apps.minerec.tests.test_render_preparer -v
```

Expected: import failure for `minerec.render.control.preparer`.

- [ ] **Step 3: Implement `RenderPreparer.run_once()`**

The method claims one preparation lease, resolves the exact verified replay
artifact, resolves the capture session, snapshots the selected connection,
exports to `<session>-<connection>.dataset`, verifies it with
`DatasetViewer`, reads connection bounds, and calls `bind_dataset()`.

Heartbeat before and after snapshot/export. Convert `RecorderError`,
`DatasetViewerError`, and source disappearance into `fail_preparation()`.
Never catch `KeyboardInterrupt` or `SystemExit`.

- [ ] **Step 4: Add dataset reuse and failure tests**

Cover:

- reuse of an intact exact-selection dataset;
- refusal to reuse a mismatched dataset;
- replay artifact replacement after request creation;
- missing capture session;
- missing join/leave boundary;
- export failure leaves no published partial directory;
- a canceled request cannot be bound after work completes.

- [ ] **Step 5: Add CLI and Compose service**

Register:

```text
minerec render-preparer
```

The command loops `run_once()`, waits one second when empty, handles SIGTERM,
and exits nonzero only for process-level initialization errors. Add a Compose
service mounting `/captures`, `/replays`, `/artifacts/exports`,
`/.mc-recorder`, and `/recorder.toml`.

- [ ] **Step 6: Run focused tests and validate Compose**

```sh
pixi run python -m unittest \
  apps.minerec.tests.test_render_preparer \
  apps.minerec.tests.test_render_cli -v
docker compose --env-file deploy/.env --file deploy/docker-compose.yml config --quiet
```

- [ ] **Step 7: Commit**

```sh
git add apps/minerec/src/minerec/render/control/preparer.py apps/minerec/src/minerec/cli.py apps/minerec/tests/test_render_preparer.py apps/minerec/tests/test_render_cli.py deploy/docker-compose.yml
git commit -m "feat(render): prepare artifact requests headlessly"
```

### Task 5: Artifact Render API

**Files:**
- Modify: `apps/minerec/src/minerec/serve/dashboard/service.py`
- Modify: `apps/minerec/src/minerec/serve/dashboard/server.py`
- Modify: `apps/minerec/tests/test_dashboard_service.py`
- Modify: `apps/minerec/tests/test_dashboard_http.py`

- [ ] **Step 1: Write failing service tests**

Assert `create_artifact_render_request()` resolves an exact verified artifact,
persists immutable source identity and render settings, and returns an
idempotent request. Cover unknown artifact, unbound connection, truncated
catalog, and invalid render settings.

- [ ] **Step 2: Run service tests and verify RED**

```sh
pixi run python -m unittest apps.minerec.tests.test_dashboard_service -v
```

- [ ] **Step 3: Implement the service method**

Add:

```python
def create_artifact_render_request(
    self,
    artifact_id: str,
    *,
    width: int,
    height: int,
    fps: int = 20,
    no_gui: bool = False,
) -> dict[str, Any]:
    ...
```

Use one `ArtifactCatalog.scan()` result, require an exact bound replay, and
pass only catalog-derived identity into `RenderQueueStore`.

- [ ] **Step 4: Write failing HTTP tests**

Test authenticated and CSRF-protected:

```text
POST /api/v1/replay-artifacts/{artifact_id}/render
```

Assert 201/200 for creation/deduplication, 404 for unknown artifact, 409 for
catalog/source conflict, 400 for invalid JSON/settings, and 404 for any
Minecraft server control route.

- [ ] **Step 5: Implement the route**

Add one anchored artifact-ID regex and route handler. Keep request-body size,
same-origin, CSRF, and JSON-object validation consistent with the dataset
render endpoint.

- [ ] **Step 6: Run Dashboard backend tests**

```sh
pixi run python -m unittest \
  apps.minerec.tests.test_dashboard_service \
  apps.minerec.tests.test_dashboard_http -v
```

- [ ] **Step 7: Commit**

```sh
git add apps/minerec/src/minerec/serve/dashboard apps/minerec/tests/test_dashboard_service.py apps/minerec/tests/test_dashboard_http.py
git commit -m "feat(dashboard): submit render requests from replay artifacts"
```

### Task 6: Replay-Row Render UI

**Files:**
- Modify: `apps/dashboard/src/types/dashboard.ts`
- Modify: `apps/dashboard/src/composables/useDashboard.ts`
- Modify: `apps/dashboard/src/components/ArtifactBrowser.vue`
- Modify: `apps/dashboard/src/components/RenderPipeline.vue`

- [ ] **Step 1: Extend typed contracts**

Make `RenderJob.dataset_id` nullable, add `source_artifact_id`, and include
`preparing_dataset` in the displayed state vocabulary. Add an artifact render
emit contract:

```ts
renderArtifact: [
  artifactId: string,
  resolution: string,
  noGui: boolean,
]
```

- [ ] **Step 2: Implement the composable request**

POST render settings to
`/api/v1/replay-artifacts/${artifactId}/render`, show the returned state,
switch to `renders`, and refresh jobs. Preserve the existing dataset render
method.

- [ ] **Step 3: Add replay render controls**

In `ArtifactBrowser.vue`, show resolution, `Hide GUI`, and `Render RGB` in the
Flashback section. Disable the action when `connection_id` is null. Keep
capture rows read-only and do not add server/Seal controls.

- [ ] **Step 4: Display preparation state**

`RenderPipeline.vue` shows source artifact identity while `dataset_id` is
null and the progress message emitted by the preparer. Retry and cancel use
the existing job actions.

- [ ] **Step 5: Typecheck and build**

```sh
pnpm -F @mc-recorder/dashboard typecheck
pnpm -F @mc-recorder/dashboard build
```

- [ ] **Step 6: Commit**

```sh
git add apps/dashboard/src
git commit -m "feat(dashboard-ui): render directly from replay artifacts"
```

### Task 7: Documentation And End-To-End Verification

**Files:**
- Modify: `docs/specs/render-transfer-v1.md`
- Modify: `README.md`
- Modify: `apps/minerec/README.md`

- [ ] **Step 1: Update persisted and operator contracts**

Document preparation states and leases, the `render-preparer` process, the
one-click replay workflow, append-prefix evidence, and the continued
requirement for a logged-in host GUI worker.

- [ ] **Step 2: Run the full repository checks**

```sh
pixi run py-check
pixi run test-python
pixi run build-recorder-mod
pixi run build-scene-extractor-mod
pixi run build-renderer-mod
pnpm typecheck
pnpm build:dashboard
git diff --check
```

- [ ] **Step 3: Recreate the incompatible runtime database and restart**

Stop Compose, remove only the generated render queue database and private
preparation staging directories, rebuild the minerec image/dashboard assets,
and start Compose with health waits.

- [ ] **Step 4: Verify with the current real artifacts**

Submit the current Flashback artifact from the Dashboard while its recorder
connection still resides in an active epoch. Confirm:

- a connection-scoped dataset is atomically published;
- the request reaches `queued`;
- RabbitMQ contains the corresponding task;
- no Minecraft stop/start request occurs;
- the source capture and replay hashes remain unchanged.

- [ ] **Step 5: Browser smoke test**

Use `agent-browser` with Dashboard Basic Auth. Verify only `Artifacts` and
`Renders` views exist, the Flashback row exposes `Render RGB`, clicking it
shows `preparing_dataset` or `queued`, and no Docker Compose, server, Seal, or
live-player controls appear.

- [ ] **Step 6: Commit**

```sh
git add docs/specs/render-transfer-v1.md README.md apps/minerec/README.md
git commit -m "docs: describe artifact render preparation"
```
