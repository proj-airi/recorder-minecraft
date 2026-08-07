# Artifacts V1 Pipeline

Artifacts V1 is the shared, filesystem-native pipeline. The recorder and later
processors operate on the same play directory, but have separate ownership.
It is not a ZIP bundle, publisher, importer, dataset format, or custom file
extension.

The Recorder mod may run with a dedicated server or with an integrated server
hosted by a game client. Server deployment does not change the hierarchy,
authority, metadata, event stream, or processor inputs. A client connected only
to a remote server does not create local artifacts.

## Canonical hierarchy

```text
artifacts/v1/
  <server-name>--<server-instance-uuid>/
    world/                                      # reserved; optional
    players/
      <player-name>--<player-uuid>/
        plays/
          <started-at-utc>--<connection-uuid>/
            metadata.json                       # recorder ProtoJSON
            capture/                            # recorder
              events.jsonl
              replay.zip
            actions.jsonl                       # optional post-process result
            scene.sqlite3                       # optional post-process result
            renders/                            # optional render result
              render-job.json
              result.json
              fpv_frames/
                frames.jsonl
                frame_*.png
```

Every player connection has exactly one play directory. Names are non-empty
NFC Unicode without separators or control characters. UUIDs use canonical
lowercase spelling. Start time uses `YYYYMMDDTHHMMSS[.fraction]Z`. No alternate
nesting is valid.

The instance UUID is generated once when recorder configuration is initialized
and reused across restarts. The server name is an editable display label. An
integrated-server recording profile retains the same instance identity when it
opens different save worlds. A new connection UUID is generated for every
join. Multiple players and overlapping connections create independent plays.

`world/` is reserved for future server-instance-wide inputs such as a world
save or seed. It is not required in V1 and processors must not infer it.

## Stage 1: recorder

At player join on either server deployment, the recorder creates the play and
starts writing only:

```text
metadata.json
capture/events.jsonl
capture/replay.zip
```

The generated ProtoJSON-lines stream is single, buffered, and connection-local. ServerReplay uses
a timestamped working child under `capture/replay/`; after it closes, the
recorder moves the archive unchanged to `capture/replay.zip` and removes the
empty working parent. The recorder writes the metadata end tick last. There is
no explicit seal step. A non-null end tick is the handoff marker for
post-processing.

See [Primitive Capture V1](capture-v1.md) for the persisted fields and gaps.

## Stage 2: post-processing and rendering

Processors are explicit file-to-file tools. They do not discover a play,
construct the hierarchy, download data, or mutate recorder-owned inputs. The
caller passes exact inputs and chooses an exact output, conventionally in the
same play:

```sh
PLAY='artifacts/v1/<server>--<instance>/players/<player>--<uuid>/plays/<start>--<connection>'

go run ./cmd/recorder-minecraft actions extract \
  --metadata "$PLAY/metadata.json" \
  --events "$PLAY/capture/events.jsonl" \
  --output "$PLAY/actions.jsonl"

go run ./cmd/recorder-minecraft scene extract \
  --metadata "$PLAY/metadata.json" \
  --events "$PLAY/capture/events.jsonl" \
  --replay "$PLAY/capture/replay.zip" \
  --output "$PLAY/scene.sqlite3"

go run ./cmd/recorder-minecraft render \
  --metadata "$PLAY/metadata.json" \
  --events "$PLAY/capture/events.jsonl" \
  --replay "$PLAY/capture/replay.zip" \
  --output "$PLAY/renders"
```

All commands validate that metadata has an end tick and that the event/replay
identities match it. `--from-tick` and `--to-tick` select a bounded interval.
`--prepare-only` leaves a scene/render job for inspection. `--overwrite` replaces
only an output already recognized as owned by that processor.

Processor scratch, locks, subject-pose streams, player-state staging, and the
private Scene Store V1 spool live under `.recorder/minecraft/runtime/`. They are not
part of Artifacts V1. Durable results alone are written to the explicit output.

Independent workers can copy complete plays with SSH/rsync, process them, and
copy back only `actions.jsonl`, `scene.sqlite3`, or `renders/`. Coordination and
dataset assembly are deliberately outside V1.

## Derived outputs

`actions.jsonl` contains generated `PlayerAction` ProtoJSON records reconstructed from authoritative
`packet_apply` records and 20 Hz `control_state`. It excludes diagnostic
`packet_arrival`, physical keyboard events, raw mouse samples, and raw bytes.

`renders/render-job.json` and `renders/result.json` are generated `RenderJob`
and `RenderResult` ProtoJSON messages. `renders/fpv_frames/` contains PNG frames
plus generated `RenderFrameIndex` ProtoJSON lines mapping every frame to server
tick, replay tick, player/connection identity, and replay ID. The terminal
result binds the frame-index digest, byte size, frame count, replay integrity,
identity, requested/actual tick ranges, resolution, and frame rate. Renders are
optional; absence means not rendered.

`renders/fpv.mp4` is the optional H.264/YUV420p playback derivative composed
from a complete `fpv_frames/` result. The render command publishes it atomically
after `ffmpeg` succeeds; `--frames-only` retains the image-sequence-only
workflow. The video is derived and may be regenerated without mutating the
capture inputs.

## Read API and media serving

`recorder-minecraft serve` exposes the read-only Artifacts V1 catalog over gRPC
and a grpc-gateway HTTP API. Catalog traversal validates the server, player, and
play directory identities against their metadata before returning them.
`/assets/` serves only regular files contained below the configured artifacts
root and supports HTTP byte ranges so browser decoders can seek in MP4 files.
Symlinks and path traversal outside that root are rejected.

`scene.sqlite3` is Scene Store V2. It requires exact frame/player-state tick
coverage and contains typed player state plus the full inventory/effect/ability
payload in compressed content-addressed blobs. Its logical base tables are
`schema_info`, `scene_meta`, `blobs`, `frames`, `player_states`,
`section_versions`, `entity_versions`, and `block_entity_versions`. Explicit
application-assigned `BIGINT` version IDs avoid SQLite `rowid` dependence.
SQLite-only R-tree tables, triggers, PRAGMAs, immutable reads, and atomic
replacement stay in the SQLite adapter so a future storage adapter can preserve
the logical model.

Scene data has the same client-visible limits as the replay. Unknown cells and
unopened-container contents remain unknown; they are never fabricated as air
or empty inventories.
