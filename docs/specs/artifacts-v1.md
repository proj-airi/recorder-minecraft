# Artifacts V1

Artifacts V1 is a staged, filesystem-native recording layout. It is not a
bundle or publication format. No wrapper directory, content ID, custom file
extension, archive, publisher, importer, or compatibility reader exists.

## Canonical path

Every player connection has exactly one play directory:

```text
artifacts/v1/
  <server-name>--<server-instance-uuid>/
    players/
      <player-name>--<player-uuid>/
        plays/
          <started-at-utc>--<connection-uuid>/
```

Names are non-empty NFC Unicode without path separators or control characters.
UUIDs use canonical lowercase spelling. `<started-at-utc>` uses
`YYYYMMDDTHHMMSS[.fraction]Z`. Alternate nesting is invalid.

`server-instance-uuid` is generated once by `minerec init` and remains stable
when the editable display name changes. A new `connection-uuid` is generated
for every join, including reconnects by the same player.

## Recorder stage

The server recorder creates only:

```text
<play>/
  metadata.json
  replays/
    000000--<segment-uuid>.zip
    000001--<segment-uuid>.zip
```

`metadata.json` identifies the server, session, player, connection, start/end
ticks and times, terminal status, Flashback capture contract, known modality
gaps, and ordered replay filenames. It is ordinary recorder metadata, not an
integrity manifest.

ServerReplay owns and rotates each Flashback ZIP. On its post-save event the
recorder copies the exact completed file into the connection's `replays/`
directory. It does not merge segments and does not remove the ServerReplay
working copy. A replay copy failure is fatal to that recording session; the
play is marked failed instead of silently continuing with an incomplete tree.

Raw sidecar epochs and ServerReplay working files live under the configured
intermediate root, outside `artifacts/`.

## Post-processing stages

Processors take explicit input paths and one explicit output path. They do not
discover, construct, publish, import, or mutate the Artifacts V1 hierarchy.
The caller chooses these conventional outputs:

```text
<play>/
  metadata.json
  replays/
  actions.jsonl             # after action extraction
  scene.sqlite3             # after scene extraction
  renders/                  # optional, after rendering
    render-job.json
    result.json
    fpv_frames/
      frame_*.png
      frames.jsonl
```

Commands:

```sh
pixi run minerec actions extract INTERMEDIATE_SESSION \
  --player PLAYER_UUID --connection CONNECTION_UUID \
  --output PLAY/actions.jsonl

pixi run minerec scene extract INTERMEDIATE_SESSION \
  --player PLAYER_UUID --connection CONNECTION_UUID \
  --replay PLAY/replays/000000--SEGMENT.zip \
  --output PLAY/scene.sqlite3

pixi run minerec render INTERMEDIATE_SESSION \
  --player PLAYER_UUID --connection CONNECTION_UUID \
  --replay PLAY/replays/000000--SEGMENT.zip \
  --output PLAY/renders
```

Repeat `--replay` for scene extraction when multiple replay segments
contribute. FPV rendering currently processes one segment per invocation.

## Scene Store V2

`scene.sqlite3` is the public scene result. Scene Store V1 exists only as a
private extraction spool and is deleted with the job workspace.

The vendor-neutral SQLAlchemy Core schema contains `schema_info`, `scene_meta`,
`blobs`, `frames`, `player_states`, `section_versions`, `entity_versions`, and
`block_entity_versions`. Entity and block-entity versions use explicit
application-assigned `BIGINT` IDs. SQLite-specific R-tree tables, triggers,
PRAGMAs, immutable reads, and atomic replacement remain in the SQLite adapter;
a future PostgreSQL adapter can use the same logical tables.

There is exactly one `player_states` row per scene frame tick. It links to the
active player entity version. Common state is typed; the complete canonical
inventory/effects/abilities state is retained in a compressed,
content-addressed blob. Missing state or incomplete reconstructed world
coverage rejects the output.

Scene data is client-visible, not omniscient server state. Unloaded cells stay
unknown. An unopened container may not expose its inventory in client packets;
missing inventory data must not be interpreted as an empty chest. Exact light
arrays, particles, and audio are not persisted in Scene Store V2. Block-derived
lighting can only be an approximation, not a reconstruction of observed light.

## Actions

`actions.jsonl` contains the selected connection's reconstructed semantic
action stream from authoritative packet application and 20 Hz reconstructed
control state. It does not contain physical keyboard events, raw mouse samples,
or raw packet bytes. `packet_arrival` remains intermediate diagnostic data; it
is useful for latency/order diagnosis but is not an exported action because
main-thread `packet_apply` is authoritative.
