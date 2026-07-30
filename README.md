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
pixi run recorder-minecraft init --accept-eula
cp deploy/.env.example deploy/.env
```

## Record

```sh
hack/minecraft-server start
# join localhost:25565
hack/minecraft-server stop
```

At join, the recorder creates a play and streams one generated ProtoJSON line file plus one
unrotated Flashback replay directly into `capture/`. At disconnect it writes
the metadata end tick last. That completed directory can be copied with normal
SSH/rsync and passed to independent processors.

## Post-process

```sh
# Run every stage for one play directory:
hack/process-play PLAY

# Or run the processors independently:
pixi run recorder-minecraft actions extract \
  --metadata PLAY/metadata.json \
  --events PLAY/capture/events.jsonl \
  --output PLAY/actions.jsonl

pixi run recorder-minecraft scene extract \
  --metadata PLAY/metadata.json \
  --events PLAY/capture/events.jsonl \
  --replay PLAY/capture/replay.zip \
  --output PLAY/scene.sqlite3

pixi run recorder-minecraft render \
  --metadata PLAY/metadata.json \
  --events PLAY/capture/events.jsonl \
  --replay PLAY/capture/replay.zip \
  --output PLAY/renders
```

Processors know only the files and output supplied on the command line. Their
temporary jobs and locks live under `.recorder/minecraft/runtime`; durable results can
be placed back in the play as shown above.

Each `scene.sqlite3` is a self-contained, immutable per-play datastore. Ent
opens that explicit file read-only; writable command-scoped stores are created
and closed through `samber/do`. The repository has no shared base database.

See [Artifacts V1 Pipeline](docs/specs/artifacts-v1.md),
[Primitive Capture V1](docs/specs/capture-v1.md),
[Terms and Concepts](docs/TERMS_AND_CONCEPTS.md), and
[the recorder-minecraft CLI](cmd/recorder-minecraft/README.md).

## Modules

```text
mods/recorder-mod/         server recorder and canonical capture writer
mods/renderer-mod/        client-only FPV frame renderer
processors/scene-extractor/ headless Flashback scene reducer
cmd/recorder-minecraft/    Go file-to-file processor CLI
apis/proto/               Protobuf artifact contracts
databases/scene/           Ent model for per-play scene.sqlite3 files
deploy/                   recorder server Compose configuration
```

Record only players who have consented. Metadata, inventory, actions, replay
archives, and scene databases can contain sensitive gameplay data.
