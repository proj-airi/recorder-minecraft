# Minecraft gameplay recorder

This repository records per-player Minecraft 1.21.8 connections and
post-processes them into reconstructed actions, random-access scene/state data,
and optional first-person image frames.

The durable contract is a normal directory tree:

```text
artifacts/v1/<server>--<instance>/players/<player>--<uuid>/plays/<start>--<connection>/
  metadata.json
  replays/
  actions.jsonl       # optional post-processing result
  scene.sqlite3       # optional post-processing result
  renders/            # optional post-processing result
    fpv_frames/
```

There is no play-bundle archive, custom extension, publisher, standalone data
viewer, dashboard, dataset export, or backward-compatibility layer.

## Setup and checks

```sh
proto install --config-mode local
pixi install --locked
pixi run --locked check
```

Initialize one server identity:

```sh
pixi run minerec init --accept-eula
cp deploy/.env.example deploy/.env
```

`minerec init` generates `server.instance_id` once. Keep that UUID stable;
`server.name` is only the editable display-name portion of artifact paths.

## Record

```sh
hack/minecraft-server start
# join localhost:25565
hack/minecraft-server stop
```

The recorder creates the canonical play directory at join. It writes
`metadata.json` and copies completed Flashback ZIPs into `replays/`. Raw JSONL
epochs and ServerReplay working files stay under `.mc-recorder/intermediate`
while `.mc-recorder/runtime` is reserved for short-lived processor jobs and
locks.

## Post-process

Every processor accepts explicit source and destination paths; none knows how
to find or form the artifact hierarchy.

```sh
pixi run minerec actions extract INTERMEDIATE_SESSION \
  --player PLAYER_UUID --connection CONNECTION_UUID \
  --output PLAY/actions.jsonl

pixi run minerec scene extract INTERMEDIATE_SESSION \
  --player PLAYER_UUID --connection CONNECTION_UUID \
  --replay PLAY/replays/000000--SEGMENT_UUID.zip \
  --output PLAY/scene.sqlite3

pixi run minerec render INTERMEDIATE_SESSION \
  --player PLAYER_UUID --connection CONNECTION_UUID \
  --replay PLAY/replays/000000--SEGMENT_UUID.zip \
  --output PLAY/renders
```

Scene extraction accepts repeated `--replay` arguments. Rendering currently
takes one Flashback segment and writes PNGs plus `frames.jsonl` beneath
`renders/fpv_frames/`.

See [Artifacts V1](docs/specs/artifacts-v1.md),
[Source Record V1](docs/specs/source-record-v1.md), and
[the minerec CLI](apps/minerec/README.md).

## Modules

```text
mods/recorder-mod/         server recorder and canonical artifact writer
mods/scene-extractor-mod/ headless Flashback scene reducer
mods/renderer-mod/        client-only FPV frame renderer
apps/minerec/             explicit file-to-file processors
deploy/                   recorder server Compose configuration
```

Record only players who have consented. Metadata, inventory, actions, replay
archives, and scene databases can contain sensitive gameplay data.
