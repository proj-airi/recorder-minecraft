# Development Environment

This repository uses Pixi for the Python CLI workspace and proto for the JVM
toolchain. Gradle is installed by proto instead of the Gradle wrapper.

## Tool Ownership

| Area | Owner | Configuration |
| --- | --- | --- |
| Python 3.14 development interpreter, editable `minerec` install, and root tasks | Pixi | `pixi.toml`, `pixi.lock` |
| OpenJDK 21 and Gradle 9.6.1 | proto | `.prototools` |
| Python package metadata | setuptools | `apps/minerec/pyproject.toml` |
| Fabric/Kotlin build logic and dependencies | Gradle projects | `mods/recorder-mod/`, `mods/scene-extractor-mod/`, `mods/renderer-mod/` |
| Docker Compose runtime | Docker Compose | `deploy/docker-compose.yml`, `deploy/.env` copied from `deploy/.env.example` |
| Pull request verification | GitHub Actions | `.github/workflows/ci.yml` |

Do not use a repository-local virtualenv for normal development, and do not add
the Gradle wrapper back unless the JVM toolchain decision changes.

## First-Time Setup

Install Pixi and proto, then install both locked environments from the
repository root:

```sh
./hack/install
```

Verify the toolchain:

```sh
proto run openjdk -- --version
proto run gradle -- --version
pixi run python --version
pixi run minerec --help
```

If your shell is configured with proto shims, plain `java` and `gradle` should
resolve to the versions pinned in `.prototools` while you are in this
repository.

## Daily Commands

```sh
./hack/minecraft-server start
./hack/minecraft-server restart
./hack/minecraft-server stop
./hack/minecraft-server logs --follow
./hack/dashboard start
pixi run test-python
pixi run build-recorder-mod
pixi run build-scene-extractor-mod
pixi run build-renderer-mod
pixi run check
```

The Minecraft `hack/minecraft-server` command is a thin wrapper around Docker
Compose and `hack/minecraft-server prepare`. Use Docker Compose directly with
`--env-file deploy/.env --file deploy/docker-compose.yml` when you only need
container lifecycle control. Run `hack/minecraft-server prepare` after changing
the local recorder mod or mod configuration inputs.

`pixi run check` runs the Python test suite and all three Gradle builds. Renderer
builds normally resolve Flashback from the pinned Modrinth version ID in
`mods/renderer-mod/gradle.properties`. If Modrinth is unavailable, set
`MC_RECORDER_FLASHBACK_JAR` to the Flashback 0.39.5 JAR for Minecraft 1.21.8.

`minerec render-worker` runs `prepareRendererRuntime` before registering
with a remote queue. This resolves Flashback, Minecraft libraries, launch files,
natives, and assets without consuming a render attempt; claimed renders then run
with Gradle offline. The preparation uses Gradle's normal user cache, or the
operator-provided `GRADLE_USER_HOME`, so direct builds and workers share verified
downloads instead of maintaining a second recorder-private cache.

Scene extraction launches the `mc-recorder-scene-extractor` CLI produced by
`pixi run build-scene-extractor-mod`. By default the Python launcher uses
`mods/scene-extractor-mod/build/install/mc-recorder-scene-extractor/bin/mc-recorder-scene-extractor`.
Set `MC_RECORDER_SCENE_EXTRACTOR` to an absolute executable path when a service
manager needs a different installed extractor. The launcher does not invoke a
shell and rejects relative overrides.

## macOS Notes

The proto-managed OpenJDK replaces the previous Homebrew/jenv setup for this
repository. If `java --version` does not show Java 21 inside the repository,
check that proto's shell integration is active:

```sh
proto activate zsh
exec zsh
java --version
gradle --version
```

You can always bypass shell activation for diagnostics:

```sh
proto run openjdk -- --version
proto run gradle -- --version
```

## Capture Runtime Requirements

Development tooling does not replace runtime services. To capture or replay
data, the machine still needs Docker with Docker Compose. GUI rendering also
needs a logged-in graphical desktop session plus OpenSSH and `rsync` for remote
worker mode.
