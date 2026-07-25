# Source Record V1

Source Record V1 is the server-side intermediate capture contract for
Minecraft 1.21.8. The recorder writes one combined JSONL event stream while
ServerReplay independently rotates Flashback archives. Minecraft server ticks
and recorded timeline payloads are their shared clock.

## Intermediate layout

```text
<intermediate-root>/sessions/<session-id>/
  manifest.json
  session_end.json
  epochs/
    epoch-000000/
      events.jsonl
      manifest.json

<intermediate-root>/replays/          # ServerReplay working files
```

An active epoch uses `events.jsonl.inprogress`. At an end-of-tick rotation,
the recorder syncs and renames the stream, then writes a sealed manifest with
session/epoch identity, tick and sequence bounds, record and byte counts,
record-type counts, SHA-256, close time, rotation reason, and forced-seal flag.
Post-processors read only epochs whose declared count, size, and hash verify.

These are private intermediates. At player join, the recorder separately
creates the canonical Artifacts V1 play directory described in
[Artifacts V1](artifacts-v1.md).

## Common envelope

Every `events.jsonl` line contains:

| Field | Meaning |
| --- | --- |
| `schema_version` | `1`. |
| `record_type` | Event kind. |
| `session_id` | One server-process recording session. |
| `epoch_index` | Containing intermediate epoch. |
| `server_tick` | Logical server tick. Between-tick events belong to the upcoming tick. |
| `sequence` | Strictly increasing session-global event order. |
| `recorded_at_ns` | Process-monotonic diagnostic timestamp. |
| `recorded_at_unix_ms` | Wall-clock diagnostic timestamp. |

Player-scoped records include player UUID/name and entity ID. After join they
also include the random per-join `connection_id` and connection start tick. The
recorder always records every connected player.

## Records

The stream contains `session_start`, `session_end`, `tick_start`, `tick_end`,
`player_join`, `player_leave`, `packet_arrival`, `packet_apply`,
`player_state`, `control_state`, and `replay_timeline`.

`packet_arrival` is diagnostic network-thread observation. It can diagnose
queueing, latency, or a missing arrival/apply match, but it is not authoritative
gameplay order and is not copied to `actions.jsonl`.

`packet_apply` is stamped on the main server thread immediately before the
handler body. Its `apply_sequence` is the authoritative serverbound action
order. It carries normalized semantic packet fields, not raw bytes. Chat,
commands, and custom-payload contents are redacted.

At each tick end, `player_state` records dimension, transform, velocity, pose,
movement flags, health/food/air/experience, game mode, abilities, effects,
vehicle/passenger references, selected slot, and non-empty inventory stacks.
`state_barrier_apply_sequence` identifies the last applied action included in
that post-tick state.

`control_state` reconstructs the latest server-visible persistent movement
flags, camera yaw/pitch and deltas, and selected slot at 20 Hz. It is useful as
a semantic action stream but is not physical keyboard or raw mouse telemetry.

For consecutive snapshots, the transition boundary is:

```text
post_state[t]
  + control_state[t+1]
  + packet_apply where state_barrier[t] < apply_sequence <= state_barrier[t+1]
  -> post_state[t+1]
```

## Replay identity and alignment

Each replay recorder embeds `mc_recorder` into Flashback `metadata.json` with
session ID, segment UUID, per-player segment ordinal, player UUID, connection
UUID when bound, and capture-contract markers.

On ServerReplay's post-save event, the recorder copies the exact completed ZIP
into the connection play's `replays/` directory. It never merges segments or
removes the working copy. A copy failure marks recording failed.

Once per player tick, `mc_recorder:timeline/v1` records session ID, connection
ID, global server tick, and matching event sequence inside Flashback. Render and
scene processors use those values rather than filenames or modification times.

## World coverage and gaps

World capture is the moving client-visible corridor, not omniscient server
state. Each player state declares best-effort center/view-distance coverage and
`complete=false`; unloaded cells must remain unknown.

The scene extractor reconstructs block sections, entities, block entities, and
authoritative player state. It currently does not persist exact light arrays,
particles, or audio. Block-derived lighting is only an approximation. Minecraft
may omit unopened container inventory from client packets, so missing contents
must not be interpreted as an empty chest.
