# `mc-recorder` CLI

The Python 3.11+ CLI provisions the pinned Minecraft 1.21.8 Fabric server,
inspects verified sidecar epochs, exports state/action JSONL, enforces combined
capture/replay retention, and launches the local Flashback RGB/voxel renderer.
Docker Compose and Java 21 are required for the complete workflow.

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

### Local rendering and viewer refresh

Rendering stays outside the dashboard in V1 because the recorder server may be
headless. The recording row shows the exact local command. On a GUI-capable
machine with access to the capture and completed replay, run its equivalent:

```sh
mc-recorder render SESSION_ID \
  --player PLAYER_UUID \
  --connection CONNECTION_ID \
  --replay /path/to/completed-replay.zip \
  --voxel-horizontal-radius 16 \
  --voxel-vertical-radius 8
```

Copy or mount the completed render-job directory beneath the server's configured
`paths.exports`, then explicitly re-export the same connection into its
deterministic dashboard path with the attachments:

```sh
mc-recorder export SESSION_ID \
  --player PLAYER_UUID \
  --connection CONNECTION_ID \
  --frames artifacts/exports/render-jobs/JOB \
  --voxels artifacts/exports/render-jobs/JOB \
  --output artifacts/exports/SESSION_ID-PLAYER_UUID-CONNECTION_ID.dataset \
  --force
```

Omit `--voxels` when the job contains RGB only. `--force` is an explicit CLI
replacement of the earlier structured-only export; the dashboard itself never
performs that overwrite. Once the manifest and declared hashes change on the
server filesystem, the viewer invalidates and rebuilds its background SQLite
byte-offset index under `.mc-recorder`.

The viewer never sends the full `samples.jsonl` to the browser. It offers
paginated sample summaries, player/connection and validity/modality filters,
20 Hz timeline stepping/playback, and on-demand sample detail. Detail includes
state-to-next-state differences, reconstructed controls, ordered packet actions,
peer state, transition validity, and provenance. Attached RGB is fetched on
demand. Valid voxel data is rendered as axis-selectable 2D slices with uncovered
cells shown as unknown; V1 does not provide a full interactive 3D view.

Dataset, sample, frame, and voxel routes use opaque IDs. The server rejects
browser-supplied paths, symlinks, artifacts outside `paths.exports`, missing
files, and declared size/hash mismatches.

The versioned HTTP interface includes:

- `GET /api/v1/status`, `/api/v1/recordings`, and `/api/v1/jobs/{id}`;
- `POST /api/v1/server/start`, `/api/v1/server/stop`, and
  `/api/v1/recordings/{id}/generate`; and
- `GET /api/v1/datasets` plus dataset metadata, paginated `samples`, opaque
  sample detail, `frame`, and `voxel-slice` routes below
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

```sh
mc-recorder render SESSION_ID \
  --player UUID \
  [--connection ID] \
  [--replay PATH] \
  [--from-tick N] [--to-tick N] \
  [--width 640] [--height 360] [--fps 20] \
  [--voxel-horizontal-radius N --voxel-vertical-radius N] \
  [--output PATH] [--prepare-only] [--force]
```

V1 accepts completed Flashback replay ZIPs and exactly 20 FPS. The command
validates/hashes the source episode and replay, selects one recorded connection,
and writes `render-job.json`. Optional positive voxel radii request a crop around
the player on every selected tick; use zero/omit both for RGB only. Unless
`--prepare-only` is used, the command launches `renderer-mod` through Gradle.

The job stores a replay byte-size/SHA-256 integrity envelope produced while the
archive is stable. The renderer checks that envelope before opening the replay
and after producing all requested RGB/voxel artifacts. The launching CLI then
rehashes the replay and requires `result.json` to report the same size and hash
before it accepts the job as complete.

The local client finds a matching `mc_recorder:timeline/v1` marker inside the
archive, aligns replay ticks to global server ticks, tracks the selected
player's head in first person, and writes:

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
