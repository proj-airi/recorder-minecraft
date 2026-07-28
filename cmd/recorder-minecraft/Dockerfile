# syntax=docker/dockerfile:1.7

FROM golang:1.26.4-bookworm AS build
WORKDIR /src
COPY go.mod go.sum ./
RUN --mount=type=cache,target=/go/pkg/mod go mod download
COPY apis ./apis
COPY cmd ./cmd
COPY databases ./databases
COPY internal ./internal
RUN --mount=type=cache,target=/go/pkg/mod --mount=type=cache,target=/root/.cache/go-build \
    CGO_ENABLED=0 go build -trimpath -ldflags='-s -w' -o /out/recorder-minecraft ./cmd/recorder-minecraft

FROM ubuntu:24.04 AS runtime
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates openjdk-21-jre-headless \
    && rm -rf /var/lib/apt/lists/*
COPY --from=build /out/recorder-minecraft /usr/local/bin/recorder-minecraft
COPY mods/scene-extractor-mod/build/install/mc-recorder-scene-extractor /mods/scene-extractor-mod/build/install/mc-recorder-scene-extractor
RUN chmod 0755 /mods/scene-extractor-mod/build/install/mc-recorder-scene-extractor/bin/mc-recorder-scene-extractor
ENV MC_RECORDER_SCENE_EXTRACTOR=/mods/scene-extractor-mod/build/install/mc-recorder-scene-extractor/bin/mc-recorder-scene-extractor
ENTRYPOINT ["recorder-minecraft"]
CMD ["--help"]
