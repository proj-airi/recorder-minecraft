# minerec

`minerec` is the file-oriented post-processing CLI. Its processors require
explicit inputs and outputs and never discover the Artifacts V1 directory.

```sh
pixi run minerec --help
pixi run minerec sessions list
pixi run minerec sessions validate SESSION_ID
```

Action extraction:

```sh
pixi run minerec actions extract SESSION_DIR \
  --player PLAYER_UUID --connection CONNECTION_UUID \
  --output actions.jsonl
```

Scene extraction emits Scene Store V2 directly. The extractor's Scene Store V1
is private staging:

```sh
pixi run build-scene-extractor-mod
pixi run minerec scene extract SESSION_DIR \
  --player PLAYER_UUID --connection CONNECTION_UUID \
  --replay SEGMENT_0.zip --replay SEGMENT_1.zip \
  --output scene.sqlite3
```

FPV rendering writes `render-job.json`, `result.json`, and `fpv_frames/` below
the requested output directory:

```sh
pixi run build-renderer-mod
pixi run minerec render SESSION_DIR \
  --player PLAYER_UUID --connection CONNECTION_UUID \
  --replay SEGMENT.zip --output renders
```

Use `--prepare-only` to inspect a scene or render job without launching the
Minecraft process. Use `--force` only to replace a valid output created by the
same processor.

Configuration has three disjoint roots: `paths.artifacts`,
`paths.intermediate`, and `paths.runtime`. Legacy capture/replay/export roots
are rejected rather than migrated.
