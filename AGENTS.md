# Repository Guidelines

## Project Structure & Module Organization

- `tooling/src/mc_recorder/` contains the Python provisioning, validation, export, retention, and render-job CLI. Its tests live in `tooling/tests/`.
- `recorder-mod/` is the Kotlin/Java Fabric server mod; production code is under `src/main/` and JUnit tests under `src/test/`.
- `renderer-mod/` is the Java Fabric client mod that replays Flashback archives into RGB frames and optional voxel crops.
- `schemas/` defines the source JSONL and dataset contracts. Update these documents when changing persisted fields or invariants.
- `deploy/` contains Docker Compose configuration. `ServerReplay/` is upstream source; avoid unrelated edits there.
- `artifacts/`, `.mc-recorder/`, `runtime/`, and renderer `run/` directories are generated and must not be committed.

## Build, Test, and Development Commands

Install the pinned Python and JVM toolchains:

```sh
proto install --config-mode local
pixi install --locked
```

Run the automated checks:

```sh
pixi run test-python
pixi run build-recorder-mod
pixi run build-renderer-mod
```

Use `MC_RECORDER_FLASHBACK_JAR=/path/to/Flashback-0.39.5.jar` for renderer builds when Modrinth is unavailable. For an end-to-end capture, use `pixi run mc-recorder server start --wait`, join `localhost:25565`, then run `pixi run mc-recorder server stop` and `pixi run mc-recorder episodes validate SESSION_ID`.

## Coding Style & Naming Conventions

Use four-space indentation. Follow standard Python `snake_case`, Java/Kotlin `UpperCamelCase` types, and `lowerCamelCase` members. Keep CLI errors actionable and preserve explicit type hints. Prefer small functions around integrity boundaries; never weaken containment, SHA-256, sealing, or provenance checks. No repository-wide autoformatter is configured, so match adjacent code and keep `git diff --check` clean.

## Testing Guidelines

Python tests use `unittest` and files named `test_*.py`. Mod tests use JUnit 5/Kotlin Test and classes named `*Test`. Add regression coverage beside the affected module. Changes to capture alignment, rendering, or retention should also be exercised with a sealed real replay; do not mutate source captures during verification.

## Commit & Pull Request Guidelines

History follows Conventional Commits such as `feat(tooling): ...`, `fix(capture): ...`, and `docs: ...`. Keep commits focused and commit working changes as you go. Pull requests should explain behavior and safety implications, list exact tests run, link relevant issues, and include sample manifests or rendered screenshots when output contracts or visuals change. Never include player chat, credentials, local `recorder.toml`, worlds, or generated recordings.
