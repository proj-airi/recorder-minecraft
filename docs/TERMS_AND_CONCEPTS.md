# Terms and Concepts

The normative persisted contracts are [Artifacts V1](specs/artifacts-v1.md)
and [Primitive Capture V1](specs/capture-v1.md).

## System model

The system has two independent stages joined by ordinary files:

1. The recorder creates one primitive capture for one player connection.
2. Explicit file-to-file processors may later derive actions, scenes, and
   renders from that completed capture.

There is no publication service between the stages. Operators and external
systems may copy a completed play with normal filesystem tools such as SSH or
rsync.

| Domain | Owns | Does not own |
| --- | --- | --- |
| Minecraft server | Server ticks, player entities, connection lifecycle, and authoritative game state | Post-processing outputs or worker coordination |
| Recorder mod | Artifacts V1 hierarchy, play metadata, connection-local event stream, replay association, and completion marker | Action extraction, Scene Store V2, renders, downloads, or datasets |
| ServerReplay | Live Flashback writer and final Flashback ZIP bytes | Recorder events, play layout, or derived outputs |
| Action processor | Reconstruction of a semantic action stream from one completed capture | Capture discovery, hierarchy creation, or replay mutation |
| Scene processor | Headless replay reduction, private Scene Store V1 staging, and durable Scene Store V2 output | GUI rendering or omniscient server-world recovery |
| Renderer | Flashback playback and optional first-person PNG generation | Scene extraction, artifact discovery, or capture mutation |
| Operator or external orchestrator | Copying plays, choosing inputs and outputs, scheduling processors, and assembling later datasets | Recorder-internal lifecycle state |

## Canonical artifact hierarchy

```text
artifacts/v1/
  <server-name>--<server-instance-uuid>/
    world/                                      # reserved for future
    players/
      <player-name>--<player-uuid>/
        plays/
          <started-at-utc>--<connection-uuid>/
            metadata.json
            capture/
              events.jsonl
              replay.zip
            actions.jsonl                       # optional
            scene.sqlite3                       # optional
            renders/                            # optional
              render-job.json
              result.json
              fpv_frames/
                frames.jsonl
                frame_*.png
```

The hierarchy is part of the recorder contract. A processor receives exact
input and output paths and does not discover, construct, or interpret this
hierarchy on the caller's behalf.

### Layout terms

| Term | Definition |
| --- | --- |
| Artifacts root | Durable root configured by `paths.artifacts`; Artifacts V1 lives under its `v1/` child |
| Server instance | One stable recorder-server identity represented by an editable display name plus a generated UUID |
| Player directory | Display name plus stable Minecraft player UUID; the name is descriptive, while the UUID is identity |
| Play | One join-to-disconnect player connection and all primitive or derived files associated with it |
| Capture directory | Recorder-owned primitive inputs inside one play |
| Derived output | Optional processor-owned result placed at an explicit caller-selected path, conventionally inside the play |
| Runtime root | Private scratch and lock root configured by `paths.runtime`; it is not part of Artifacts V1 |
| World directory | Reserved server-instance-wide location for possible future world saves, seeds, or related inputs; absent and unused in V1 |

## Identities and ordering

| Identity or coordinate | Scope | Meaning |
| --- | --- | --- |
| `server.name` | Human-facing server label | Configurable display name; defaults to the machine hostname when omitted |
| `server.instance_id` | Recorder server instance | UUID generated once by `minerec init` and reused across restarts |
| `session_id` | One recorder process run | In-memory run identity embedded in metadata, events, and replay metadata; it does not create a session directory |
| `player_uuid` | Minecraft player | Stable player identity across reconnects and display-name changes |
| `connection_id` | One play | UUID generated for every join-to-disconnect interval |
| `replay_id` | One replay archive | UUID embedded in `arcade_replay_meta.json` and bound to the player and connection |
| `server_tick` | Server timeline | Logical tick used to align state, actions, and replay markers |
| `sequence` | One `events.jsonl` stream | Strictly increasing total order for records belonging to the connection |
| `apply_sequence` | Applied serverbound packets | Main-thread gameplay application order used by action reconstruction and state barriers |
| Replay tick | Flashback timeline | Replay-local tick aligned to a server tick through `mc_recorder:timeline/v1` |

Display names, directory modification times, replay filenames, and process
timestamps are not substitutes for UUID identity.

## Recorder lifecycle

### Open play

At player join, the recorder allocates a connection UUID, creates the canonical
play directory, writes `metadata.json` with null end fields, opens
`capture/events.jsonl`, and associates the player's ServerReplay writer with
that play.

ServerReplay writes to one timestamped working child under `capture/replay/`.
That child is an implementation detail of the live writer, not a replay
segment or durable artifact.

### Completed play

On disconnect, the recorder closes and flushes the event writer and asks
ServerReplay to stop. After ServerReplay saves and closes its archive, the
recorder moves those exact ZIP bytes to `capture/replay.zip`, removes the empty
working directory, and atomically rewrites `metadata.json` with `ended_at`,
`end_server_tick`, and `terminal_reason`.

The non-null metadata end tick is the readiness marker and is written last.
It means the recorder-owned inputs are closed and available to processors. It
does not mean the directory is immutable, cryptographically sealed, published,
downloaded, or fully post-processed.

### Incomplete play

If either primitive writer fails or the recorder cannot finish the handoff,
the metadata end tick remains null. Processors reject that play. Recovery and
download policies are outside the V1 recorder contract.

## Primitive capture terms

| Term | Definition |
| --- | --- |
| Primitive capture | `metadata.json`, `capture/events.jsonl`, and `capture/replay.zip` for one completed play |
| Capture metadata | Facts occurring once: server, session, player, connection, tick range, terminal reason, canonical filenames, capture contract, and known gaps |
| Event stream | Single connection-local, newline-delimited JSON stream written by the recorder |
| Replay archive | One unrotated Flashback ZIP containing the client-visible packet timeline and embedded recorder identity |
| Capture contract | Declared interpretation of the replay and event stream, currently `client_visible_scene_v1` for Flashback scene data |
| Known gap | A modality or state the current capture cannot guarantee and must not silently fabricate |

### Event record types

| Record | Authority and use |
| --- | --- |
| `packet_arrival` | Diagnostic network-thread observation for latency and arrival/apply correlation; not authoritative gameplay order |
| `packet_apply` | Main-thread observation immediately before a serverbound packet handler; authoritative ordering input for semantic action reconstruction |
| `player_state` | Authoritative post-tick state for the recorded player, including transform, health, inventory, effects, abilities, and state barrier |
| `control_state` | 20 Hz reconstruction of persistent movement flags, camera rotation and deltas, sprint/sneak state, and selected slot |
| `replay_timeline` | Connection identity and server tick also sent into Flashback as `mc_recorder:timeline/v1` |

An action record is normalized semantic data. It is not a raw network byte
stream, physical keyboard event, or raw mouse sample. Chat, command, and custom
payload contents are redacted where required by the capture contract.

The state barrier means that `player_state[t]` includes all applied packets up
to its `state_barrier_apply_sequence`. It lets a processor place ordered packet
actions between adjacent authoritative states without relying on arrival time.

## Processor terms

| Term | Definition |
| --- | --- |
| Processor | Explicit command that validates named input files and writes one named output |
| Actions extraction | Transformation from metadata plus events into `actions.jsonl` |
| Scene extraction | Transformation from metadata, events, and replay into `scene.sqlite3` |
| Rendering | Transformation from metadata, events, and replay into a `renders/` directory |
| Tick selection | Optional inclusive `--from-tick` and `--to-tick` interval applied by a processor |
| Prepared job | Private scene or render job created under the runtime root for validation or execution |
| Owned output | Existing result that passes the processor's identity/format checks and may therefore be replaced with `--force` |

Processors do not scan `artifacts/v1`, infer a replay from a player, create a
play, download remote files, or mutate `metadata.json` and `capture/`.
Independent workers can process copied plays because all required durable
inputs are contained in the play itself.

## Action terms

| Term | Definition |
| --- | --- |
| Action stream | `actions.jsonl`, ordered by the selected server-tick interval |
| Persistent control | Reconstructed held movement state such as forward, jump, sneak, or sprint |
| Discrete action | Normalized applied packet action such as interaction, inventory selection, or other server-visible transition |
| Protocol acknowledgement | Packet retained for protocol/accounting semantics but not presented as a physical player input |

`actions.jsonl` is derived and replaceable. The recorder never writes it.

## Scene terms

| Term | Definition |
| --- | --- |
| Scene Store V2 | Durable `scene.sqlite3` output and the only public scene-store format produced by the processor |
| Scene Store V1 | Private extractor spool/intermediate under the runtime root; not an Artifacts V1 output or compatibility format |
| Scene frame | Random-access client-visible world snapshot aligned to one selected server tick |
| Player state row | Exactly one typed subject-state row linked to every scene frame |
| Section version | Time-bounded version of a client-visible chunk section |
| Entity version | Time-bounded instance of a client-visible entity with an explicit application-assigned ID |
| Block-entity version | Time-bounded client-visible block-entity payload at a world position |
| Content-addressed blob | Canonical binary payload stored once and referenced by digest, including complete private player inventory/effects/abilities data |
| Unknown cell | World location for which the replay provides no client-visible state at the requested tick |

Scene Store V2 uses SQLite as the portable file format. Its logical base schema
is defined with SQLAlchemy Core and avoids dependence on SQLite `rowid`, so a
future PostgreSQL adapter can preserve the model. SQLite R-tree indexes,
triggers, PRAGMAs, immutable reads, and atomic replacement remain adapter
details.

The scene is reconstructed from what the recorded client could see. An unknown
cell is not air, and missing unopened-container contents are not an empty
inventory.

## Render terms

| Term | Definition |
| --- | --- |
| Render output | Optional processor-owned `renders/` directory |
| Render job | `render-job.json`, binding explicit capture inputs, identity, replay digest, selected ticks, resolution, FPS, and presentation options |
| Render result | `result.json`, written only for a completed renderer invocation |
| FPV frame | First-person PNG reconstructed by playing the Flashback archive in the client renderer |
| Frame index | `fpv_frames/frames.jsonl`, mapping every PNG to server tick, replay tick, player, connection, and replay identity |
| GUI presentation | First-person hand/item and Minecraft HUD presentation; enabled by default |
| `no_gui` | Explicit request to omit the client HUD; it does not make the renderer headless |
| Replay coverage | Tick interval for which matching timeline markers actually exist in the replay |

Rendering is optional. A missing `renders/` directory means not rendered, not a
failed or incomplete primitive capture. The current output is PNG frames, not
MP4 and not pixels captured from the original player's computer.

When requested ticks and replay coverage differ, the renderer reports and uses
their explicit intersection. It does not interpolate absent ticks or compress
gaps invisibly.

## Coverage and modality gaps

`client_visible_best_effort` describes replay world coverage. It is not a claim
of complete server-world state. Unloaded chunks, entities outside tracking
range, and information Minecraft never sent to the client remain unknown.

Current Scene Store V2 does not persist exact lighting, particles, or audio.
These limitations are declared in capture metadata and scene provenance. A
future processor may add derived modalities without changing recorder
ownership of the primitive capture.

## Obsolete terms and components

The following concepts belong to the removed architecture and must not be used
to describe Artifacts V1:

- Recorder epochs, epoch rotation, epoch manifests, and seal files.
- Session directories as persisted artifact containers.
- Independently rotated replay segments or segment ordinals.
- Dataset V1/V2 exports, modality attachment manifests, and dataset viewers.
- `.mcplay`, bundle ZIPs, bundle IDs, bundle publishers, importers, and
  in-place bundle rendering.
- Dashboard services, browser viewers, render queues, RabbitMQ dispatch, RPC
  workers, leases, and render attachment publication.
- Recorder-side download, post-processing, or dataset assembly.

`session_id` remains a valid in-memory recorder-run identity. `Scene Store V1`
also remains valid only as a private implementation intermediate. Neither term
restores the removed persisted layout or compatibility paths.
