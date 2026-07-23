# Minecraft Dataset Recorder mod

Fabric 1.21.8 server-side companion for ServerReplay. It records every connected player's decoded
serverbound packets, their authoritative main-thread application order, persistent control state,
and authoritative end-of-tick player state.

The mod creates `config/mc-recorder.json` on first launch:

```json
{
  "capture_root": "/captures",
  "epoch_ticks": 6000,
  "record_all_players": true,
  "writer_queue_capacity": 65536,
  "include_inventory_components": true
}
```

V1 always records all players. `/captures` is the container default and should be mounted to durable
host storage. Native launches should set it to an absolute writable path.

## Source layout

```text
<capture_root>/<session_id>/
  manifest.json
  session_end.json                 # complete/incomplete close status when available
  epochs/epoch-000000/
    events.jsonl
    manifest.json                  # immutable slice counts, byte length, SHA-256
```

The active epoch is named `events.jsonl.inprogress`; converters must ignore it. Rotation closes,
syncs, and atomically renames the event stream before publishing its manifest. A crash may leave an
in-progress epoch, but it does not mutate earlier published slices.

Epoch numbers advance at explicit end-of-tick boundaries. Automatic rotation still limits an epoch
to `epoch_ticks`. The recorder does not accept external promotion requests; tooling waits for
already-published slices and copies verified filesystem units when generating datasets.

Every JSONL record carries `session_id`, `epoch_index`, `server_tick`, and a global `sequence`.
`packet_arrival` records network observation order. `packet_apply` is stamped when
`PacketUtils.ensureRunningOnSameThread` returns on the server thread, immediately before the packet
handler body continues, and carries a global `apply_sequence`. `tick_start` and `tick_end` are explicit
barriers; packets executed between ticks are associated with the upcoming tick.

`control_state` contains the latest server-observed persistent movement flags and accepted per-tick
camera deltas. It is a 20 Hz semantic reconstruction, not physical keyboard or raw mouse telemetry.
Ordered discrete actions remain in `packet_apply`. `player_state` contains position,
rotation, velocity, game mode, health, hunger, experience, effects, and inventory. Inventory stacks
include an SNBT encoding when component capture is enabled.

Every `player_state` carries `state_barrier_apply_sequence`. Every join receives a fresh
`connection_id`, and all player-scoped records retain it until leave. V1 captures all connected
players, so multiplayer samples can be joined without conflating reconnects.

Once per player per tick the mod also sends `mc_recorder:timeline/v1` with the session ID,
connection ID, global server tick, and matching event sequence. ServerReplay records this payload
inside its independently rotated Flashback archive; the local renderer uses it for exact alignment.

Scene-capable captures declare
`flashback_capture_contract: "client_visible_scene_v1"` in the session manifest, `session_start`,
and embedded Flashback `mc_recorder` metadata. The recorder narrowly
overrides upstream packet exclusions for chunk unloads, player corrections, minecart steps, and
explicit entity movement/teleport/velocity. It does not override pause state or unrelated capture
settings. Mutable packet collections are copied before ServerReplay's asynchronous encoding.

## Intentional limitations

- The decoded packet object is available, but canonical raw bytes are not: re-encoding requires the
  connection's protocol/registry context and may not reproduce the received bytes. Known gameplay
  fields are normalized, and unknown packet types retain class/type/order metadata.
- Chat, commands, and custom payload contents are redacted by default.
- `player_state.replay_coverage` is only a best-effort client-visible center/view-distance hint and
  declares `complete=false`. The headless scene extractor materializes loaded block sections,
  entities, and block entities from replay state; unloaded positions remain explicitly unknown.
- Storage quota warnings and eviction of verified sidecar slices or stable completed replay archives
  are orchestrator responsibilities; the recorder itself only appends and publishes bounded
  immutable sidecar units.

## Build

This module intentionally has no Gradle wrapper. From the workspace root, use
the proto-managed Gradle task:

```sh
gradle --project-dir mods/recorder-mod test build
```
