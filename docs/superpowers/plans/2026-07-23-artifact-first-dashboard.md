# Artifact-First Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Minecraft control-oriented Dashboard with a filesystem artifact viewer and dataset-scoped render pipeline monitor/operator.

**Architecture:** A new `ArtifactCatalog` inspects capture directories and saved Flashback archives without reading recorder control files. `DashboardService` composes that catalog with `DatasetViewer` and `RenderQueueStore`; render jobs are created from verified dataset subjects, while the broker uses a durable queue outbox instead of `render-ready`. The recorder mod keeps archive identity injection and source-file publication but removes its Dashboard control spool.

**Tech Stack:** Python 3.13, `unittest`, SQLite, RabbitMQ/pika, Kotlin/Fabric/JUnit 5, Vue 3 Composition API, TypeScript, Vite, pnpm, Pixi.

---

## File Map

- Create `apps/minerec/src/minerec/processing/artifacts/catalog.py`: secure capture and Flashback artifact discovery.
- Create `apps/minerec/tests/test_artifact_catalog.py`: artifact catalog behavior and containment tests.
- Modify `apps/minerec/src/minerec/render/control/sources.py`: resolve replay archives from embedded metadata instead of control ledgers.
- Modify `apps/minerec/src/minerec/render/control/queue.py`: dataset-scoped jobs and durable RabbitMQ publication state.
- Modify `apps/minerec/src/minerec/render/control/broker.py`: publish pending queue jobs without `render-ready`.
- Modify `apps/minerec/src/minerec/serve/dashboard/service.py`: artifact composition and dataset-scoped render submission.
- Modify `apps/minerec/src/minerec/serve/dashboard/server.py`: remove server/recording routes and add artifact/dataset-render routes.
- Delete `apps/minerec/src/minerec/serve/dashboard/jobs.py`: remove generic lifecycle/generation jobs.
- Modify Python tests beside each affected module.
- Modify `mods/recorder-mod/.../ReplaySegmentTracker.kt`: retain archive metadata identity only.
- Modify `mods/recorder-mod/.../CaptureCoordinator.kt` and `CaptureRuntime.kt`: remove control-spool publication.
- Delete `mods/recorder-mod/.../RecorderControlPlane.kt` and its test.
- Create `apps/dashboard/src/types/dashboard.ts`: typed HTTP contracts.
- Replace `apps/dashboard/src/components/OperationsView.vue` with focused artifact/render components.
- Modify `apps/dashboard/src/composables/useDashboard.ts` and `pages/index.vue`: two-view artifact-first workflow.
- Modify `deploy/docker-compose.yml`, READMEs, and persisted contract docs to remove control-spool claims.

### Task 1: Filesystem Artifact Catalog

**Files:**
- Create: `apps/minerec/src/minerec/processing/artifacts/__init__.py`
- Create: `apps/minerec/src/minerec/processing/artifacts/catalog.py`
- Create: `apps/minerec/tests/test_artifact_catalog.py`

- [ ] **Step 1: Write failing capture catalog tests**

Add tests that create complete, open, and incomplete capture directories and assert the public vocabulary:

```python
result = ArtifactCatalog(captures, replays).scan()
self.assertEqual("complete", result.capture_sessions[0].state)
self.assertEqual(1, result.capture_sessions[0].published_epoch_count)
self.assertNotIn("sealed", result.capture_sessions[0].as_json())
```

- [ ] **Step 2: Run the capture catalog tests and verify RED**

Run:

```sh
pixi run python -m unittest apps.minerec.tests.test_artifact_catalog -v
```

Expected: import failure because `minerec.processing.artifacts.catalog` does not exist.

- [ ] **Step 3: Implement capture discovery**

Create immutable dataclasses and map existing `EpisodeInfo` states:

```python
@dataclass(frozen=True)
class CaptureSessionArtifact:
    session_id: str
    relative_path: str
    state: Literal["open", "complete", "incomplete", "empty"]
    size_bytes: int
    epoch_count: int
    published_epoch_count: int
    unpublished_epoch_count: int
    first_tick: int | None
    last_tick: int | None
```

Use `list_episodes()` and convert internal `active` to `open`, internal
`sealed` to `complete`, and epoch counts to published/unpublished counts.

- [ ] **Step 4: Write failing Flashback archive tests**

Cover:

- valid `metadata.json`, `.flashback`, and `arcade_replay_meta.json`;
- missing `mc_recorder`;
- symlinked archive;
- archive outside the configured root;
- file mutation between stat reads;
- one invalid sibling does not hide one valid archive.

- [ ] **Step 5: Run the replay catalog tests and verify RED**

Run the same unittest command and confirm failures are for missing replay
discovery behavior.

- [ ] **Step 6: Implement replay discovery**

Expose:

```python
@dataclass(frozen=True)
class ReplayArchiveArtifact:
    artifact_id: str
    relative_path: str
    size_bytes: int
    sha256: str
    session_id: str
    segment_id: str
    segment_ordinal: int
    player_uuid: str
    connection_id: str | None
    flashback_capture_contract: str | None
```

Derive `artifact_id` from canonical archive identity plus SHA-256. Return
bounded `ArtifactIssue` entries for invalid candidates.

- [ ] **Step 7: Run tests and commit**

```sh
pixi run python -m unittest apps.minerec.tests.test_artifact_catalog -v
git add apps/minerec/src/minerec/processing/artifacts apps/minerec/tests/test_artifact_catalog.py
git -c commit.gpgsign=false commit -m "feat(artifacts): catalog filesystem capture and replay artifacts"
```

### Task 2: Replay Resolution Without Control Ledgers

**Files:**
- Modify: `apps/minerec/src/minerec/render/control/sources.py`
- Modify: `apps/minerec/src/minerec/render/control/rpc.py`
- Modify: `apps/minerec/tests/test_render_sources.py`
- Modify: `apps/minerec/tests/test_render_rpc.py`

- [ ] **Step 1: Write failing direct-resolution tests**

Replace ledger fixtures with two saved Flashback archives carrying the same
session/player/connection and consecutive ordinals:

```python
sources = resolve_replay_segments(
    replays_root=replays,
    session_id=SESSION,
    player_uuid=PLAYER,
    connection_id=CONNECTION,
)
self.assertEqual([0, 1], [source.segment_ordinal for source in sources])
```

Also assert duplicate segment IDs/ordinals, unstable bytes, mismatched archive
identity, and missing exact matches fail.

- [ ] **Step 2: Run focused tests and verify RED**

```sh
pixi run python -m unittest apps.minerec.tests.test_render_sources apps.minerec.tests.test_render_rpc -v
```

Expected: old signature requires `control_root` and reads a ledger.

- [ ] **Step 3: Reuse the artifact scanner in replay resolution**

Change the public resolver to:

```python
def resolve_replay_segments(
    *,
    replays_root: Path,
    session_id: str,
    player_uuid: str,
    connection_id: str,
) -> list[ReplaySegmentSource]:
```

Resolve exact matches from verified `ReplayArchiveArtifact` values, reject
ambiguous duplicate identity, and preserve hash/size/contract fields.

- [ ] **Step 4: Update render RPC call sites**

Remove `control_root` from replay source resolution while preserving attempt
containment, pinned input, and finalize verification.

- [ ] **Step 5: Run tests and commit**

```sh
pixi run python -m unittest apps.minerec.tests.test_render_sources apps.minerec.tests.test_render_rpc -v
git add apps/minerec/src/minerec/render/control/sources.py apps/minerec/src/minerec/render/control/rpc.py apps/minerec/tests/test_render_sources.py apps/minerec/tests/test_render_rpc.py
git -c commit.gpgsign=false commit -m "refactor(render): resolve saved replays from archive metadata"
```

### Task 3: Dataset-Scoped Render Queue And Durable Publication

**Files:**
- Modify: `apps/minerec/src/minerec/render/control/queue.py`
- Modify: `apps/minerec/src/minerec/render/control/broker.py`
- Modify: `apps/minerec/src/minerec/cli.py`
- Modify: `apps/minerec/tests/test_render_queue.py`
- Modify: `apps/minerec/tests/test_render_broker.py`

- [ ] **Step 1: Write failing queue migration and outbox tests**

Assert new jobs require a 32-character opaque `dataset_id`, expose
`published_at`, and can be selected/marked:

```python
job = store.create({"dataset_id": DATASET_ID, **payload})
self.assertIsNone(job["published_at"])
self.assertEqual([job["id"]], [item["id"] for item in store.pending_publication()])
store.mark_published(job["id"])
self.assertIsNotNone(store.get(job["id"])["published_at"])
```

Create a legacy database fixture and assert initialization preserves its rows.

- [ ] **Step 2: Run queue tests and verify RED**

```sh
pixi run python -m unittest apps.minerec.tests.test_render_queue -v
```

- [ ] **Step 3: Implement the queue migration**

Add nullable `dataset_id` and `published_at` columns, backfill `dataset_id`
from payload JSON for existing dataset jobs, and implement:

```python
def pending_publication(self, limit: int = 50) -> list[dict[str, Any]]: ...
def mark_published(self, job_id: str) -> dict[str, Any]: ...
```

Clear `published_at` whenever an expired attempt returns a job to `queued`.

- [ ] **Step 4: Write failing broker tests**

Replace `render-ready` fixtures with pending jobs. Assert:

- successful publish marks the job;
- publish failure leaves it pending;
- published jobs are not sent again;
- a requeued expired attempt becomes publishable again;
- RabbitMQ messages contain `dataset_id`.

- [ ] **Step 5: Run broker tests and verify RED**

```sh
pixi run python -m unittest apps.minerec.tests.test_render_broker -v
```

- [ ] **Step 6: Implement pending-job dispatch**

Replace `dispatch_mod_emitted_render_jobs()` with:

```python
def dispatch_pending_render_jobs(
    store: RenderQueueStore,
    *,
    publish: Callable[[dict[str, Any]], None],
    limit: int = 50,
) -> int:
```

Update `render-dispatcher` CLI to call it without `control_root`.

- [ ] **Step 7: Run tests and commit**

```sh
pixi run python -m unittest apps.minerec.tests.test_render_queue apps.minerec.tests.test_render_broker apps.minerec.tests.test_render_cli -v
git add apps/minerec/src/minerec/render/control apps/minerec/src/minerec/cli.py apps/minerec/tests/test_render_queue.py apps/minerec/tests/test_render_broker.py apps/minerec/tests/test_render_cli.py
git -c commit.gpgsign=false commit -m "refactor(render): dispatch dataset jobs from durable queue"
```

### Task 4: Artifact-First Dashboard Backend

**Files:**
- Modify: `apps/minerec/src/minerec/serve/dashboard/service.py`
- Modify: `apps/minerec/src/minerec/serve/dashboard/server.py`
- Delete: `apps/minerec/src/minerec/serve/dashboard/jobs.py`
- Modify: `apps/minerec/tests/test_dashboard_service.py`
- Modify: `apps/minerec/tests/test_dashboard_http.py`
- Delete: `apps/minerec/tests/test_dashboard_jobs.py`

- [ ] **Step 1: Write failing service tests**

Test that:

```python
catalog = service.artifacts()
self.assertIn("capture_sessions", catalog)
self.assertIn("replay_archives", catalog)

job = service.create_dataset_render_job(DATASET_ID, width=640, height=360)
self.assertEqual(DATASET_ID, job["payload"]["dataset_id"])
```

Cover one-subject defaulting, multi-subject ambiguity, explicit subject
selection, no matching replay archive, complete RGB rejection, retry after
source revalidation, and legacy RGB replacement.

- [ ] **Step 2: Run service tests and verify RED**

```sh
pixi run python -m unittest apps.minerec.tests.test_dashboard_service -v
```

- [ ] **Step 3: Simplify `DashboardService`**

The constructor retains only:

```python
self.artifact_catalog = ArtifactCatalog(config.paths.captures, config.paths.replays)
self.dataset_viewer = DatasetViewer(config.paths.exports, config.paths.runtime)
self.dataset_index = DatasetIndex(self.dataset_viewer)
self.render_queue = RenderQueueStore(config.paths.runtime / "render-queue.sqlite3")
```

Delete compose status, capture status, connection rows, generic jobs, dataset
generation, scene-generation orchestration, and sealed-source caches.

Build render payloads from `get_dataset_metadata()` and
`list_player_connections()`.

- [ ] **Step 4: Write failing HTTP route tests**

Assert:

- `GET /api/v1/artifacts` returns filesystem artifacts;
- `POST /api/v1/datasets/{id}/render` creates a job;
- render cancel/retry remain protected by auth, same-origin, and CSRF;
- removed status/recording/server/generate routes return 404;
- all dataset viewer routes remain unchanged.

- [ ] **Step 5: Run HTTP tests and verify RED**

```sh
pixi run python -m unittest apps.minerec.tests.test_dashboard_http -v
```

- [ ] **Step 6: Replace the HTTP route surface**

Add `ARTIFACTS_PATH` and `DATASET_RENDER_PATH` routing. Keep one traceable
handler docstring on `do_GET` and `do_POST` describing the upstream HTTP
request and downstream service method/response writer.

Remove `JobManager` shutdown and job routes from `DashboardApplication`.

- [ ] **Step 7: Run tests and commit**

```sh
pixi run python -m unittest apps.minerec.tests.test_dashboard_service apps.minerec.tests.test_dashboard_http apps.minerec.tests.test_dataset_viewer -v
git add apps/minerec/src/minerec/serve/dashboard apps/minerec/tests/test_dashboard_service.py apps/minerec/tests/test_dashboard_http.py
git add -u apps/minerec/tests/test_dashboard_jobs.py
git -c commit.gpgsign=false commit -m "refactor(dashboard): operate on artifacts and datasets"
```

### Task 5: Remove Recorder Dashboard Control Spool

**Files:**
- Modify: `mods/recorder-mod/src/main/kotlin/dev/mcdata/recorder/DatasetRecorderMod.kt`
- Modify: `mods/recorder-mod/src/main/kotlin/dev/mcdata/recorder/capture/CaptureRuntime.kt`
- Modify: `mods/recorder-mod/src/main/kotlin/dev/mcdata/recorder/capture/CaptureCoordinator.kt`
- Modify: `mods/recorder-mod/src/main/kotlin/dev/mcdata/recorder/capture/ReplaySegmentTracker.kt`
- Delete: `mods/recorder-mod/src/main/kotlin/dev/mcdata/recorder/control/RecorderControlPlane.kt`
- Modify: `mods/recorder-mod/src/test/kotlin/dev/mcdata/recorder/capture/ReplaySegmentTrackerTest.kt`
- Modify: `mods/recorder-mod/src/test/kotlin/dev/mcdata/recorder/capture/CaptureCoordinatorShutdownTest.kt`
- Delete: `mods/recorder-mod/src/test/kotlin/dev/mcdata/recorder/control/RecorderControlPlaneTest.kt`

- [ ] **Step 1: Rewrite tracker tests for metadata-only behavior**

Assert recorder start plus connection binding writes:

```kotlin
assertEquals(SESSION, metadata["mc_recorder"].asJsonObject["session_id"].asString)
assertEquals(CONNECTION, metadata["mc_recorder"].asJsonObject["connection_id"].asString)
assertEquals(0L, metadata["mc_recorder"].asJsonObject["segment_ordinal"].asLong)
```

Remove assertions about ledger and `render-ready` files.

- [ ] **Step 2: Run recorder tests and verify RED**

```sh
pixi run ./mods/recorder-mod/gradlew -p mods/recorder-mod test --tests '*ReplaySegmentTrackerTest' --tests '*CaptureCoordinatorShutdownTest'
```

- [ ] **Step 3: Remove control publication**

Delete `RecorderControlPlane`. Remove it from runtime/coordinator constructors,
join/leave/shutdown/status paths, and remove `ReplayRecorderSaveEvent`
registration.

Keep `ReplaySegmentTracker` identity allocation, connection binding, and
metadata provider. Remove snapshot publication and saved-output state.

- [ ] **Step 4: Run recorder tests and build**

```sh
pixi run build-recorder-mod
```

- [ ] **Step 5: Commit**

```sh
git add mods/recorder-mod
git -c commit.gpgsign=false commit -m "refactor(recorder): remove dashboard control spool"
```

### Task 6: Artifact And Render Dashboard UI

**Files:**
- Create: `apps/dashboard/src/types/dashboard.ts`
- Create: `apps/dashboard/src/components/ArtifactBrowser.vue`
- Create: `apps/dashboard/src/components/RenderPipeline.vue`
- Modify: `apps/dashboard/src/components/DatasetList.vue`
- Modify: `apps/dashboard/src/composables/useDashboard.ts`
- Modify: `apps/dashboard/src/pages/index.vue`
- Delete: `apps/dashboard/src/components/OperationsView.vue`
- Delete: `apps/dashboard/src/components/ConfirmationDialog.vue` if no longer used
- Modify: `apps/dashboard/src/styles.css`

**Component map:**

- `index.vue`: route-level composition and Artifacts/Renders navigation only.
- `ArtifactBrowser.vue`: capture/replay/dataset lists and dataset render event.
- `DatasetList.vue`: verified dataset selection and per-dataset render command.
- `SampleInspector.vue`: existing sample viewer, unchanged except typed props.
- `RenderPipeline.vue`: worker/job monitoring and cancel/retry events.
- `useDashboard.ts`: typed HTTP state, polling, dataset inspection, and render mutations.

- [ ] **Step 1: Add typed API contracts and make typecheck fail**

Define `ArtifactCatalogResponse`, `DatasetSummary`, `DatasetMetadata`,
`PlayerConnection`, `RenderJob`, and `RenderWorker`. Replace top-level `any`
refs in the composable with these types.

Run:

```sh
pnpm --filter @proj-airi/mc-recorder-dashboard typecheck
```

Expected: failures where old status/recording APIs and component props no
longer satisfy the new contracts.

- [ ] **Step 2: Implement the two focused views**

Remove server, capture heartbeat, storage, recording, Seal, generation, and
recent-operation markup. Use:

```ts
type ViewName = 'artifacts' | 'renders'
```

Poll `/artifacts`, `/datasets`, `/render-jobs`, and `/render-workers`. Submit
RGB through `/datasets/{id}/render`.

- [ ] **Step 3: Complete typed component boundaries**

Use `<script setup lang="ts">`, readonly props, typed emits, `computed` for
derived lists, and `shallowRef` for primitive state. Keep `index.vue` thin.

- [ ] **Step 4: Run frontend checks**

```sh
pnpm --filter @proj-airi/mc-recorder-dashboard typecheck
pnpm --filter @proj-airi/mc-recorder-dashboard build
```

- [ ] **Step 5: Commit**

```sh
git add apps/dashboard
git -c commit.gpgsign=false commit -m "refactor(dashboard-ui): focus on artifacts and renders"
```

### Task 7: Deployment, Contracts, And End-To-End Verification

**Files:**
- Modify: `deploy/docker-compose.yml`
- Modify: `deploy/README.md`
- Modify: `apps/minerec/README.md`
- Modify: `mods/recorder-mod/README.md`
- Modify: `docs/specs/source-record-v1.md`
- Modify: `docs/specs/render-transfer-v1.md`
- Modify: `hack/minecraft-server`

- [ ] **Step 1: Remove control-spool deployment coupling**

Remove the Minecraft `/control` mount and control-directory setup. Keep
`render-dispatcher`, RabbitMQ, and the dashboard runtime database mount because
they belong to the render pipeline.

- [ ] **Step 2: Update persisted-contract documentation**

Document archive-embedded identity as the replay discovery source, internal
epoch publication as a source invariant, dataset-scoped render jobs, and the
durable broker outbox. Remove `render-ready`, server-control, and dashboard
Seal instructions.

- [ ] **Step 3: Run repository checks**

```sh
pixi run py-format-check
pixi run py-lint
pixi run py-typecheck
pixi run test-python
pixi run build-recorder-mod
pnpm --filter @proj-airi/mc-recorder-dashboard typecheck
pnpm --filter @proj-airi/mc-recorder-dashboard build
git diff --check
```

- [ ] **Step 4: Browser smoke test**

Start the dashboard using the existing local command, log in, and verify:

- only Artifacts and Renders views exist;
- no Minecraft server, Seal, live connection, or `render-ready` text exists;
- capture sessions, replay archives, datasets, render workers, and render jobs
  load from their actual stores;
- a dataset render request reaches the queue;
- the Renders view can cancel/retry eligible jobs;
- dataset sample/frame/scene inspection still works.

- [ ] **Step 5: Commit documentation and deployment changes**

```sh
git add deploy apps/minerec/README.md mods/recorder-mod/README.md docs/specs hack/minecraft-server
git -c commit.gpgsign=false commit -m "docs: describe artifact-first recording workflow"
```

- [ ] **Step 6: Final audit**

Search:

```sh
rg -n 'server/start|server/stop|Seal & generate|render-ready|RecorderControlPlane' apps mods deploy docs/specs hack
```

Every remaining match must be either an explicit historical migration note or
an internal source-file publication invariant. Inspect `git status`, all new
commits, and the exact test outputs before claiming completion.
