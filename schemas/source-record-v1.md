# Source record v1

Source record v1 is the loss-minimizing, server-side capture contract for
Minecraft 1.21.8. The recorder writes one combined JSONL event stream and
ServerReplay writes independent Flashback archives. Both use the Minecraft
server tick as their common clock.

## Session and epoch layout

```text
artifacts/captures/<session-id>/
  manifest.json
  session_end.json                    # written on close; status may be incomplete
  epochs/
    epoch-000000/
      events.jsonl                    # present only after sealing
      manifest.json

artifacts/replays/
  players/<player-uuid>/...           # independent ServerReplay archives
  chunks/...                          # only for explicitly configured fixed areas
```

The default sidecar epoch is 6,000 ticks, nominally five minutes at 20 Hz.
While it is active, its stream is named `events.jsonl.inprogress`. Sealing
flushes and syncs the stream, atomically publishes it as `events.jsonl`, and
then publishes a manifest containing:

- `session_id` and `epoch_index`;
- `sealed: true`;
- record and byte counts;
- first/last server tick and global sequence;
- per-record-type counts;
- SHA-256 of `events.jsonl`; and
- `sealed_at`, `rotation_reason`, and `forced_seal` boundary metadata.

Epoch indices are session-local, contiguous, and monotonically increasing.
Automatic rotation occurs after the configured number of complete ticks. A
manual rotation is processed only after `tick_end`, seals all records through
that tick, and starts the automatic interval again with the next tick. If a
manual request arrives at an already-due automatic boundary, one
`manual_and_automatic` rotation satisfies both. Manual rotation never changes
the session ID or ServerReplay's independent archive schedule.

Readers, exporters, and retention code accept an epoch as immutable only when
the final file and all three integrity checks (record count, byte count, and
SHA-256) match its manifest. Active or incomplete epochs are never silently
promoted.

V1 leaves fixed-area chunk recorders disabled: the default source is the moving
client-visible corridor inside each player's archive. ServerReplay archives
rotate on their own five-minute schedule. A sidecar
epoch and a replay archive are deliberately **not** assumed to be one-to-one or
to start on the same tick. They are retained and selected independently, then
aligned from timeline markers recorded inside the replay.

## Common event envelope

Every line in `events.jsonl` is one JSON object with these fields:

| Field | Meaning |
| --- | --- |
| `schema_version` | `1` for this contract. |
| `record_type` | The event kind. |
| `session_id` | Stable generated identifier for one server-process capture session. |
| `epoch_index` | Sidecar epoch containing the event. |
| `server_tick` | Logical server tick; events between ticks belong to the upcoming tick. |
| `sequence` | Strictly increasing, session-global event order assigned by the recorder. |
| `recorded_at_ns` | Process-monotonic diagnostic timestamp; not a cross-process join key. |
| `recorded_at_unix_ms` | Wall-clock diagnostic timestamp; not a training-order key. |

Player-scoped records also carry `player_uuid`, `player_name`, and `entity_id`.
After join they carry `connection_id` and `connection_start_server_tick`. A new
random `connection_id` is allocated for every join, so reconnects by the same
player remain distinct. V1 always records every connected player; end-of-tick
players are emitted in UUID order for deterministic multiplayer capture.

The session manifest declares the exact loaded mods, capture scope, tick/apply
phase, privacy policy, and record types. `session_end.json` distinguishes clean
and incomplete shutdowns.

## Runtime control and connection ledger

When `control_root` is configured, the recorder atomically maintains these
runtime-only files for the host dashboard:

```text
<control_root>/
  status.json
  connections.json
  replay-segments.json
  sessions/<session-id>.connections.json
  sessions/<session-id>.replay-segments.json
  requests/<request-uuid>.json
  responses/<request-uuid>.json
```

`status.json` is refreshed at most once per second while ticks are running. It
reports the active session/tick/sequence/epoch, writer queue and failure state,
last writer progress, storage availability, and active connections.
`connections.json` is bounded to the current session, while the same atomic
snapshot is retained under `sessions/` so completed rows survive later server
starts. Each row contains player UUID/name, a unique `connection_id`, join
tick/sequence, and, after termination, end tick/sequence and `terminal_reason`
(`disconnect` or `server_shutdown`). A clean server shutdown queues terminal
source events before the final session seal, then publishes terminal ledger
fields only after that seal succeeds. If sealing fails, the ledger stays
unterminated so the stale heartbeat is classified as interrupted rather than
cleanly disconnected.

A seal request uses control schema v1 and operation `seal_connection`, with a
UUID request ID, `expected_session_id`, exact player UUID/connection ID, and the
ledger's `connection_end_sequence`. Active, unknown, stale-session, or
mismatched requests receive atomic failure responses. Valid requests are
coalesced at the next end-of-tick boundary, or reuse an already-published seal
that covers the end sequence. A success response names the epoch, its terminal
sequence, manifest, and event hash and is written only after the epoch stream is
fsynced and its integrity manifest exists. Existing responses make request IDs
idempotent.

`replay-segments.json` binds ServerReplay's independently rotated player
archives to capture identity. The recorder allocates a UUID `segment_id` and a
monotonic per-session/player `segment_ordinal` at each ServerReplay recorder
start. Because ServerReplay starts during login, before Fabric publishes the
player join, the recorder retains recorder-object identity and fills the exact
`connection_id` when the connection ledger starts. Reconnects therefore cannot
claim a still-saving archive from an earlier connection. Each segment row
contains player identity, optional connection join/end boundaries, replay
format, `recording` or `saved` state, timestamps, and host-local source/output
locations. A saved row also includes the observed output size; host tooling
must still enforce replay-root containment and compute a stable SHA-256 before
using the archive.

Every saved replay embeds an `mc_recorder` metadata object in ServerReplay's
`arcade_replay_meta.json` ZIP entry, with schema version, session ID, segment
ID/ordinal, player UUID, and the connection ID when binding completed. This is
distinct from Flashback's base `metadata.json`. The current and session-history
segment ledgers are atomic runtime indexes, not substitutes for that archive
metadata, timeline markers, or host integrity verification.

These files coordinate the dashboard; they are never source truth. Exporters
must still validate the immutable source events and sealed epoch manifests.

## Packet observation and authoritative order

`packet_arrival` records the server's network-thread observation. It includes
`arrival_sequence`, `tick_phase`, `network_thread`, player/connection identity,
and a normalized `packet` object. `packet_apply` is stamped on the main server
thread immediately before the packet handler body continues. It includes:

- a session-global `apply_sequence`;
- `phase: "main_thread_before_handler_body"`;
- the matching `arrival_sequence` and `arrival_server_tick`, when available;
- the player and connection identity; and
- the normalized `packet` object, enriched with apply-time data when possible.

`sequence` orders every source record. `apply_sequence` orders only
authoritative serverbound applications and is the action/state barrier used by
the converter. Arrival fields are diagnostic and must not replace apply order.

Packet payloads are decoded, server-observed semantic actions, not physical
keyboard events, raw mouse samples, or canonical packet bytes. Normalization
covers movement-control flags, accepted position/rotation, block/entity
interaction, use/swing, inventory, stance, and related gameplay fields. Unknown
packets retain class, type, action kind, and order metadata. Chat, commands, and
custom payload contents are redacted; `raw_bytes_available` is `false`.

## Tick records and canonical transition

The combined stream includes:

- `tick_start` and `tick_end`, each with `apply_sequence_at_barrier`;
- `player_join` and `player_leave`;
- `packet_arrival` and `packet_apply`;
- `player_state`, `control_state`, and `replay_timeline`; and
- `session_start` and `session_end` events.

At the end of each tick the recorder snapshots every connected player.
`player_state` includes dimension, transform, velocity, pose, movement state,
health/food/air/experience, game mode, abilities, effects, vehicle/passenger
references, selected slot, and non-empty inventory stacks. Its
`state_barrier_apply_sequence` is the last applied packet included in that
post-tick state.

`control_state` is a 20 Hz reconstruction of the latest server-observed
persistent controls (`forward`, `backward`, `left`, `right`, `jump`, `sneak`,
and `sprint`), selected slot, accepted yaw/pitch, and their per-tick deltas. It
is useful for behavioral cloning, but it is not the original input-device
telemetry.

For two consecutive snapshots, the canonical transition is:

```text
post_state[t]
  + control_state[t+1]
  + packet_apply where
      state_barrier_apply_sequence[t] < apply_sequence
      <= state_barrier_apply_sequence[t+1]
  -> post_state[t+1]
```

Multiple discrete actions inside that interval remain ordered by `sequence`;
they are not collapsed into a single categorical action.

## Replay alignment and coverage

Once per connected player per tick, the server sends a custom payload using
`mc_recorder:timeline/v1`. ServerReplay captures that payload inside the
player's Flashback archive. Its values are:

- `session_id`;
- `connection_id`;
- global `server_tick`; and
- event sequence, exposed as `marker_event_sequence` by the matching sidecar
  `replay_timeline` record.

The renderer finds the first and last markers for the requested session and
connection and requires a constant exact offset from replay ticks to global
server ticks. Segment-aware jobs use `range_policy: "intersection"` to render
only the overlap between marker coverage and the requested connection range; a
disjoint archive completes with `status: "no_coverage"` and no synthetic
frames. Jobs without `range_policy` retain the legacy single-anchor behavior.
Joins must therefore use both player UUID and connection ID, never filename or
archive mtime. Independent archive rotation does not change the sample
timeline.

Open-world data is intentionally best effort. Each `player_state` contains a
`replay_coverage` hint with `kind: "client_visible_best_effort"`, the player's
center chunk, server view distance, and `complete: false`. It does not force
chunk generation and does not claim that a requested voxel crop is complete.
Missing world data must remain unknown rather than being encoded as air.

Current implementation status: the structured sidecar, replay markers, RGB
renderer, and optional coverage-aware replay-to-voxel crop converter are
implemented. Voxel crops are a local replay-client derivative, not an
authoritative world stream in the sidecar. Block entities are not materialized
in voxel V1; the immutable replay remains their source when client-visible.
