# Minecraft gameplay dataset recorder

This workspace provisions a Minecraft 1.21.8 Fabric server that records every
connected player's server-observed gameplay, seals durable five-minute capture
epochs, exports canonical state/action transitions, and reconstructs first-
person RGB frames plus local voxel crops in a Flashback client.

It combines three pieces:

1. **ServerReplay** records one replay archive per player, including the chunks
   and entities visible along that player's trajectory, without a graphics
   stack on the server.
2. **`recorder-mod`** writes a synchronized server-side JSONL sidecar containing
   all-player state, decoded semantic actions, connection identity, apply
   barriers, and replay timeline markers.
3. **`mc-recorder` tooling** provisions Docker, validates immutable epochs,
   enforces capture/replay retention, exports trainable samples, and launches
   deterministic RGB/voxel render jobs through `renderer-mod`.

## Current V1 capabilities

- Minecraft 1.21.8 on `itzg/minecraft-server:2026.7.0-java21`, with
  ServerReplay pinned by immutable Modrinth selector `server-replay:TbWIikrT`.
- Automatic capture of every connected player in multiplayer sessions.
- One combined event stream ordered by global `sequence`, with a distinct
  `connection_id` for each player join.
- Authoritative post-tick player state and ordered, applied serverbound semantic
  actions at Minecraft's 20 Hz tick rate.
- Nominal five-minute sidecar epochs with count/size/SHA-256 sealing.
- Independent ServerReplay archives aligned exactly through embedded
  `mc_recorder:timeline/v1` markers.
- Standard Flashback archives that can also be inspected interactively in a
  Minecraft 1.21.8 client with Flashback 0.39.1.

  If Modrinth's Maven endpoint is unavailable, set
  `MC_RECORDER_FLASHBACK_JAR` to an installed Flashback 0.39.1 JAR before
  running `mc-recorder render`.
- Canonical `state + action -> next_state` JSONL samples with provenance and
  exact-key modality attachment.
- First-person 20 FPS RGB PNG rendering and optional coverage-aware voxel crops
  in a local Flashback client.
- Capture/replay-volume warnings and oldest-first eviction of only verified,
  sealed sidecar epochs or stable, completed replay archives.

Voxel conversion is optional because it requires replaying the server capture
in a graphics client. V1 writes palette-indexed block-state crops plus an
explicit coverage bitset for every requested replay tick. Unloaded cells remain
unknown rather than becoming valid air. Block entities are not materialized in
V1; the immutable replay remains their source when they were client-visible.

## Requirements

- Python 3.11 or newer;
- Docker with Docker Compose;
- Java 21 for building the Fabric mods and running the local renderer; and
- a normal Minecraft client account for joining the capture server.

## Quickstart

From the workspace root:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e tooling
mc-recorder init
```

`mc-recorder init` always defaults to `server.eula = false`. Read the
[Minecraft EULA](https://aka.ms/MinecraftEULA), then explicitly opt in by
editing `recorder.toml`:

```toml
[server]
eula = true
```

The CLI never accepts the EULA automatically. If you have already reviewed and
accepted it, `mc-recorder init --accept-eula --force` is an explicit equivalent.

Start the server and connect to `localhost:25565`:

```sh
mc-recorder server start --wait
mc-recorder server logs --follow
```

Recording starts automatically when players join. The capture side requires no
client mod; a normal Minecraft 1.21.8 client can connect. Stop cleanly before
consuming the latest files, then inspect and validate the capture:

```sh
mc-recorder server stop
mc-recorder episodes list
mc-recorder episodes validate SESSION_ID
```

Export all recorded subjects, or add repeatable player and connection UUID filters:

```sh
mc-recorder export SESSION_ID \
  --player PLAYER_UUID \
  --connection CONNECTION_ID
```

The export contains `samples.jsonl`, `states.jsonl`, `actions.jsonl`,
`modalities.jsonl`, and a provenance manifest under
`artifacts/exports/SESSION_ID.dataset/`.

Both subject filters are repeatable. Player, connection, and tick-range
filters are intersected, so a reconnect can be exported without mixing its
states or actions with another connection. Selected samples still include
other recorded players as peer context.

Render one recorded connection from a completed Flashback archive:

```sh
mc-recorder render SESSION_ID \
  --player PLAYER_UUID \
  --connection CONNECTION_ID \
  --replay artifacts/replays/players/PLAYER_UUID/REPLAY.zip \
  --voxel-horizontal-radius 16 \
  --voxel-vertical-radius 8
```

`--connection` may be omitted when the selected player has exactly one recorded
connection. `--replay` may be omitted only when exactly one completed archive is
available for that player. Omit both voxel-radius options for RGB only. The
command launches the local client renderer by default; `--prepare-only` writes
the validated render job without launching it.

Render preparation records a stable replay byte size and SHA-256. The client
verifies both before opening the archive and again after RGB/voxel generation;
after the client exits, the CLI rehashes the archive and accepts the atomic
complete result only when all three checks match the prepared envelope.

Attach the completed render artifacts while exporting. Both options are
repeatable for multiple players, connections, or replay segments:

```sh
mc-recorder export SESSION_ID \
  --frames artifacts/exports/render-jobs/SESSION_ID-PLAYER_UUID \
  --voxels artifacts/exports/render-jobs/SESSION_ID-PLAYER_UUID \
  --force
```

Attachment is accepted only for exact
`(session_id, player_uuid, connection_id, server_tick)` matches with valid,
contiguous indexes. Referenced artifacts are containment-checked and hashed;
absolute/escaping paths, missing files, and symlinks are rejected. Samples
outside the supplied render ranges retain explicit invalid modality entries.

## Capture and replay relationship

The structured sidecar and ServerReplay rotate independently. Both default to
roughly five-minute segments, but their boundaries and filenames are not join
keys. Every recorded connection receives a timeline marker each server tick.
The renderer uses the marker's session ID, connection ID, and global server tick
to derive the replay-tick offset.

Open-world coverage is best effort and client-visible. The recorder does not
force chunk generation. A `player_state.replay_coverage` hint records the center
chunk and view distance with `complete: false`. The replay converter centers a
requested crop on the recorded player and emits an explicit bit per cell, so an
unloaded position is distinguishable from a covered `minecraft:air` block.

## Storage safety

The configured quota counts `paths.captures` and `paths.replays`; it never
deletes the world or exports. At the warning threshold the CLI and monitor
report their combined usage. After the quota is reached, optional eviction
works across the oldest immutable source units until usage falls to the warning
threshold. Eligible units are either:

- whole sidecar epochs whose final stream matches the sealed manifest's record
  count, byte count, and SHA-256; or
- completed `.zip`/`.mcpr` replay archives that are readable, unchanged across
  inspection, and at least five minutes old.

Active/incomplete epochs, recent/partial replay files, directories, symlinks,
and unexpected paths are ineligible. Sidecar and replay units are independently
evicted; do not assume coupled retention.

```sh
mc-recorder storage status
mc-recorder storage enforce
```

## Configuration and contracts

`mc-recorder init` generates `recorder.toml`; [`recorder.example.toml`](recorder.example.toml)
documents every V1 option with EULA acceptance disabled. Relative paths resolve
from the configuration file's directory.

For an API-independent local deployment, stage ServerReplay, Fabric API, and
Fabric Language Kotlin in `.mc-recorder/mods/`, then set
`mods.server_replay_project = ""`. The default remains the immutable Modrinth
version selector.

- [`schemas/source-record-v1.md`](schemas/source-record-v1.md) defines the
  combined capture stream, barriers, identity, replay alignment, and coverage.
- [`schemas/dataset-v1.md`](schemas/dataset-v1.md) defines canonical samples and
  current modality support.
- [`tooling/README.md`](tooling/README.md) documents all CLI commands.

## Workspace layout

```text
deploy/         Docker Compose deployment
recorder-mod/   server-side Fabric capture sidecar
renderer-mod/   local Flashback first-person RGB/voxel renderer
schemas/        source and dataset V1 contracts
tooling/        Python provisioning/export CLI
ServerReplay/   upstream server replay mod source
```

Chat, command text, and custom payload contents are redacted by the sidecar.
ServerReplay has its own privacy/storage implications; only record players who
have consented to dataset capture.
