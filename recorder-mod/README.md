# Minecraft Dataset Recorder mod

Fabric 1.21.8 server-side companion for ServerReplay. It records every connected player's decoded
serverbound packets, their authoritative main-thread application order, persistent control state,
and authoritative end-of-tick player state.

The mod creates `config/mc-recorder.json` on first launch:

```json
{
  "capture_root": "/captures",
  "control_root": "/control",
  "epoch_ticks": 6000,
  "record_all_players": true,
  "writer_queue_capacity": 65536,
  "include_inventory_components": true
}
```

V1 always records all players. `/captures` is the container default and should be mounted to durable
host storage. Native launches should set it to an absolute writable path. `/control` should be a
separate host runtime directory bind-mounted read/write for dashboard status, the current-session
connection ledger, and seal request/response spools.

## Source layout

```text
<capture_root>/<session_id>/
  manifest.json
  session_end.json                 # complete/incomplete close status when available
  epochs/epoch-000000/
    events.jsonl
    manifest.json                  # sealed=true, counts, byte length, SHA-256
```

The active epoch is named `events.jsonl.inprogress`; converters must ignore it. Rotation closes,
syncs, and atomically renames the event stream before publishing its manifest. A crash may leave an
in-progress epoch, but it does not mutate earlier sealed epochs.

Epoch numbers advance at explicit end-of-tick boundaries. Automatic rotation still limits an epoch
to `epoch_ticks`; a validated manual request for a disconnected connection seals the current epoch
after `tick_end` and continues the same session in the next epoch. Requests that coincide with an
automatic boundary share one seal.

The recorder atomically refreshes `<control_root>/status.json` and
`<control_root>/connections.json`. The latter contains one row per connection, including reconnects,
and terminal tick/sequence fields after disconnect or clean server shutdown. Matching snapshots in
`<control_root>/sessions/` preserve completed rows across later server starts. The dashboard writes
`requests/<uuid>.json` and waits for `responses/<uuid>.json`; a success response is published only
after the covering epoch manifest exists. These runtime files are not a substitute for source and
manifest integrity validation.

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

## Intentional limitations

- The decoded packet object is available, but canonical raw bytes are not: re-encoding requires the
  connection's protocol/registry context and may not reproduce the received bytes. Known gameplay
  fields are normalized, and unknown packet types retain class/type/order metadata.
- Chat, commands, and custom payload contents are redacted by default.
- `player_state.replay_coverage` is only a best-effort client-visible center/view-distance hint and
  declares `complete=false`. The local renderer can materialize coverage-masked voxel crops from
  replay state, but block entities are not materialized in voxel V1.
- Storage quota warnings and eviction of verified sealed epochs or stable completed replay archives
  are orchestrator responsibilities; the recorder itself only seals immutable sidecar units.

## Build

This module intentionally has no duplicate wrapper. From the workspace root, use the shared wrapper:

```sh
./gradlew --project-dir recorder-mod test build
```
