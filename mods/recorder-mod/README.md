# Minecraft recorder mod

Fabric 1.21.8 server-side companion for ServerReplay. It records decoded
serverbound actions, authoritative main-thread application order,
reconstructed controls, and end-of-tick player state.

`config/mc-recorder.json`:

```json
{
  "artifacts_root": "/artifacts",
  "server_name": "minecraft",
  "server_instance_id": "00000000-0000-4000-8000-000000000000",
  "record_all_players": true,
  "writer_queue_capacity": 65536,
  "include_inventory_components": true
}
```

Unknown and removed configuration keys are rejected.

At player join the mod creates:

```text
<artifacts_root>/v1/<server>--<instance>/players/<player>--<uuid>/plays/<start>--<connection>/
  metadata.json
  capture/
    events.jsonl
    replay.zip
```

It does not create actions, scenes, or renders. Those are post-processing
outputs. ServerReplay writes one unrotated Flashback recording through a
timestamped working child; after close, the recorder moves the archive
unchanged to `capture/replay.zip`.

The recorder appends one buffered, connection-local `capture/events.jsonl`
stream. There are no sessions on disk, epochs, manifests, or sealing step.
After events are durable and the replay writer has closed, `metadata.json` is
atomically updated with the end tick; a null end tick means incomplete.

`packet_arrival` is diagnostic network-thread timing. `packet_apply` is the
authoritative action order. `control_state` is a semantic 20 Hz reconstruction,
not physical keyboard/mouse telemetry. `player_state` contains complete server
state including inventory/effects/abilities and an application barrier.

The recorder emits `mc_recorder:timeline/v1` once per player tick so replay
ticks align exactly with server ticks. Captured world scope remains
client-visible; unloaded cells and unopened-container contents may be unknown.

Build with:

```sh
pixi run build-recorder-mod
```
