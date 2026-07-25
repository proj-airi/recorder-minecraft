# Minecraft gameplay recorder

This repository records per-player Minecraft 1.21.8 connections into a small
primitive capture, then post-processes explicit files into actions,
random-access scene/state data, and optional first-person frames.

```text
artifacts/v1/<server>--<instance>/players/<player>--<uuid>/plays/<start>--<connection>/
  metadata.json
  capture/
    events.jsonl
    replay.zip
  actions.jsonl       # optional post-process result
  scene.sqlite3       # optional post-process result
  renders/            # optional render result
    fpv_frames/
```

There are no epochs, replay segments, seal files, bundle archives, custom
extensions, publishers, downloaders, dashboard viewers, or compatibility
readers.

## Setup and checks

```sh
proto install --config-mode local
pixi install --locked
pixi run --locked check
```

Initialize one stable server instance identity:

```sh
pixi run minerec init --accept-eula
cp deploy/.env.example deploy/.env
```

## Record

```sh
hack/minecraft-server start
# join localhost:25565
hack/minecraft-server stop
```

At join, the recorder creates a play and streams one JSONL file plus one
unrotated Flashback replay directly into `capture/`. At disconnect it writes
the metadata end tick last. That completed directory can be copied with normal
SSH/rsync and passed to independent processors.

## Post-process

```sh
# Run every stage for one play directory:
hack/process-play PLAY

# Or run the processors independently:
pixi run minerec actions extract \
  --metadata PLAY/metadata.json \
  --events PLAY/capture/events.jsonl \
  --output PLAY/actions.jsonl

pixi run minerec scene extract \
  --metadata PLAY/metadata.json \
  --events PLAY/capture/events.jsonl \
  --replay PLAY/capture/replay.zip \
  --output PLAY/scene.sqlite3

pixi run minerec render \
  --metadata PLAY/metadata.json \
  --events PLAY/capture/events.jsonl \
  --replay PLAY/capture/replay.zip \
  --output PLAY/renders
```

Processors know only the files and output supplied on the command line. Their
temporary jobs and locks live under `.mc-recorder/runtime`; durable results can
be placed back in the play as shown above.

See [Artifacts V1 Pipeline](docs/specs/artifacts-v1.md),
[Primitive Capture V1](docs/specs/capture-v1.md), and
[the minerec CLI](apps/minerec/README.md).

## Modules

```text
mods/recorder-mod/         server recorder and canonical capture writer
mods/scene-extractor-mod/ headless Flashback scene reducer
mods/renderer-mod/        client-only FPV frame renderer
apps/minerec/             explicit file-to-file processors
deploy/                   recorder server Compose configuration
```

Record only players who have consented. Metadata, inventory, actions, replay
archives, and scene databases can contain sensitive gameplay data.
