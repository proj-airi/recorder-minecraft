# Recorder server deployment

Initialize `recorder.toml`, copy `.env.example` to `.env`, and edit host paths:

```sh
go run ./cmd/recorder-minecraft init --accept-eula
cp deploy/.env.example deploy/.env
hack/minecraft-server start
```

Release builds publish `ghcr.io/proj-airi/recorder-minecraft/minecraft-server`
with the recorder mod already installed. Image tags pair the Minecraft and
recorder release versions, such as `1.21.8-0.2.0` for recorder release `v0.2.0`.
Set `MC_SERVER_IMAGE` to one of these tags to use it; `/mods` remains available
for additional local mods. The image keeps ServerReplay pinned through
`MODRINTH_PROJECTS`, so startup still resolves that runtime dependency from
Modrinth.

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
