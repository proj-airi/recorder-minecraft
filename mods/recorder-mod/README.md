# Minecraft recorder mod

Fabric 1.21.8 server-side companion for ServerReplay. It records decoded
serverbound actions, authoritative main-thread application order,
reconstructed controls, and end-of-tick player state.

`config/mc-recorder.json`:

```json
{
  "artifacts_root": "/artifacts",
  "intermediate_root": "/runtime/intermediate",
  "server_name": "minecraft",
  "server_instance_id": "00000000-0000-4000-8000-000000000000",
  "epoch_ticks": 6000,
  "record_all_players": true,
  "writer_queue_capacity": 65536,
  "include_inventory_components": true
}
```

Unknown/legacy configuration keys are rejected. Artifact and intermediate
roots must be separate and non-nested.

At player join the mod creates:

```text
<artifacts_root>/v1/<server>--<instance>/players/<player>--<uuid>/plays/<start>--<connection>/
  metadata.json
  replays/
```

It does not create actions, scenes, or renders. Those are post-processing
outputs. ServerReplay continues writing independently under its working root;
after each Flashback ZIP is complete, the recorder copies its exact bytes into
the play's `replays/` directory. Replay rotations are never merged.

Raw combined event sessions are written to
`<intermediate_root>/sessions/<session-id>/`. Active epochs use
`events.jsonl.inprogress`; a seal atomically publishes `events.jsonl` and its
count/size/SHA-256 manifest.

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
