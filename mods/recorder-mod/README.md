# Minecraft recorder mod

Fabric 1.21.8 server-authoritative companion for ServerReplay. The same mod jar
runs with a dedicated server or the integrated server hosted by a game client.
It records decoded serverbound actions, authoritative main-thread application
order, reconstructed controls, and end-of-tick player state.

Recording starts automatically when a local Minecraft server starts. Merely
launching the client or joining a remote server does not load recorder
configuration or create a play. If an integrated server is opened to LAN, every
connected player is recorded under the existing one-play-per-connection
contract.

Both deployments use `config/recorder-minecraft.json`:

```json
{
  "artifacts_root": "artifacts",
  "server_name": "minecraft",
  "server_instance_id": "00000000-0000-4000-8000-000000000000",
  "record_all_players": true,
  "writer_queue_capacity": 65536,
  "include_inventory_components": true
}
```

Unknown and removed configuration keys are rejected.
Relative `artifacts_root` values resolve from the Minecraft game directory, so
the generated default writes below `<game-directory>/artifacts`. Existing
absolute paths such as `/artifacts` retain their meaning.

The recording profile owns ServerReplay configuration; this mod does not
rewrite it. The effective ServerReplay settings must enable automatic recording
for all players, use Flashback encoding, retain custom payloads, and disable
rotation. In particular:

```json
{
  "automatically_record": true,
  "default_encoding": "flashback",
  "ignore_custom_payloads": false,
  "max_duration": "0s",
  "max_file_size": "0 B",
  "player_predicate": { "type": "all" },
  "restart_after_max_duration": false,
  "restart_after_max_file_size": false
}
```

This is a settings excerpt, not a complete ServerReplay configuration file.
If ServerReplay fails to provide an archive, the play remains incomplete and
processors reject it.

At player join the mod creates:

```text
<artifacts_root>/v1/<server>--<instance>/players/<player>--<uuid>/plays/<start>--<connection>/
  metadata.json
  capture/
    events.jsonl
    replay.zip
```

Once per server start, the mod also writes a world session beside `players/`:

```text
<artifacts_root>/v1/<server>--<instance>/world/sessions/<start>--<session>/
  metadata.json
  world-events.jsonl
```

The world stream records the server-side contents of every loaded container
block entity: a snapshot of each one at session start, when it loads, and when
its contents change (at most once per tick), plus a removal record when it
unloads or is destroyed. Containers with an unrolled loot table are recorded
as `LOOT_UNGENERATED`, never as empty. A world stream failure disables only the
world stream; Play capture continues. The stream is always on and needs no
configuration key.

It does not create actions, scenes, or renders. Those are post-processing
outputs. ServerReplay writes one unrotated Flashback recording through a
timestamped working child; after close, the recorder moves the archive
unchanged to `capture/replay.zip`.

The recorder appends one buffered, connection-local generated ProtoJSON `capture/events.jsonl`
stream. A play has no session subdirectories, epochs, manifests, or sealing step.
After events are durable and the replay writer has closed, `metadata.json` is
atomically updated with the end tick; a null end tick means incomplete.

`packet_arrival` is diagnostic network-thread timing. `packet_apply` is the
authoritative action order. `control_state` is a semantic 20 Hz reconstruction,
not physical keyboard/mouse telemetry. `player_state` contains complete server
state including inventory/effects/abilities and an application barrier.
`client_information` and `container_view` are actor-perception records: the
settings the client reported, and the container contents this player was
actually sent, linked to the backing block when the menu came from one.

The recorder emits `mc_recorder:timeline/v1` once per player tick so replay
ticks align exactly with server ticks. Play capture scope remains
client-visible; unloaded cells may be unknown. Container block entity contents
come from the world stream, which the play references in
`world_container_truth`. Entity-held inventories are not recorded
(`world_entities_not_recorded`).

Build with:

```sh
gradle --project-dir mods/recorder-mod build
```
