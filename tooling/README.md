# `mc-recorder` CLI

The Python 3.11+ CLI provisions the pinned Minecraft 1.21.8 Fabric server,
inspects verified sidecar epochs, exports state/action JSONL, enforces combined
capture/replay retention, and launches the local Flashback RGB/voxel renderer.
Docker Compose is required on the recorder host. Java 21, a graphical desktop,
OpenSSH, and `rsync` are required on a remote renderer; the recorder host also
needs an SSH server and `rsync` for that workflow.

V1 defaults to the exact `itzg/minecraft-server:2026.7.0-java21` image and the
immutable ServerReplay Modrinth selector `server-replay:TbWIikrT`. The companion
storage monitor is pinned to `python:3.11.15-alpine3.24`.

## Install and initialize

From the workspace root:

```sh
python3 -m pip install -e tooling
mc-recorder init
```

Initialization creates `recorder.toml` and workspace directories. It
deliberately writes `server.eula = false`. Review the
[Minecraft EULA](https://aka.ms/MinecraftEULA), then explicitly change the
field to `true`. Alternatively, after accepting it, use:

```sh
mc-recorder init --accept-eula --force
```

The CLI never accepts the EULA implicitly, and `server start` refuses to run
while the field is false.

All relative paths resolve from the directory containing `recorder.toml`. Use
`mc-recorder --config PATH ...` to operate another workspace.

## Server lifecycle

```sh
mc-recorder server start --wait
mc-recorder server status
mc-recorder server logs --follow
mc-recorder server stop
```

`server start` builds and stages `recorder-mod`, writes the capture and
ServerReplay configurations, checks the combined capture/replay quota, and
starts Docker Compose. With `--wait`, it waits for the image's Minecraft health
check. Every connected player is recorded automatically. ServerReplay writes a
Flashback archive per player, including that player's client-visible chunks,
under `paths.replays`; the sidecar writes combined event epochs under
`paths.captures`.

`server stop` requests a graceful Minecraft shutdown. Sidecar epochs are
exportable only after `events.jsonl.inprogress` has been atomically published
as `events.jsonl` with a matching sealed manifest. Completed ServerReplay
archives are independent of sidecar epochs.

## LAN dashboard

```sh
export MC_RECORDER_DASHBOARD_USERNAME=recorder
export MC_RECORDER_DASHBOARD_PASSWORD='replace-with-a-long-password'
mc-recorder dashboard serve
```

The generated configuration contains the intended LAN defaults, which can be
changed without altering the credential environment variables:

```toml
[dashboard]
bind = "0.0.0.0"
port = 8765
```

`dashboard serve` runs directly on the recorder host and defaults to
`0.0.0.0:8765` from the `[dashboard]` configuration. It is deliberately not a
Compose service: the host process can invoke the existing Docker lifecycle
without mounting the Docker socket into a container, and it stays reachable
while Minecraft is stopped. Run it with the same workspace/configuration and a
host account allowed to use Docker Compose. Use launchd, systemd, or another
host service manager to keep it running independently of an interactive shell.

Both credential environment variables are mandatory. HTTP Basic authentication
applies to HTML, static assets, API data, frames, and voxel slices. Mutations
also require a same-origin request and the dashboard CSRF token; CORS is not
enabled and responses use `Cache-Control: no-store`. Basic credentials are only
encoded, not encrypted, over plain HTTP. Bind this service only to a trusted LAN
or terminate HTTPS in a reverse proxy.

The dashboard reports structured Compose states, capture heartbeat/epoch/writer
health, storage usage, persisted background jobs, recordings, and exported
datasets. Start, stop, seal, and export mutations are serialized with the same
interprocess lock used by the CLI. A lifecycle or generation request returns
immediately as a job; progress and any actionable failure remain visible after
page reloads.

### Per-connection generation

Each player join has its own row and `connection_id`; reconnects are grouped
under the player without being merged. A row can move through `recording`,
`disconnected`, `waiting_for_seal`, `sealing`, `generating`, `complete`,
`interrupted`, or `failed`. **Seal & Generate Dataset** is enabled only after the
connection has a terminal end sequence. A stale heartbeat without a clean
terminal marker is interrupted and cannot be promoted.

If the connection end is not already covered by a verified epoch, generation
requests a global manual rotation at an end-of-tick boundary. The recorder
fsyncs and seals the current shared epoch, publishes its integrity manifest,
acknowledges the request, and continues the same session in a monotonically
numbered epoch. Other players remain connected and keep recording. An already
covering seal is reused, and a manual request coalesces with an automatic
rotation that is already due.

The export intersects the ledger's player UUID, connection ID, and observed
tick range and writes:

```text
artifacts/exports/<session>-<player>-<connection>.dataset/
```

Repeated clicks reuse a matching verified export. The dashboard never
automatically overwrites an invalid or conflicting output. Initial generation
is intentionally structured-only; explicit unavailable RGB/voxel entries do
not make the dataset invalid.

### Queued RGB rendering and viewer refresh

Rendering stays outside the dashboard process because the recorder server may
be headless. After **Seal & Generate Dataset** completes, choose a resolution in
the recording row and click **Render RGB**. The resulting job remains queued on
the server until a GUI-capable machine runs the foreground worker.

Prepare that machine from the same project revision deployed on the server:

```sh
python3 -m pip install -e tooling
mc-recorder init
ssh -o BatchMode=yes mcdatacol true
mc-recorder render-worker \
  --host mcdatacol \
  --remote-root /srv/mc-play-recorder
```

The local `recorder.toml` anchors `renderer-mod`, the Gradle wrapper, and the
worker cache; it is not the remote server configuration. Java 21 and a working
graphical desktop are required to launch the Minecraft client. `ssh` and
`rsync` must be installed locally, and the Debian recorder host must run an SSH
server and have `rsync`. Because the worker uses SSH batch mode, `mcdatacol`
must resolve through the local SSH configuration and authenticate without an
interactive password prompt. That SSH identity is the authority boundary: the
remote account must be able to read the deployed Python tooling and replay
sources, write the configured runtime/export roots, and run the tooling under
`/srv/mc-play-recorder`. Dashboard Basic-auth credentials are not sent to the
worker.

The normal build resolves Flashback through immutable Modrinth version ID
`X3J8u7wy`, which is the 0.39.1 artifact for Minecraft 1.21.8. No environment
override is required. `MC_RECORDER_FLASHBACK_JAR` remains available only for
offline builds and must point to that same Minecraft 1.21.8 artifact.

By default, one foreground process registers one worker UUID, polls every 10
seconds, and processes queued jobs sequentially until Ctrl-C. Each claimed job
still has an ephemeral workspace and launches exactly one Java client; that
client exits and the workspace is removed before the worker claims another
job. Keep the process running inside a logged-in graphical desktop session.
There is no launchd service integration yet. Useful options are:

```sh
mc-recorder render-worker --host mcdatacol \
  --remote-root /srv/mc-play-recorder \
  --job JOB_UUID                 # claim only this queued job

mc-recorder render-worker --host mcdatacol \
  --remote-root /srv/mc-play-recorder \
  --once                         # make one claim attempt and exit

mc-recorder render-worker --host mcdatacol \
  --remote-root /srv/mc-play-recorder \
  --poll-interval 5              # continuous polling; allowed range is 1-30

mc-recorder render-worker --host mcdatacol \
  --remote-root /srv/mc-play-recorder \
  --cache /path/to/cache \
  --keep-workspace               # retain this attempt for diagnosis
```

A claimed-job failure fences/fails that attempt and stops the worker before it
can touch later queued jobs. After correcting the local Java, Gradle, disk, or
renderer problem, restart the command. Failures reaching the server before a
claim during worker registration use bounded retry backoff and leave the queue
unchanged. A failed, timed-out, or invalid `claim` response stops the worker:
the server may already have leased work, so automatically claiming again would
be unsafe. Inspect the dashboard queue before restarting. Persistent mode also
requires upgraded server tooling; use `--once` only when deliberately working
with a pre-extension server.

The default cache is `$XDG_CACHE_HOME/mc-recorder` when that variable is set,
or `~/.cache/mc-recorder` otherwise. Replay archives live under
`replays/<sha256>.zip`, survive between invocations, and are reused only after
their byte size and SHA-256 are verified. Owned per-attempt directories under
`jobs/` are deleted after both success and failure unless `--keep-workspace` is
set. A heartbeat renews a fenced lease while the GUI is active; a killed worker
cannot finalize after its lease is reclaimed. Its job returns to `queued`, and
a later worker claim creates a new attempt.

An exact replay archive may still be saving after the player disconnects. In
that case the server immediately defers the attempt back to `queued` with a
30-second eligibility cooldown. The worker continues polling and can process a
later ready job instead of repeatedly reclaiming the deferred oldest job.
`--once` and `--job` retain the successful no-ready-job exit.

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
20 Hz timeline stepping/playback, a bounded top-down X/Z trajectory, and
on-demand sample detail. The trajectory is derived from the verified SQLite
index, breaks across invalid/missing transitions, and is decimated on the server
for long captures. Synchronized held-control buttons, camera movement, and
selected slot are explicitly labeled as server-reconstructed rather than raw
device input. Detail includes state-to-next-state differences, reconstructed
controls, ordered packet actions, peer state, transition validity, and
provenance. Attached RGB is fetched on demand. Valid voxel data is rendered as
axis-selectable 2D slices with uncovered cells shown as unknown; V1 does not
provide a full interactive 3D view.

Dataset, sample, frame, and voxel routes use opaque IDs. The server rejects
browser-supplied paths, symlinks, artifacts outside `paths.exports`, missing
files, and declared size/hash mismatches.

The versioned HTTP interface includes:

- `GET /api/v1/status`, `/api/v1/recordings`, and `/api/v1/jobs/{id}`;
- `GET /api/v1/render-jobs`, `/api/v1/render-jobs/{id}`, and
  `/api/v1/render-workers`;
- `POST /api/v1/server/start`, `/api/v1/server/stop`, and
  `/api/v1/recordings/{id}/generate`;
- `POST /api/v1/recordings/{id}/render` and
  `/api/v1/render-jobs/{id}/cancel` or `/api/v1/render-jobs/{id}/retry`; and
- `GET /api/v1/datasets` plus dataset metadata, bounded `trajectory`, paginated
  `samples`, opaque sample detail, `frame`, and `voxel-slice` routes below
  `/api/v1/datasets/{id}`.

## Inspect and export

```sh
mc-recorder episodes list [--json]
mc-recorder episodes validate [SESSION_ID] [--json]
mc-recorder export SESSION_ID \
  [--player UUID] [--connection UUID] \
  [--from-tick N] [--to-tick N] \
  [--frames RENDER_OUTPUT] [--voxels RENDER_OUTPUT] \
  [--output PATH] [--force]
```

Validation recalculates every sealed stream's record count, byte count, and
SHA-256 and checks session identity, `epoch_index`, global `sequence`, and
non-decreasing server ticks.

`--player` and `--connection` are independently repeatable and are intersected
with the inclusive tick bounds. This makes a reconnect an exact export unit
without discarding other players from the selected sample's peer context. The
normalized connection selection is preserved in `manifest.json` as
`selection.connections`.

The exporter validates the source first, reads only sealed epochs, and emits:

```text
artifacts/exports/<session-id>.dataset/
  manifest.json
  samples.jsonl
  states.jsonl
  actions.jsonl
  modalities.jsonl
```

`samples.jsonl` is canonical: authoritative post-state at tick `t`, ordered
applied semantic actions through the next state barrier, and post-state at tick
`t+1`. Identity includes player UUID and per-join connection ID. `states.jsonl`
and `actions.jsonl` are supporting streams; `modalities.jsonl` makes missing
voxel/RGB references explicit.

The action stream represents decoded, server-observed intent at 20 Hz—not raw
keyboard/mouse telemetry or canonical packet bytes. `--frames` and `--voxels`
are repeatable. They attach completed renderer artifacts only by exact session,
player, connection, and global-tick identity; unmatched samples remain
explicitly invalid. Completed indexes must be contiguous. Path traversal,
absolute references, missing files, and symlinks are rejected, and the export
records hashes and byte sizes for attached artifacts. PNG structure, CRC, and
dimensions are checked; voxel gzip payloads are size-bounded and validated down
to palette indexes and coverage bits.

## Render RGB and voxels

This lower-level command remains useful for a manual local render or optional
voxel capture. Dashboard RGB jobs use `render-worker` instead and attach their
verified results automatically.

```sh
mc-recorder render SESSION_ID \
  --player UUID \
  [--connection ID] \
  [--replay PATH] \
  [--from-tick N] [--to-tick N] \
  [--width 640] [--height 360] [--fps 20] \
  [--voxel-horizontal-radius N --voxel-vertical-radius N] \
  [--no-gui] [--output PATH] [--prepare-only] [--force]
```

V1 accepts completed Flashback replay ZIPs and exactly 20 FPS. The command
validates/hashes the source episode and replay, selects one recorded connection,
and writes `render-job.json`. Optional positive voxel radii request a crop around
the player on every selected tick; use zero/omit both for RGB only. Unless
`--prepare-only` is used, the command launches `renderer-mod` through Gradle.
New jobs include the recorded first-person hand/item and full recorded in-game
HUD by default. `--no-gui` is the explicit HUD-free opt-out; it does not
remove the requirement for a graphical Java client.

The job stores a replay byte-size/SHA-256 integrity envelope produced while the
archive is stable. The renderer checks that envelope before opening the replay
and after producing all requested RGB/voxel artifacts. The launching CLI then
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
    voxels.jsonl                       # when voxel conversion is enabled
    voxels/
      voxel_<server-tick>.json.gz
```

`frames.jsonl` identifies every PNG by session, connection, player, global
server tick, and replay tick. These are reconstructed server-visible views, not
original client pixels. `voxels.jsonl` indexes palette-based gzip crops with an
explicit per-cell coverage bitset; unloaded cells are unknown, not known air.
Block entities are not materialized in voxel V1.

Attach the completed artifacts during export:

```sh
mc-recorder export SESSION_ID \
  --frames artifacts/exports/render-jobs/JOB \
  --voxels artifacts/exports/render-jobs/JOB
```

## Retention

```sh
mc-recorder storage status
mc-recorder storage enforce
```

The quota counts `paths.captures` plus `paths.replays`, but not world data or
exports. Usage at `warn_percent` produces a visible warning. At the quota,
optional eviction removes oldest immutable source units until usage reaches the
warning threshold. Eligible units are verified whole sidecar epochs and stable,
readable completed `.zip`/`.mcpr` replay archives. Replay archives must be
unchanged and at least five minutes old. Active/incomplete epochs,
recent/partial archives, directories, symlinks, and unexpected paths are never
candidates.

## Tests

```sh
PYTHONPATH=tooling/src python3 -m unittest discover -s tooling/tests -v
```
