# Recorder server deployment

Initialize `recorder.toml`, copy `.env.example` to `.env`, and edit host paths:

```sh
pixi run recorder-minecraft init --accept-eula
cp deploy/.env.example deploy/.env
hack/minecraft-server start
```

The helper builds the recorder mod and stages both mod configurations. It reads
the stable server instance UUID and either the configured `server.name` or the
machine hostname from `recorder.toml`.

The Minecraft container mounts:

- `MC_ARTIFACTS_HOST_DIR` at `/artifacts` for canonical Artifacts V1 plays;
- `MC_DATA_HOST_DIR` at `/data` for the Minecraft server/world.

No storage monitor deletes artifacts. No dashboard, RabbitMQ, render worker,
or viewer service is part of this Compose file.

Vanilla empty-server tick pausing is disabled so recorder tick boundaries
continue after the last player disconnects.

ServerReplay duration and size rotation are disabled. The recorder redirects
its one live Flashback writer per connection to `<play>/capture/replay/`, then
moves the closed archive unchanged to `<play>/capture/replay.zip`.
