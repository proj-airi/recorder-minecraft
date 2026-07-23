# Docker deployment

`docker-compose.yml` is driven by `deploy/.env`. Copy `deploy/.env.example` to
`deploy/.env`, edit it for the host, then run Docker Compose directly when you
only need container lifecycle control:

```sh
docker compose --env-file deploy/.env --file deploy/docker-compose.yml up --detach
docker compose --env-file deploy/.env --file deploy/docker-compose.yml stop
```

Use `hack/minecraft-server prepare` to build and stage the local capture mod and
write the mod configuration files before the first direct Compose start.
`hack/minecraft-server start` runs that preparation step and then invokes the
same Docker Compose command.

The Minecraft service uses the exact
`itzg/minecraft-server:2026.7.0-java21` image, Fabric, and Minecraft 1.21.8.
ServerReplay is pinned to the immutable Modrinth project/version selector
`server-replay:TbWIikrT`. Python services share the local `minerec:local` image
built from `apps/minerec/Dockerfile`; that image installs the locked Pixi
environment from `pixi.toml` and `pixi.lock`, including the editable
`apps/minerec` package, OpenJDK 21, and the headless scene extractor
distribution produced by `pixi run build-scene-extractor-mod`. Use
`pixi run build-minerec-image` after extractor changes so the Docker build
context contains the installed CLI. The local capture mod is bind-mounted
through the Minecraft image's documented `/mods` synchronization point. Compose
uses the image's `mc-health` probe, so `hack/minecraft-server start` waits for a
playable server rather than only a running container.

The storage monitor sees `/captures` and `/replays`, but cannot access the world
or server data. After the configured quota is reached it may remove oldest
immutable source units: sidecar epochs whose seal/count/size/SHA-256 envelope
verifies, or readable completed replay archives that have remained unchanged
for at least five minutes. Active/incomplete epochs, recent or partial replay
files, directories, and symlinks are never eviction candidates.

Vanilla empty-server tick pausing is disabled with
`PAUSE_WHEN_EMPTY_SECONDS=-1`. The recorder heartbeat and seal-request control
plane run on server ticks, so they must remain live after the last player
disconnects even though Minecraft otherwise has no active players.
