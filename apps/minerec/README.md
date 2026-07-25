# minerec

`minerec` is the explicit file-to-file post-processing CLI. It never discovers
or constructs the Artifacts V1 hierarchy.

```sh
pixi run minerec --help
```

For one completed play:

```sh
pixi run minerec actions extract \
  --metadata PLAY/metadata.json \
  --events PLAY/capture/events.jsonl \
  --output PLAY/actions.jsonl

pixi run build-scene-extractor-mod
pixi run minerec scene extract \
  --metadata PLAY/metadata.json \
  --events PLAY/capture/events.jsonl \
  --replay PLAY/capture/replay.zip \
  --output PLAY/scene.sqlite3

pixi run build-renderer-mod
pixi run minerec render \
  --metadata PLAY/metadata.json \
  --events PLAY/capture/events.jsonl \
  --replay PLAY/capture/replay.zip \
  --output PLAY/renders
```

Scene extraction publishes Scene Store V2; Scene Store V1 and subject-state
files are private job staging. Rendering writes `render-job.json`,
`result.json`, and `fpv_frames/` under the requested output.

Use `--prepare-only` to inspect a scene/render job without launching Minecraft.
Use `--force` only to replace a valid output owned by the same processor.
Configuration has two disjoint roots: durable `paths.artifacts` and private
`paths.runtime`.
