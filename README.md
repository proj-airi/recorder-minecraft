# Minecraft gameplay dataset recorder

This workspace provisions a Minecraft 1.21.8 Fabric server that records every
connected player's server-observed gameplay into durable five-minute capture
slices, exports canonical state/action transitions, reconstructs a random-access
client-visible world scene for every selected tick without a GUI, and can render
first-person RGB frames in a Flashback client.

It combines three pieces:

1. **ServerReplay** records one replay archive per player, including the chunks
   and entities visible along that player's trajectory, without a graphics
   stack on the server.
2. **`mods/recorder-mod`** writes a synchronized server-side JSONL sidecar containing
   all-player state, decoded semantic actions, connection identity, apply
   barriers, and replay timeline markers.
3. **`minerec`** provisions Docker, validates immutable slices,
   enforces capture/replay retention, exports trainable samples, finalizes
   portable per-connection play bundles, and launches headless scene extraction
   plus deterministic RGB jobs through `mods/renderer-mod`.

## Current capabilities

- Minecraft 1.21.8 on `itzg/minecraft-server:2026.7.0-java21`, with
  ServerReplay pinned by immutable Modrinth selector `server-replay:TbWIikrT`.
- Automatic capture of every connected player in multiplayer sessions.
- One combined event stream ordered by global `sequence`, with a distinct
  `connection_id` for each player join.
- Authoritative post-tick player state and ordered, applied serverbound semantic
  actions at Minecraft's 20 Hz tick rate.
- Nominal five-minute sidecar slices with count/size/SHA-256 manifests.
- Independent ServerReplay archives aligned exactly through embedded
  `mc_recorder:timeline/v1` markers.
- Standard Flashback archives that can also be inspected interactively in a
  Minecraft 1.21.8 client with Flashback 0.39.5. The renderer pins immutable
  Modrinth version ID `9YgAwnpm`, because the display version is reused across
  incompatible Minecraft variants.

  If Modrinth's Maven endpoint is unavailable, set
  `MC_RECORDER_FLASHBACK_JAR` to the Flashback 0.39.5 JAR specifically built
  for Minecraft 1.21.8 before running `pixi run minerec render-worker`.
- Canonical `state + action -> next_state` JSONL samples with provenance and
  exact-key modality attachment.
- Dataset V2 exports with a contained SQLite scene store that supports direct
  lookup of block, entity, and block-entity state at every selected tick.
- Immutable `.mcplay.zip` bundles containing reconstructed actions, Scene Store
  V2 player/world state, and the exact contributing Flashback replay archives.
- A dashboard-independent local Vue viewer for drag/drop bundle inspection;
  first-person MP4 playback is optional.
- Server-only scene extraction from client-visible replay packets; no Minecraft
  window, graphics stack, source world mount, or forward replay is needed by
  the dataset viewer.
- Optional first-person 20 FPS RGB PNG rendering in a local Flashback client.
- Capture/replay-volume warnings and oldest-first eviction of only verified
  sidecar slices or stable, completed replay archives.

Scene extraction replays the recorded network stream through Minecraft's server
packet codecs, materializes the logical client-visible scene, and structurally
shares unchanged values in `scene-v1.sqlite3`. Unloaded cells remain unknown
rather than becoming valid air. The default `client_visible` scope includes
entities and block entities and is marked sensitive when full captured metadata
is retained. Each job uses its own `runtime/scene-jobs/<job-id>` work directory
and launches the headless `mc-recorder-scene-extractor` CLI; it never starts a
Minecraft server, mounts a captured world, or binds the capture server's port.

## Requirements

- Pixi for the Python CLI workspace;
- proto for OpenJDK 21 and Gradle 9.6.1;
- Docker with Docker Compose;
- RabbitMQ from the included Compose stack for queued RGB dispatch;
- a logged-in graphical desktop on any machine that runs a persistent render consumer; and
- a normal Minecraft client account for joining the capture server.

See [Development Environment](docs/environment.md) for tool ownership,
installation, and troubleshooting.

## Quickstart

From the workspace root:

```sh
./hack/install
```

Read the [Minecraft EULA](https://aka.ms/MinecraftEULA), then explicitly opt in
by editing `deploy/.env`:

```sh
MC_EULA=TRUE
```

Start the server and connect to `localhost:25565`:

```sh
./hack/minecraft-server start
```

Recording starts automatically when players join. The capture side requires no
client mod; a normal Minecraft 1.21.8 client can connect. Stop cleanly before
consuming the latest files, then inspect and validate the capture:

```sh
./hack/minecraft-server stop
pixi run minerec episodes list
pixi run minerec episodes validate SESSION_ID
```

### LAN dashboard

The dashboard can run either as a host process or as the Compose-managed
`dashboard` service. Minecraft server lifecycle is managed outside the Python
package with `hack/minecraft-server start`, `hack/minecraft-server stop`, or
direct Docker Compose commands using `deploy/.env`. Run the dashboard as a user
that can read the configured capture/export paths, and use launchd, systemd, or
another host service manager when it should survive logouts or reboots.

Set both required HTTP Basic credentials, then start the service:

```sh
export MC_RECORDER_DASHBOARD_USERNAME=recorder
export MC_RECORDER_DASHBOARD_PASSWORD='replace-with-a-long-password'
./hack/dashboard start
```

`hack/dashboard start` installs workspace Node dependencies and builds the
dashboard before serving it. Compose dashboard startup also needs an existing
`apps/dashboard/dist` build; run `hack/dashboard build` first when starting the
dashboard container directly. Set `MC_RECORDER_SKIP_DASHBOARD_BUILD=1` when you
want to reuse an existing host-mode build.

The default `[dashboard]` listener is `0.0.0.0:8765`, so another trusted-LAN
machine can open `http://SERVER_ADDRESS:8765/`. Basic authentication over plain
HTTP does **not** encrypt the username or password. Use this deployment only on
a LAN you trust, or place an HTTPS reverse proxy in front of it. Every route is
authenticated; mutating requests additionally require same-origin and CSRF
checks.

The Dashboard is an artifact viewer and render-pipeline operator. It scans
capture directories, completed Flashback replay ZIPs, and verified dataset
exports directly from their configured filesystem roots. It does not start,
stop, inspect, or command the Minecraft server.

Click **Render RGB** on a completed Flashback replay ZIP to create an artifact
render request. The Compose `render-preparer` revalidates the ZIP, takes a
read-only prefix snapshot of the disconnected connection's append-only capture,
publishes and verifies its connection-scoped dataset, then advances the request
from `preparing_dataset` to `queued`. This works when the connection records are
still in `events.jsonl.inprogress`; Minecraft does not need to stop or rotate
the active recorder epoch.

The headless Dashboard and preparer never start a graphics client. The host
worker streams the verified dataset into a hash-bound structured-HUD sidecar
and applies its exact inventory, selected slot, health, food, air, and
experience state before each frame. Existing dataset connections retain their
own **Render RGB** action.

Successful scene jobs are removed after the contained store is published. The
newest failed or intentional `--prepare-only` job is retained for diagnostics;
older marker-owned crash jobs are pruned under the global operation lock.
Non-owned or symlinked directories are never removed.

The Compose `render-preparer` builds artifact-request datasets, and
`render-dispatcher` publishes queued jobs from the durable SQLite outbox to
RabbitMQ. Start one or more persistent consumer processes
from a logged-in graphical desktop session on a machine with the same
workspace, RabbitMQ access, OpenJDK 21, and Gradle:

```sh
MC_RECORDER_RABBITMQ_URL=amqp://guest:guest@localhost:5672/%2F \
  pixi run minerec render-worker
```

The consumer keeps one RabbitMQ connection with prefetch 1, claims each exact
queued job through the Dashboard's token-authenticated render-control endpoint,
verifies its replay segments and structured-HUD sidecar, launches one local
Java GUI renderer, imports the integrity-bound bundles, acknowledges the
message, and continues with the next job until interrupted. Compose keeps the
authoritative queue SQLite database in its `render-control` named volume rather
than a macOS bind mount.

If a claimed job fails, the consumer reports/fences that attempt and rejects
that message before continuing. An ambiguous claim outcome is requeued and
stops the consumer for operator inspection. Consumers require server tooling that advertises the same
full-client presentation contract, preventing an upgraded worker from attaching
pixels through a server that would discard their fidelity provenance.

Downloaded replays persist in a content-addressed cache under
`$XDG_CACHE_HOME/minerec/replays/` when that variable is set, or
`~/.cache/minerec/replays/` otherwise. They are reused only after size and
SHA-256 verification. Per-attempt workspaces under the cache are removed after
success or failure; `--keep-workspace` retains one for diagnosis, and
`--cache PATH` moves both areas. If the worker disappears, its fenced lease
expires and the RabbitMQ message can be retried later without trusting the
abandoned attempt. Job workspaces and Minecraft clients remain per-attempt;
the consumer process is persistent.

If ServerReplay is still finalizing the disconnected player's archive, the
attempt is deferred without failing the job and becomes eligible again after a
30-second server-side cooldown. The dispatcher waits for another mod-emitted
ready file before publishing more work for that connection.

For connections spanning several ServerReplay archives, the worker processes
segments newest-to-oldest. Newer coverage owns overlapping ticks; older segments
are cut off before that coverage. A segment with no matching timeline is
accepted as no coverage, and genuine gaps remain explicit missing RGB rather
than being synthesized. The dashboard reports such a valid but incomplete
attachment as **partial**. Imported files use server-owned relative paths and
are rehash-verified. RGB references are atomically published under
`artifacts/exports/.dataset-attachments/<dataset-id>/rgb.json`; Dataset core
files and their manifest are not replaced. The dataset catalog detects the
attachment fingerprint and rebuilds its local index automatically.

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
pixi run minerec export SESSION_ID \
  --player PLAYER_UUID \
  --connection CONNECTION_ID
```

The export contains `samples.jsonl`, `states.jsonl`, `actions.jsonl`,
`modalities.jsonl`, and a provenance manifest under
`artifacts/exports/SESSION_ID.dataset/`.

The dataset viewer indexes every canonical state/modality tick. Transition
controls are joined when present, while a connection's final tick remains
viewable with its scene and an explicitly unavailable transition.

Finalize one complete connection-scoped Dataset V2 directory as a portable
bundle, then inspect it with the standalone local viewer:

```sh
pixi run minerec bundle create \
  artifacts/exports/CONNECTION.dataset
pixi run build-viewer
pixi run minerec viewer \
  artifacts/v1/SERVER--INSTANCE/players/PLAYER--UUID/plays/PLAY/BUNDLE.mcplay.zip
```

`bundle create` fails unless the selected connection is closed and its actions,
authoritative state, Scene Store V1 provenance, and exact replay segments are
complete and valid. It builds Scene Store V2 and publishes atomically. Add
`--fpv FPV.mp4 --fpv-timeline FPV.timeline.jsonl` to create a rendered revision;
rendering is optional.

Both subject filters are repeatable. Player, connection, and tick-range
filters are intersected, so a reconnect can be exported without mixing its
states or actions with another connection. Selected samples still include
other recorded players as peer context.

Extract a random-access scene store directly from the immutable replay, without
a GUI client:

```sh
pixi run minerec scene extract SESSION_ID \
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
The canonical subject pose comes from sliced, hash-verified `player_state`
records and is applied before snapshot hashing. Scene extraction requires new
schema-v3 replay metadata with the `client_visible_scene_v1` capture contract;
older replay archives are not accepted as scene sources.

Attach manually completed RGB and scene results while exporting. Dashboard RGB
jobs instead publish a separate verified attachment index:

```sh
pixi run minerec export SESSION_ID \
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

- whole sidecar slices whose final stream matches the manifest's record
  count, byte count, and SHA-256; or
- completed `.zip`/`.mcpr` replay archives that are readable, unchanged across
  inspection, and at least five minutes old.

Active/incomplete slices, recent/partial replay files, directories, symlinks,
and unexpected paths are ineligible. Sidecar and replay units are independently
evicted; do not assume coupled retention. Dataset generation holds shared locks
on every selected sidecar slice and replay through atomic publication, so quota
enforcement skips source units that are still in use.

```sh
pixi run minerec storage status
pixi run minerec storage enforce
```

## Configuration and contracts

`minerec init` generates `recorder.toml`; [`recorder.example.toml`](recorder.example.toml)
documents every option with EULA acceptance disabled. Relative paths resolve
from the configuration file's directory.

Docker Compose reads deployment settings from `deploy/.env`. Copy
`deploy/.env.example` to `deploy/.env`, then edit values such as
`MC_MODRINTH_PROJECTS`, ports, memory, host paths, and storage quota there. For
an API-independent local deployment, stage ServerReplay, Fabric API, and Fabric
Language Kotlin in `.mc-recorder/mods/`, then set `MC_MODRINTH_PROJECTS=` in
`deploy/.env`.

- [`docs/specs/source-record-v1.md`](docs/specs/source-record-v1.md) defines the
  combined capture stream, barriers, identity, replay alignment, and coverage.
- [`docs/specs/dataset-v2.md`](docs/specs/dataset-v2.md) defines canonical samples,
  exact modality envelopes, and the random-access scene store.
- [`docs/specs/play-bundle-v1.md`](docs/specs/play-bundle-v1.md) defines the
  portable connection bundle, Scene Store V2, integrity rules, and local viewer.
- [`apps/minerec/README.md`](apps/minerec/README.md) documents all CLI commands.

## Workspace layout

```text
deploy/         Docker Compose deployment
mods/recorder-mod/   server-side Fabric capture sidecar
mods/scene-extractor-mod/ headless replay-to-scene extractor CLI
mods/renderer-mod/   local Flashback first-person RGB renderer
docs/specs/     source and Dataset V2 contracts
apps/minerec/   Python provisioning/export/bundle/viewer CLI
apps/viewer/    standalone Vue play-bundle viewer
ServerReplay/   upstream server replay mod source
docs/           development environment notes
```

Chat, command text, and custom payload contents are redacted by the sidecar.
ServerReplay has its own privacy/storage implications; only record players who
have consented to dataset capture.
