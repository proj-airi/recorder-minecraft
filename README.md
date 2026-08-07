# Minecraft gameplay recorder

This repository records each Minecraft 1.21.8 player connection as a primitive
capture, then turns explicit capture files into actions, random-access scene
state, and optional first-person frames.

The same Recorder mod captures a dedicated server or an integrated server
hosted by a game client. Both produce the same server-authoritative Play;
joining a remote server does not create a local Play.

## Development workflow

Install the versions of Go, Buf, OpenJDK, Gradle, and golangci-lint pinned in
`.prototools`:

```sh
proto install --config-mode local
```

Run the CLI directly while developing:

```sh
go run ./cmd/recorder-minecraft --help
go run ./cmd/recorder-minecraft init --accept-eula
```

After changing Go code, format the affected files and run the Go checks:

```sh
gofmt -w path/to/changed.go
go test ./...
golangci-lint run ./...
```

After changing files under `apis/proto/`, update and verify the generated SDKs
before testing their consumers:

```sh
buf format -w
buf lint
buf generate
git diff -- apis/sdk/go apis/sdk/jvm
go test ./...
gradle --project-dir mods/recorder-mod build
gradle --project-dir processors/scene-extractor build installDist
gradle --project-dir mods/renderer-mod build
```

Build only the component you changed:

```sh
# Go CLI
mkdir -p .recorder/minecraft/bin
go build -o .recorder/minecraft/bin/recorder-minecraft ./cmd/recorder-minecraft

# Recorder server mod
gradle --project-dir mods/recorder-mod build

# Headless scene extractor and its installed distribution
gradle --project-dir processors/scene-extractor build installDist

# Client renderer mod
gradle --project-dir mods/renderer-mod build

# Both Fabric mods
gradle --project-dir mods/recorder-mod build
gradle --project-dir mods/renderer-mod build

# Recorder mod staged for the release server image
gradle --project-dir mods/recorder-mod stageServerImage

# Self-contained recording profile for Airicraft evaluation
gradle --project-dir mods/recorder-mod stageRecordingProfile

# Local server image; build the extractor distribution first
gradle --project-dir processors/scene-extractor build installDist
docker buildx build --platform linux/amd64 -f cmd/recorder-minecraft/Dockerfile . \
  --tag recorder-minecraft:local --load
```

Renderer builds normally fetch the pinned Flashback release. If Modrinth is
unavailable, set `MC_RECORDER_FLASHBACK_JAR` to a local Flashback 0.39.5 JAR.

The recording profile is `mods/recorder-mod/build/recording-profile/recorder-profile.jar`.
It contains the recorder mod, ServerReplay 3.0.1 for Minecraft 1.21.8, and
Fabric Language Kotlin 1.13.13 with Kotlin 2.4.10. Verify the production
profile with `gradle --project-dir mods/recorder-mod verifyRecordingProfile`.

Include the generated SDK changes in the same change as the contract. After
reviewing and staging the intended generated updates, run the same checks as
CI before submitting:

```sh
test -z "$(find cmd internal databases apis/sdk/go -name '*.go' -type f -exec gofmt -l {} +)"
buf format --diff --exit-code
buf lint
buf generate
git diff --exit-code -- apis/sdk/go apis/sdk/jvm
go test ./...
golangci-lint run ./...
gradle --project-dir mods/recorder-mod build
gradle --project-dir processors/scene-extractor build installDist
gradle --project-dir mods/renderer-mod build
git diff --check
```

## Record and process a play

Initialize local configuration once, accept the Minecraft EULA in
`deploy/.env`, and start the development server:

```sh
./hack/install
./hack/minecraft-server start
# join localhost:25565
./hack/minecraft-server stop
```

Process one completed play directory with every stage:

```sh
./hack/process-play PLAY
```

The stages can also be run independently with `go run
./cmd/recorder-minecraft`: `actions extract`, `scene extract`, and `render` each
accept explicit input and output paths.

## Artifact layout

```text
artifacts/v1/<server>--<instance>/players/<player>--<uuid>/plays/<start>--<connection>/
  metadata.json
  capture/
    events.jsonl
    replay.zip
  actions.jsonl       # optional post-process result
  scene.sqlite3       # optional post-process result
  renders/            # optional render result
    fpv_frames/
```

Each play is self-contained. Recorder-owned capture inputs are not mutated by
post-processing, and generated runtime directories must not be committed.

## Modules

```text
mods/recorder-mod/          server-authoritative recorder and canonical capture writer
mods/renderer-mod/          client-only first-person frame renderer
processors/scene-extractor/ headless Flashback scene reducer
cmd/recorder-minecraft/     Go file-to-file processor CLI
apis/proto/                 Protobuf artifact contracts
databases/scene/            Ent model for per-play scene.sqlite3 files
deploy/                     recorder server Compose configuration
```

See [Artifacts V1 Pipeline](docs/specs/artifacts-v1.md),
[Primitive Capture V1](docs/specs/capture-v1.md),
[Terms and Concepts](docs/TERMS_AND_CONCEPTS.md), and
[the CLI reference](cmd/recorder-minecraft/README.md).

Record only players who have consented. Captures and derived artifacts can
contain sensitive gameplay data.
