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
for additional local mods.

## Runtime pins

The dev Compose service and the release image run the same server runtime and
download its mods from Modrinth at startup:

| Component | Version | Modrinth version ID |
| --- | --- | --- |
| Minecraft | 1.21.8 | - |
| Fabric Loader | 0.19.3 | - |
| ServerReplay | 3.0.1+1.21.8 | `TbWIikrT` |
| Fabric API | 0.136.1+1.21.8 | `g58ofrov` |
| Fabric Language Kotlin | 1.13.13+kotlin.2.4.10 | `bdhiINYC` |

Every mod is pinned by version ID and `MODRINTH_DOWNLOAD_DEPENDENCIES` is
`none`. Automatic dependency resolution picks the newest compatible release,
which can require a newer loader than the pin and stop the server from
starting. The scene extractor also rejects a replay unless its Minecraft,
loader, Fabric API, and Fabric Language Kotlin versions equal the versions
that the server recorded.

These pins are not read from `deploy/.env`. To change one, update all of these
files together:

- `mods/recorder-mod/build.gradle.kts` (versions and Modrinth version IDs);
- `deploy/docker-compose.yml`;
- `deploy/minecraft-server.Dockerfile`;
- `processors/scene-extractor/gradle.properties`.

`gradle --project-dir mods/recorder-mod build` runs `verifyServerRuntimePins`,
which fails when these files disagree. It does not check that a Modrinth
version ID names the declared version; confirm that with
`https://api.modrinth.com/v2/version/<id>`. `mods/renderer-mod/gradle.properties`
uses the same loader and Fabric API versions, but no runtime check requires
them to match.

## Development server

The helper builds the recorder mod and stages both mod configurations:
`server-replay/config.json` and `recorder-minecraft.json`. It reads
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
