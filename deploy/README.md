# Docker deployment

`docker-compose.yml` is driven by the generated `.mc-recorder/compose.env` file.
Do not invoke it directly: `mc-recorder server start` validates the EULA, builds
and stages the local capture mod, writes both mod configurations, checks the
combined capture/replay quota, and then invokes Docker Compose.

The Minecraft service uses the exact
`itzg/minecraft-server:2026.7.0-java21` image, Fabric, and Minecraft 1.21.8.
ServerReplay is pinned to the immutable Modrinth project/version selector
`server-replay:TbWIikrT`. The storage monitor uses the exact
`python:3.11.15-alpine3.24` image. The local capture mod is bind-mounted through
the image's documented `/mods` synchronization point. Compose uses the image's
`mc-health` probe, so `mc-recorder server start --wait` waits for a playable
server rather than only a running container.

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
