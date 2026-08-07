# Primitive Capture V1

Primitive Capture V1 is the recorder-owned input to every later processor. One
player connection produces one top-level ProtoJSON metadata message, one
connection-local ProtoJSON-lines stream, and one unrotated Flashback archive.

The producing Minecraft server may be dedicated or integrated into a game
client. Both deployments use authoritative server ticks and produce this same
contract. Joining a remote server without hosting an integrated server does not
produce a local primitive capture.

## Files

```text
<play>/
  metadata.json
  capture/
    events.jsonl
    replay.zip
```

There are no sessions directories, epochs, rotations, per-epoch manifests,
seal files, replay segments, or recorder-side post-processing intermediates.

`metadata.json` is the ProtoJSON form of generated `ServerMetadata` and owns facts that occur once: server and player identity,
connection UUID, start time/tick, end time/tick, terminal reason, capture file
names, capture contract, and known gaps. While recording, its optional end fields are absent.

`capture/events.jsonl` is opened at connection start and appended through one
bounded, buffered writer. A clean disconnect drains the queue, flushes, and
fsyncs it. Every line is the ProtoJSON form of one generated `CaptureEvent`.
The file is never renamed or rotated.

ServerReplay streams Flashback data to a timestamped child of
`capture/replay/` while the connection is live. After ServerReplay closes that
writer, the recorder moves its completed ZIP unchanged to
`capture/replay.zip` and removes the empty working parent. Rotation is
disabled. The archive embeds `mc_recorder` schema 4 identity in
`arcade_replay_meta.json`, with `replay_id`, player UUID, connection UUID,
timeline/capture contracts, and the capture-relative path.

Only after the event stream and replay have both closed does the recorder
atomically rewrite `metadata.json` with the end fields.
That rewrite is the readiness marker, not a publication or cryptographic seal.
If recording stops partway through, the optional end tick remains absent; processors
reject the play as incomplete. Operators can copy complete plays with ordinary
SSH or rsync. No downloader is part of this contract.

## Event envelope

Every JSONL `CaptureEvent` contains an `EventIdentity` plus exactly one
generated oneof record:

| Field | Meaning |
| --- | --- |
| `schema_version` | `1`. |
| `record` | Oneof event kind. |
| `session_id` | In-memory recorder-process identity. |
| `server_tick` | Logical server tick; between-tick events use the upcoming tick. |
| `sequence` | Strictly increasing order within this connection stream. |
| `recorded_at_ns` | Process-monotonic diagnostic timestamp. |
| `recorded_at_unix_ms` | Wall-clock diagnostic timestamp. |
| `player_uuid` | Canonical player UUID. |
| `connection_id` | Canonical connection UUID. |

The stream contains `packet_arrival`, `packet_apply`, `player_state`,
`control_state`, and `replay_timeline`. Join, leave, start, and end facts live in
metadata rather than one-off event records.

`packet_arrival` is a diagnostic network-thread observation. It is useful for
queue latency and arrival/apply matching, but does not define authoritative
gameplay order. `packet_apply` is stamped on the main thread before the packet
handler and its `apply_sequence` defines serverbound action order. The payload
is normalized semantic data, not raw bytes; chat, commands, and custom payload
contents are redacted.

`player_state` is the authoritative post-tick player snapshot: dimension,
transform, velocity, pose, movement flags, health/food/air/experience, game
mode, abilities, effects, references, selected slot, and inventory. Its
`state_barrier_apply_sequence` is the last applied action included in the
snapshot.

`control_state` is a 20 Hz reconstruction of the latest server-visible
persistent movement flags, camera rotation/deltas, and selected slot. It is not
physical keyboard or raw mouse telemetry. `replay_timeline` is also written
inside Flashback as `mc_recorder:timeline/v1`, aligning server ticks to replay
ticks without filenames or file timestamps.

For adjacent snapshots:

```text
post_state[t]
  + control_state[t+1]
  + packet_apply where state_barrier[t] < apply_sequence <= state_barrier[t+1]
  -> post_state[t+1]
```

## Coverage limits

The replay reconstructs the moving client-visible world, not omniscient server
state. Unloaded cells remain unknown. Minecraft may not send unopened
container contents, so missing chest inventory is unknown rather than empty.
Exact light arrays, particles, and audio are not currently extracted. These
gaps stay explicit in metadata and Scene Store V2 provenance.
