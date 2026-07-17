# Source record v1

An epoch is the smallest immutable, validated, and evictable capture unit. All
records use one server-owned timeline; file write time is never used to join
modalities.

## Identity and ordering

Every record contains:

- `schema_version`: currently `1`.
- `session_id`: stable for one server process.
- `epoch_id`: stable for one epoch.
- `server_tick`: the logical Minecraft server tick.
- `event_seq`: a process-wide sequence assigned on the server main thread.

Inbound packets additionally carry a network-thread `arrival_seq` and
`arrival_monotonic_ns`. These fields are diagnostic only. `apply_tick` and
`apply_seq`, stamped immediately before authoritative packet application on the
server thread, determine the training transition and multiplayer order.

## Epoch layout

```text
source/<session-id>/<epoch-id>/
  manifest.json
  events.jsonl
  states.jsonl
  coverage.jsonl
  replays/
    <player-uuid>.<format>
```

Writers first create an active epoch outside the published namespace. The
epoch becomes visible only after all streams reach the same final barrier,
flush successfully, and the manifest contains their sizes and SHA-256 hashes.
An interrupted active epoch may be recovered, but it is never silently treated
as complete.

## Events

`events.jsonl` contains serverbound arrival/application records and lifecycle
events. A gameplay packet may have two records joined by `packet_seq`:

```json
{"type":"packet_arrival","packet_seq":42,"player_id":"...","packet_type":"...","arrival_seq":91,"arrival_monotonic_ns":1234}
{"type":"packet_apply","packet_seq":42,"player_id":"...","apply_tick":100,"apply_seq":883,"action":{"kind":"move","forward":true}}
```

Multiple discrete actions in one tick remain ordered events; they are not
collapsed into one lossy categorical label. Persistent controls are carried
forward by the dataset converter.

## Player state

`states.jsonl` contains one authoritative post-tick snapshot per connected
player. It includes dimension, transform, velocity, pose/control flags,
health/food/air/experience, game mode, selected slot, inventory, effects,
vehicle relationship, and a state barrier sequence.

The canonical transition is:

```text
post_state[t] + actions with barrier[t] < apply_seq <= barrier[t+1]
    -> post_state[t+1]
```

## World and visual coverage

`coverage.jsonl` describes which dimensions/chunks and replay ticks are
available without forcing chunk generation. Dataset crops are chosen later.
An exporter emits a coverage mask whenever the requested crop extends beyond
captured data.

For v1, ServerReplay/Flashback is the canonical client-visible chunk packet
journal used for voxel reconstruction and rendering. The source schema leaves
room for an authoritative chunk-checkpoint/delta stream without changing the
training sample identity.

