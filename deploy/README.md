# Recorder server deployment

Initialize `recorder.toml`, copy `.env.example` to `.env`, and edit host paths:

```sh
pixi run minerec init --accept-eula
cp deploy/.env.example deploy/.env
hack/minecraft-server start
```

The helper builds the recorder mod and stages both mod configurations. It reads
the stable server name and instance UUID from `recorder.toml`.

The Minecraft container mounts:

- `MC_ARTIFACTS_DIR` at `/artifacts` for canonical Artifacts V1 plays;
- `MC_INTERMEDIATE_DIR` at `/intermediate` for raw sidecars and ServerReplay working
  files; and
- `MC_DATA_DIR` at `/data` for the Minecraft server/world.

No storage monitor deletes artifacts. No dashboard, RabbitMQ, render worker,
or viewer service is part of this Compose file.

Vanilla empty-server tick pausing is disabled so recorder tick boundaries
continue after the last player disconnects.
