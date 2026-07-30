# Development environment

proto owns the repository's Go, Buf, OpenJDK, Gradle, and golangci-lint
versions. Module dependencies remain owned by Go modules and the individual
Gradle projects.

| Area | Owner | Configuration |
| --- | --- | --- |
| Go, Buf, OpenJDK, Gradle, and golangci-lint | proto | `.prototools` |
| Go dependencies | Go modules | `go.mod`, `go.sum` |
| Artifact contracts and generated SDKs | Buf | `buf.yaml`, `buf.gen.yaml`, `apis/` |
| Minecraft JVM builds | Gradle projects | `mods/`, `processors/scene-extractor/` |
| Local capture runtime | Docker Compose | `deploy/docker-compose.yml`, `deploy/.env` |
| Pull request verification | GitHub Actions | `.github/workflows/ci.yml` |

## Setup

Install proto, then run this from the repository root:

```sh
./hack/install
```

The script installs the pinned toolchains, creates `recorder.toml` when
needed, and copies the example Compose environment. If proto shims are active,
plain `go`, `buf`, `java`, `gradle`, and `golangci-lint` resolve to the pinned
versions while inside this repository.

Verify them with:

```sh
go version
buf --version
java --version
gradle --version
golangci-lint version
go run ./cmd/recorder-minecraft --help
```

The root [README](../README.md) documents which commands to run after each
kind of source change and gives the complete pre-submit sequence used by CI.

## Runtime notes

The development toolchain does not install Docker or a graphical desktop.
Docker with Compose is required for capture, and GUI rendering requires a
logged-in graphical session.

The scene extractor build installs its executable at
`processors/scene-extractor/build/install/mc-recorder-scene-extractor/bin/mc-recorder-scene-extractor`.
Set `MC_RECORDER_SCENE_EXTRACTOR` to an absolute executable path only when a
service manager needs a different installation.

Renderer builds normally fetch the pinned Flashback release from Modrinth. Set
`MC_RECORDER_FLASHBACK_JAR` to a local Flashback 0.39.5 JAR when Modrinth is
unavailable.

On macOS, activate proto in the shell if pinned tools are not being resolved:

```sh
proto activate zsh
exec zsh
```
