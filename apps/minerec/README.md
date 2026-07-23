# `minerec` CLI

The Python 3.14 CLI inspects verified sidecar epochs, exports state/action
JSONL, enforces combined capture/replay retention, extracts headless
random-access scene stores, serves the LAN dashboard, and launches local
Flashback RGB/voxel rendering through RabbitMQ-dispatched one-shot worker
processes. Pixi owns the Python environment. proto owns OpenJDK 21 and Gradle.
Docker Compose is required on the recorder host.

The capture stack defaults to the exact `itzg/minecraft-server:2026.7.0-java21` image and the
immutable ServerReplay Modrinth selector `server-replay:TbWIikrT`. The companion
storage monitor, dashboard, and render dispatcher share the Pixi-built
`minerec:local` image.

## Install and initialize

From the workspace root:

```sh
./hack/install
```

Initialization creates `recorder.toml` and workspace directories. Copy
`deploy/.env.example` to `deploy/.env`, review the
[Minecraft EULA](https://aka.ms/MinecraftEULA), then explicitly set
`MC_EULA=TRUE`.

`minerec init` still accepts `--accept-eula` for compatibility with older
workspace config files:

```sh
pixi run minerec init --accept-eula --force
```

All relative paths resolve from the directory containing `recorder.toml`. Use
`minerec --config PATH ...` to operate another workspace.

## Server lifecycle

For the default workspace, use the convenience scripts:

```sh
./hack/minecraft-server start
./hack/minecraft-server restart
./hack/minecraft-server stop
./hack/minecraft-server status
./hack/minecraft-server logs --follow
```

`hack/minecraft-server start` builds and stages `mods/recorder-mod`, writes the
capture and ServerReplay configuration files, and starts Docker Compose while
waiting for Compose health checks. Every connected player is recorded
automatically. ServerReplay writes a Flashback archive per player, including
that player's client-visible chunks, under `paths.replays`; the sidecar writes
combined event slices under `paths.captures`.

`hack/minecraft-server stop` requests a graceful Minecraft shutdown. Sidecar slices are
exportable only after `events.jsonl.inprogress` has been atomically published
as `events.jsonl` with a matching manifest. Completed ServerReplay archives
are independent of sidecar slices.

## LAN dashboard

```sh
export MC_RECORDER_DASHBOARD_USERNAME=recorder
export MC_RECORDER_DASHBOARD_PASSWORD='replace-with-a-long-password'
./hack/dashboard start
```

`hack/dashboard start` runs `pnpm install` and `pnpm build:dashboard` before
serving. Set `MC_RECORDER_SKIP_DASHBOARD_BUILD=1` to reuse an existing
dashboard build, or run `hack/dashboard serve` for raw CLI control.

Host-mode dashboard defaults come from `recorder.toml` and can be changed
without altering credential environment variables:

```toml
[dashboard]
bind = "0.0.0.0"
port = 8765
```

`dashboard serve` runs directly on the recorder host and defaults to
`0.0.0.0:8765` from the `[dashboard]` configuration. `hack/minecraft-server
start` also prepares a Compose dashboard service for containerized runs with
the same workspace mounts. Run host-mode dashboard commands with the same
workspace/configuration and a host account allowed to use Docker Compose. Use
launchd, systemd, or another host service manager to keep host mode running
independently of an interactive shell.
Dataset generation launches the proto-managed Gradle executable for scene
extraction. Service-manager environments often omit proto's shim path; set
`MC_RECORDER_GRADLE` to an absolute executable Gradle path when `gradle` is not
on the service's `PATH`. Relative overrides are rejected and the executable is
invoked directly without a shell.

Both credential environment variables are mandatory. HTTP Basic authentication
applies to HTML, static assets, API data, frames, and scene slices. Mutations
also require a same-origin request and the dashboard CSRF token; CORS is not
enabled and responses use `Cache-Control: no-store`. Basic credentials are only
encoded, not encrypted, over plain HTTP. Bind this service only to a trusted LAN
or terminate HTTPS in a reverse proxy.

The dashboard reports structured Compose states, capture heartbeat/epoch/writer
health, storage usage, persisted background jobs, recordings, and exported
datasets. Start, stop, slice, and export mutations are serialized with the same
interprocess lock used by the CLI. A lifecycle or generation request returns
immediately as a job; progress and any actionable failure remain visible after
page reloads.

### Per-connection generation

Each player join has its own row and `connection_id`; reconnects are grouped
under the player without being merged. A row can move through `recording`,
`disconnected`, `waiting_for_slice`, `slicing`, `generating`, `complete`,
`interrupted`, or `failed`. Dataset generation is enabled only after the
connection has a terminal end sequence. A stale heartbeat without a clean
terminal marker is interrupted and cannot be promoted.

If the connection end is not already covered by a verified immutable slice,
generation reports `waiting_for_slice` while the recorder continues appending.
The tooling never sends recorder command requests for promotion; it consumes
only already-published slice manifests and completed replay archives, then pins
and copies those filesystem units under the global operation lock.

The export intersects the ledger's player UUID, connection ID, and observed
tick range and writes:

```text
artifacts/exports/<session>-<player>-<connection>.dataset/
```

Repeated clicks reuse a matching verified export. The dashboard never
automatically overwrites an invalid or conflicting output. Generation waits for
the saved replay, extracts and verifies every selected scene frame headlessly,
then atomically publishes Dataset V2. A failed or incomplete scene extraction
does not attach a partial dataset. RGB remains explicitly unavailable until a
separate GUI render succeeds.

### Queued RGB rendering and viewer refresh

Rendering stays outside the dashboard process because it may run headlessly.
Select a verified dataset connection and click **Render RGB**.
`minerec render-dispatcher` publishes pending jobs from the durable render
queue outbox to RabbitMQ.

Run one GUI render worker process on a machine that has the same workspace,
RabbitMQ access, OpenJDK 21, Gradle, and a graphical desktop:

```sh
proto install --config-mode local
pixi install --locked
MC_RECORDER_RABBITMQ_URL=amqp://guest:guest@localhost:5672/%2F \
  pixi run minerec render-worker
```

The worker consumes one RabbitMQ message, claims that exact queued job through
the local recorder runtime, renders it with the GUI client, finalizes the
result, acknowledges the message, and exits. Supervisors can start as many
one-shot worker processes as needed. Dashboard
Basic-auth credentials are not sent to the worker.

The normal build resolves Flashback through immutable Modrinth version ID
`9YgAwnpm`, which is the 0.39.5 artifact for Minecraft 1.21.8. No environment
override is required. `MC_RECORDER_FLASHBACK_JAR` remains available only for
offline builds and must point to that same Minecraft 1.21.8 artifact.

Each claimed job has an ephemeral workspace and launches exactly one Java
client; that client exits and the workspace is removed before the process exits.
Useful options are:

```sh
MC_RECORDER_RABBITMQ_URL=amqp://guest:guest@localhost:5672/%2F \
  pixi run minerec render-worker \
  --cache /path/to/cache \
  --keep-workspace               # retain this attempt for diagnosis
```

A claimed-job failure fences/fails that attempt and stops the worker before it
can touch later queued jobs. After correcting the local Java, Gradle, disk, or
renderer problem, start another worker process. A failed, timed-out, or invalid
claim leaves the RabbitMQ message unacknowledged so it can be retried by a later
process.

The default cache is `$XDG_CACHE_HOME/minerec` when that variable is set,
or `~/.cache/minerec` otherwise. Replay archives live under
`replays/<sha256>.zip`, survive between invocations, and are reused only after
their byte size and SHA-256 are verified. Owned per-attempt directories under
`jobs/` are deleted after both success and failure unless `--keep-workspace` is
set. A heartbeat renews a fenced lease while the GUI is active; a killed worker
cannot finalize after its lease is reclaimed. Its job returns to `queued`, and
a later worker process creates a new attempt.

An exact replay archive may still be saving after the player disconnects. In
that case the server immediately defers the attempt back to `queued` with a
30-second eligibility cooldown. The dispatcher waits for another mod-emitted
ready file before publishing more work for that connection.

The server pins every saved replay archive for the exact player connection and
authors path-free requests. The worker downloads the pinned archives, verifies
them, renders segments newest-to-oldest, and uploads hash-indexed portable
bundles. Newer coverage owns overlaps, so each older request stops before the
first tick supplied by a newer segment. An archive with no matching timeline
returns `no_coverage`; holes between all valid segment ranges remain missing.
The server never follows a worker-provided path: it verifies and canonicalizes
each bundle beneath `paths.exports/render-jobs/`, then re-exports only the
already verified deterministic dataset for that exact session, player,
connection, and tick range.

Complete RGB coverage ends in `complete`; a valid import with missing sample
frames ends in `partial`. Missing RGB stays explicit and non-fatal in
`modalities.jsonl`. A conflicting or tampered dataset is not overwritten. Once
the dataset manifest and declared hashes change, the viewer invalidates and
rebuilds its background SQLite byte-offset index under `.mc-recorder`.

The viewer never sends the full `samples.jsonl` to the browser. It offers
paginated sample summaries, player/connection and validity/modality filters,
20 Hz timeline stepping/playback, a bounded top-down X/Z trajectory, held-key
and observed-click indicators, an accepted yaw/pitch delta vector, and
on-demand sample detail. The trajectory is derived from the verified SQLite
index, breaks across invalid/missing transitions, and is decimated on the server
for long captures. Synchronized held-control buttons, camera movement, and
selected slot are explicitly labeled as server-reconstructed rather than raw
device input. Detail includes state-to-next-state differences, reconstructed
controls, ordered packet actions, peer state, transition validity, and
provenance. Attached RGB is fetched on demand. The contained scene store is
queried directly by `frame_id` for axis-selectable block slices, with entities
and block entities projected onto the selected plane. Unknown positions remain
unknown; slice navigation never requires replaying preceding ticks.

Dataset, sample, frame, and scene routes use opaque IDs. The server rejects
browser-supplied paths, symlinks, artifacts outside `paths.exports`, missing
files, and declared size/hash mismatches.

The versioned HTTP interface includes:

- `GET /api/v1/status` and `/api/v1/artifacts`;
- `GET /api/v1/render-jobs`, `/api/v1/render-jobs/{id}`, and
  `/api/v1/render-workers`;
- `POST /api/v1/datasets/{id}/render` and
  `/api/v1/render-jobs/{id}/cancel` or `/api/v1/render-jobs/{id}/retry`; and
- `GET /api/v1/datasets` plus dataset metadata, bounded `trajectory`, paginated
  `samples`, opaque sample detail, `frame`, and `scene-slice` routes below
  `/api/v1/datasets/{id}`.

## Inspect and export

```sh
pixi run minerec episodes list [--json]
pixi run minerec episodes validate [SESSION_ID] [--json]
pixi run minerec export SESSION_ID \
  [--player UUID] [--connection UUID] \
  [--from-tick N] [--to-tick N] \
  [--frames RENDER_OUTPUT] [--scene SCENE_STORE] \
  [--output PATH] [--force]
```

Validation recalculates every published stream's record count, byte count, and
SHA-256 and checks session identity, `epoch_index`, global `sequence`, and
non-decreasing server ticks.

`--player` and `--connection` are independently repeatable and are intersected
with the inclusive tick bounds. This makes a reconnect an exact export unit
without discarding other players from the selected sample's peer context. The
normalized connection selection is preserved in `manifest.json` as
`selection.connections`.

The exporter validates the source first, reads only immutable slices, and emits:

```text
artifacts/exports/<session-id>.dataset/
  manifest.json
  samples.jsonl
  states.jsonl
  actions.jsonl
  modalities.jsonl
  scene/
    scene-v1.sqlite3       # only when attached
```

`samples.jsonl` is canonical: authoritative post-state at tick `t`, ordered
applied semantic actions through the next state barrier, and post-state at tick
`t+1`. Identity includes player UUID and per-join connection ID. `states.jsonl`
and `actions.jsonl` are supporting streams; `modalities.jsonl` makes missing
scene/RGB references explicit.

The action stream represents decoded, server-observed intent at 20 Hz—not raw
keyboard/mouse telemetry or canonical packet bytes. `--frames` is repeatable;
it attaches completed renderer artifacts only by exact session, player,
connection, and global-tick identity. `--scene` accepts at most one complete,
identity-bound scene store. Unmatched modalities remain explicitly invalid.
Path traversal, missing files, symlinks, and size/hash mismatches are rejected;
PNG structure, CRC, and dimensions are checked, and the scene store is copied
into the published dataset with a manifest integrity envelope.

## Render RGB

This lower-level command remains useful for a manual local render. Dashboard RGB
jobs use `render-worker` instead and attach their
verified results automatically.

```sh
pixi run minerec render SESSION_ID \
  --player UUID \
  [--connection ID] \
  [--replay PATH] \
  [--from-tick N] [--to-tick N] \
  [--width 640] [--height 360] [--fps 20] \
  [--no-gui] [--output PATH] [--prepare-only] [--force]
```

The renderer accepts completed Flashback replay ZIPs and exactly 20 FPS. The command
validates/hashes the source episode and replay, selects one recorded connection,
and writes `render-job.json`. Unless `--prepare-only` is used, the command
launches `mods/renderer-mod` through Gradle.
New jobs include the recorded first-person hand/item and full recorded in-game
HUD by default. `--no-gui` is the explicit HUD-free opt-out; it does not
remove the requirement for a graphical Java client.

The job stores a replay byte-size/SHA-256 integrity envelope produced while the
archive is stable. The renderer checks that envelope before opening the replay
and after producing all requested RGB artifacts. The launching CLI then
rehashes the replay and requires `result.json` to report the same size and hash
before it accepts the job as complete.

The local client finds a matching `mc_recorder:timeline/v1` marker inside the
archive, aligns replay ticks to global server ticks, tracks the selected
player's head in first person, renders the player's hand/item and HUD, and
writes:

```text
<render-job>/
  render-job.json
  result.json
  frames/
    frame_000001.png
    ...
    frames.jsonl
```

`frames.jsonl` identifies every PNG by session, connection, player, global
server tick, and replay tick. These are reconstructed server-visible views, not
original client pixels.

Attach the completed artifacts during export:

```sh
pixi run minerec export SESSION_ID \
  --frames artifacts/exports/render-jobs/JOB
```

## Extract random-access scenes

The scene extractor is a Fabric dedicated server process, not a graphical
client. It consumes the immutable replay packets with Minecraft's registry-aware
codecs, emits one logical frame at every matching timeline marker, and compacts
the stream into a structurally shared SQLite store:

```sh
pixi run minerec scene extract SESSION_ID \
  --player UUID \
  --connection UUID \
  [--from-tick N] [--to-tick N] \
  --output PATH \
  [--force] \
  [--prepare-only]
```

The only supported scope is `client_visible`. No captured/source world is
mounted; any dedicated-server bootstrap world lives in the job-owned
`runtime/scene-jobs/<job-id>/server-run` directory and is never scene input.
The job binds an ephemeral server port with query and RCON disabled, so it does
not share `mods/scene-extractor-mod/run` or conflict with the capture server.

Every selected `player_state` tick must resolve. Segment overlaps must describe
identical logical frames, while gaps, source mutation, unknown state-affecting
packets, and identity mismatch fail the job. Before compaction the CLI verifies
the terminal identity, policy, capture contract, sources, frame/change counts,
index sizes and SHA-256 hashes, and every referenced canonical blob's name,
digest, count, and bytes. The selected player's pose is copied from sliced,
hash-verified `player_state` records into an owned integrity-enveloped stream and
overlaid before each frame and overlap hash; ServerReplay's sampled local-player
position is not treated as authoritative. A frozen verification envelope is
checked again before reads and before publication, then persisted with the
store's extraction provenance.

Scene extraction accepts only schema-v3 Flashback archives whose embedded
metadata and replay-segment ledger both declare
`flashback_capture_contract: "client_visible_scene_v1"`. Older archives remain
usable by RGB tooling but are rejected as scene sources.

The output includes block states, entities, block entities, full captured
metadata, exact source replay provenance, and a `sensitive: true` marker.
Output parents are created safely; symlinked parents/outputs are rejected.
Existing output fails by default, while `--force` replaces only a scene store
that already validates. Python consumers can use `SceneStore.frame`,
`materialize_crop`, and `slice` for independent random access without replaying
earlier ticks.

Attach it manually with `pixi run minerec export SESSION_ID --scene PATH`; the
dashboard generation path performs extraction, compaction, validation, and
attachment automatically.

## Retention

```sh
pixi run minerec storage status
pixi run minerec storage enforce
```

The quota counts `paths.captures` plus `paths.replays`, but not world data or
exports. Usage at `warn_percent` produces a visible warning. At the quota,
optional eviction removes oldest immutable source units until usage reaches the
warning threshold. Eligible units are verified whole sidecar slices and stable,
readable completed `.zip`/`.mcpr` replay archives. Replay archives must be
unchanged and at least five minutes old. Active/incomplete slices,
recent/partial archives, directories, symlinks, and unexpected paths are never
candidates. Dataset publication holds shared locks on its sidecar slices and
replays, so retention skips them. Successful scene jobs are removed; only the
newest failed or `--prepare-only` marker-owned job is retained, and older crash
jobs are pruned without touching symlinked or non-owned directories.

## Tests

```sh
pixi run test-python
pixi run build-recorder-mod
pixi run build-scene-extractor-mod
pixi run build-renderer-mod
pixi run check
```
