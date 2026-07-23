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
| Docker Compose runtime | `minerec` CLI | `deploy/docker-compose.yml`, generated `.mc-recorder/compose.env` |
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
./hack/start-minecraft-server
./hack/restart-minecraft-server
./hack/stop-minecraft-server
./hack/start-dashboard
pixi run test-python
pixi run build-recorder-mod
pixi run build-scene-extractor-mod
pixi run build-renderer-mod
pixi run check
```

The `hack/` scripts are thin wrappers around the same `pixi run minerec`
commands used by the dashboard and CI-oriented docs. They keep Compose behind
the CLI so mod staging, generated configuration, EULA validation, and retention
checks still run before Docker starts.

`pixi run check` runs the Python test suite and all three Gradle builds. Renderer
builds normally resolve Flashback from the pinned Modrinth version ID in
`mods/renderer-mod/gradle.properties`. If Modrinth is unavailable, set
`MC_RECORDER_FLASHBACK_JAR` to the Flashback 0.39.5 JAR for Minecraft 1.21.8.

Scene extraction launches the proto-managed Gradle executable directly. An
interactive shell normally exposes it through proto's shims. A service manager
often has a narrower `PATH`; in that case set `MC_RECORDER_GRADLE` to an
absolute executable Gradle path in the service environment. The launcher does
not invoke a shell and rejects relative overrides.

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
