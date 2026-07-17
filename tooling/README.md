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

## Inspect and export

```sh
mc-recorder episodes list [--json]
mc-recorder episodes validate [SESSION_ID] [--json]
mc-recorder export SESSION_ID \
  [--player UUID] [--from-tick N] [--to-tick N] \
  [--frames RENDER_OUTPUT] [--voxels RENDER_OUTPUT] \
  [--output PATH] [--force]
```

Validation recalculates every sealed stream's record count, byte count, and
SHA-256 and checks session identity, `epoch_index`, global `sequence`, and
non-decreasing server ticks.

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
