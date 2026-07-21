# Minecraft gameplay dataset recorder

This workspace provisions a Minecraft 1.21.8 Fabric server that records every
connected player's server-observed gameplay, seals durable five-minute capture
epochs, exports canonical state/action transitions, reconstructs a random-access
client-visible world scene for every selected tick without a GUI, and can render
first-person RGB frames in a Flashback client.

It combines three pieces:

1. **ServerReplay** records one replay archive per player, including the chunks
   and entities visible along that player's trajectory, without a graphics
   stack on the server.
2. **`recorder-mod`** writes a synchronized server-side JSONL sidecar containing
   all-player state, decoded semantic actions, connection identity, apply
   barriers, and replay timeline markers.
3. **`mc-recorder` tooling** provisions Docker, validates immutable epochs,
   enforces capture/replay retention, exports trainable samples, and launches
   headless scene extraction plus deterministic RGB jobs through `renderer-mod`.

## Current capabilities

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
  Minecraft 1.21.8 client with Flashback 0.39.1. The renderer pins immutable
  Modrinth version ID `X3J8u7wy`, because the display version is reused across
  incompatible Minecraft variants.

  If Modrinth's Maven endpoint is unavailable, set
  `MC_RECORDER_FLASHBACK_JAR` to the Flashback 0.39.1 JAR specifically built
  for Minecraft 1.21.8 before running `mc-recorder render-worker`.
- Canonical `state + action -> next_state` JSONL samples with provenance and
  exact-key modality attachment.
- Dataset V2 exports with a contained SQLite scene store that supports direct
  lookup of block, entity, and block-entity state at every selected tick.
- Server-only scene extraction from client-visible replay packets; no Minecraft
  window, graphics stack, source world mount, or forward replay is needed by
  the dataset viewer.
- Optional first-person 20 FPS RGB PNG rendering in a local Flashback client.
- Capture/replay-volume warnings and oldest-first eviction of only verified,
  sealed sidecar epochs or stable, completed replay archives.

Scene extraction replays the recorded network stream through Minecraft's server
packet codecs, materializes the logical client-visible scene, and structurally
shares unchanged values in `scene-v1.sqlite3`. Unloaded cells remain unknown
rather than becoming valid air. The default `client_visible` scope includes
entities and block entities and is marked sensitive when full captured metadata
is retained. Each job uses its own `runtime/scene-jobs/<job-id>/server-run`
bootstrap world and an ephemeral server port; it never mounts a captured world,
shares `scene-extractor-mod/run`, or binds the capture server's port.

## Requirements

- Python 3.11 or newer;
- Docker with Docker Compose;
- Java 21 for building the Fabric mods and running the local renderer;
- OpenSSH and `rsync` on both the recorder host and any remote GUI renderer; and
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

### LAN dashboard

The dashboard is a host process, not a Compose container. It remains available
while Minecraft is stopped and starts or stops the Compose-managed server by
calling the same lifecycle code as `mc-recorder server start` and
`mc-recorder server stop`. Run it as a user that can run Docker Compose, and use
launchd, systemd, or another host service manager when it should survive logouts
or reboots.

Set both required HTTP Basic credentials, then start the service:

```sh
export MC_RECORDER_DASHBOARD_USERNAME=recorder
export MC_RECORDER_DASHBOARD_PASSWORD='replace-with-a-long-password'
mc-recorder dashboard serve
```

The default `[dashboard]` listener is `0.0.0.0:8765`, so another trusted-LAN
machine can open `http://SERVER_ADDRESS:8765/`. Basic authentication over plain
HTTP does **not** encrypt the username or password. Use this deployment only on
a LAN you trust, or place an HTTPS reverse proxy in front of it. Every route is
authenticated; mutating requests additionally require same-origin and CSRF
checks.

The recordings view groups rows by player but keeps every join/reconnect as a
separate `connection_id`. Active rows cannot be exported. After a player
disconnects, **Seal & Generate Dataset** requests a global sidecar epoch rotation
at an end-of-tick boundary, waits for its verified manifest, and exports only
that player UUID, connection ID, and observed tick range. Other connected
players continue recording in the next epoch. Repeated generation reuses a
matching verified deterministic export and does not overwrite a conflicting
dataset.

Generation waits for the connection's replay archive, extracts every selected
scene tick on the server, compacts and verifies one immutable scene store, then
publishes Dataset V2 atomically. A failed or incomplete extraction never leaves
a partial dataset attached. RGB remains optional and explicitly unavailable
until requested. Choose a resolution and click **Render RGB** to queue a leased
GUI job; the headless server never attempts to start a graphics client. The host
streams the verified dataset into a compact, hash-bound structured-HUD sidecar,
and the worker applies its exact inventory, selected slot, health, food, air,
and experience state before each frame. GUI renders that complete this path are
versioned with the `flashback_server_spectate_structured_hud_v1` presentation
contract. Older GUI renders, including the spectate-only v1 contract, are shown
as unsynchronized legacy RGB and can be replaced explicitly with **Re-render
RGB**; each verified original import remains preserved for audit.

Successful scene jobs are removed after the contained store is published. The
newest failed or intentional `--prepare-only` job is retained for diagnostics;
older marker-owned crash jobs are pruned under the global operation lock.
Non-owned or symlinked directories are never removed.

On a GUI-capable machine, use the same project revision as the recorder host,
install the Python tooling, initialize a local `recorder.toml`, and make Java 21
the active JVM. The machine must have `ssh` and `rsync`, while the recorder host
must have an SSH server and `rsync`. The SSH alias must authenticate
non-interactively to the account that owns the remote recorder workspace. From
a logged-in graphical desktop session, start the foreground worker with:

```sh
mc-recorder render-worker \
  --host mcdatacol \
  --remote-root /srv/mc-play-recorder
```

The worker registers one process identity, polls continuously, and processes
jobs sequentially until interrupted with Ctrl-C. Each job downloads its exact
replay segments and verified structured-HUD sidecar, launches one local Java GUI
renderer, uploads integrity-bound bundles, asks the server to
verify/import/re-export the dataset, and then closes that Java client before
claiming another job. The default poll interval is 10 seconds;
`--poll-interval SECONDS` accepts values from 1 through 30. Use
`--once` for the previous single-claim behavior, or `--job JOB_UUID` for a
targeted one-shot claim. Keep the foreground process inside a logged-in GUI
session; launchd integration is not provided yet.

If a claimed job fails, the worker reports/fences that attempt and stops before
claiming another job. This avoids consuming the queue when Java, Gradle, disk,
or another machine-wide renderer dependency is broken; fix the local problem
and restart the foreground worker. Transient registration failures use bounded
retry backoff without failing queue jobs. A lost or invalid `claim` response
instead stops the worker because the server may already have leased work;
inspect the dashboard queue before restarting. Both continuous and one-shot
modes require server tooling that advertises the same full-client presentation
contract, preventing an upgraded worker from attaching pixels through a server
that would discard their fidelity provenance.

Downloaded replays persist in a content-addressed cache under
`$XDG_CACHE_HOME/mc-recorder/replays/` when that variable is set, or
`~/.cache/mc-recorder/replays/` otherwise. They are reused only after size and
SHA-256 verification. Per-attempt workspaces under the cache are removed after
success or failure; `--keep-workspace` retains one for diagnosis, and
`--cache PATH` moves both areas. If the worker disappears, its fenced lease
expires and the job returns to the queue for a later worker attempt without
trusting the abandoned attempt. The long-running process does not make job
workspaces or Minecraft clients persistent.

If ServerReplay is still finalizing the disconnected player's archive, the
attempt is deferred without failing the job and becomes eligible again after a
30-second server-side cooldown. The worker keeps polling and may process later
ready jobs before retrying it.

For connections spanning several ServerReplay archives, the worker processes
segments newest-to-oldest. Newer coverage owns overlapping ticks; older segments
are cut off before that coverage. A segment with no matching timeline is
accepted as no coverage, and genuine gaps remain explicit missing RGB rather
than being synthesized. The dashboard reports such a valid but incomplete
attachment as **partial**. Imported files use server-owned relative paths, are
rehash-verified, and replace only the matching verified structured export. The
dataset catalog detects the new manifest and hashes and rebuilds its local index
automatically.

The viewer pages through a SQLite byte-offset index rather than loading a large
`samples.jsonl` into the browser. It provides a 20 Hz synchronized timeline and
RGB playback when attached, a server-bounded top-down player trajectory,
held-key and observed-click indicators, an accepted yaw/pitch delta vector,
player/connection filters, live server-reconstructed control buttons,
state-to-next-state differences, ordered packet actions, peers, transition
validity, and provenance. The trajectory and control HUD need no GUI renderer.
The contained scene store adds axis-selectable block slices at arbitrary
coordinates with projected entity and block-entity overlays. Reads are exact
random access by `frame_id`; the browser never replays earlier ticks.

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

For a standalone manual render outside the dashboard queue, render one recorded
connection from a completed Flashback archive:

```sh
mc-recorder render SESSION_ID \
  --player PLAYER_UUID \
  --connection CONNECTION_ID \
  --replay artifacts/replays/players/PLAYER_UUID/REPLAY.zip
```

`--connection` may be omitted when the selected player has exactly one recorded
connection. `--replay` may be omitted only when exactly one completed archive is
available for that player. The command launches the local client renderer by
default; `--prepare-only` writes the validated render job without launching it.

Standalone RGB jobs render the first-person hand/item and Minecraft HUD by
default, including the hotbar, crosshair, health, hunger, titles, boss bars,
action bar, and scoreboard. An episode-only standalone job has no exported
dataset from which to author the verified per-tick HUD sidecar, so it remains a
legacy/unverified GUI result; use the dashboard dataset workflow for faithful
inventory pixels. ServerReplay is configured to omit chat packets, and
client-only screens such as inventory or crafting menus cannot be
reconstructed. Pass `--no-gui` to a standalone manual render when a HUD-free
first-person image is explicitly required.

Render preparation records a stable replay byte size and SHA-256. The client
verifies both before opening the archive and again after RGB generation;
after the client exits, the CLI rehashes the archive and accepts the atomic
complete result only when all three checks match the prepared envelope.

Extract a random-access scene store directly from the immutable replay, without
a GUI client:

```sh
mc-recorder scene extract SESSION_ID \
  --player PLAYER_UUID \
  --connection CONNECTION_ID \
  --output artifacts/scenes/SESSION_ID.sqlite3 \
  [--force]
```

The output must cover every selected `player_state` tick. Segment gaps,
non-identical overlap, unknown state-affecting packets, source mutation, or
identity mismatch fail closed. `--from-tick`, `--to-tick`, and `--prepare-only`
are available for bounded/manual operation. Output parents are created without
accepting symlinked paths. Existing outputs fail by default; `--force` replaces
only a scene store that already passes the complete owned-store validation.

Attach manually completed RGB and scene results while exporting. Dashboard
generation performs scene extraction and attachment automatically; dashboard
RGB jobs perform the verified RGB re-export automatically:

```sh
mc-recorder export SESSION_ID \
  --frames artifacts/exports/render-jobs/SESSION_ID-PLAYER_UUID \
  --scene artifacts/scenes/SESSION_ID.sqlite3 \
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

Open-world coverage is client-visible. The recorder does not force chunk
generation. A `player_state.replay_coverage` hint records the center chunk and
view distance with `complete: false`; the scene store records only loaded
sections and represents every other position as unknown, so missing data is
distinguishable from a covered `minecraft:air` block.

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
evicted; do not assume coupled retention. Dataset generation holds shared locks
on every selected sealed epoch and replay through atomic publication, so quota
enforcement skips source units that are still in use.

```sh
mc-recorder storage status
mc-recorder storage enforce
```

## Configuration and contracts

`mc-recorder init` generates `recorder.toml`; [`recorder.example.toml`](recorder.example.toml)
documents every option with EULA acceptance disabled. Relative paths resolve
from the configuration file's directory.

For an API-independent local deployment, stage ServerReplay, Fabric API, and
Fabric Language Kotlin in `.mc-recorder/mods/`, then set
`mods.server_replay_project = ""`. The default remains the immutable Modrinth
version selector.

- [`schemas/source-record-v1.md`](schemas/source-record-v1.md) defines the
  combined capture stream, barriers, identity, replay alignment, and coverage.
- [`schemas/dataset-v2.md`](schemas/dataset-v2.md) defines canonical samples,
  exact modality envelopes, and the random-access scene store.
- [`tooling/README.md`](tooling/README.md) documents all CLI commands.

## Workspace layout

```text
deploy/         Docker Compose deployment
recorder-mod/   server-side Fabric capture sidecar
scene-extractor-mod/ headless replay-to-scene Fabric server
renderer-mod/   local Flashback first-person RGB renderer
schemas/        source and Dataset V2 contracts
tooling/        Python provisioning/export CLI
ServerReplay/   upstream server replay mod source
```

Chat, command text, and custom payload contents are redacted by the sidecar.
ServerReplay has its own privacy/storage implications; only record players who
have consented to dataset capture.
