# syntax=docker/dockerfile:1.7

FROM ghcr.io/prefix-dev/pixi:0.73.0 AS build

WORKDIR /app

COPY pixi.toml pixi.lock ./
COPY apps/minerec/pyproject.toml ./apps/minerec/pyproject.toml
COPY apps/minerec/src ./apps/minerec/src

ARG TARGETARCH
RUN --mount=type=cache,id=pixi-${TARGETARCH},target=/var/cache/pixi,sharing=locked \
    PIXI_CACHE_DIR=/var/cache/pixi \
    pixi install --locked -e default

RUN <<'EOF'
set -eu
printf '#!/bin/bash\n' > /app/entrypoint.sh
pixi shell-hook --locked -e default -s bash >> /app/entrypoint.sh
printf '\nexec "$@"\n' >> /app/entrypoint.sh
chmod 0755 /app/entrypoint.sh
EOF

FROM ubuntu:24.04 AS runtime

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends openjdk-21-jre-headless \
    && rm -rf /var/lib/apt/lists/*

COPY --from=build /app/.pixi/envs/default /app/.pixi/envs/default
COPY --from=build --chmod=0755 /app/entrypoint.sh /app/entrypoint.sh
COPY --from=build /app/apps/minerec /app/apps/minerec
COPY mods/scene-extractor-mod/build/install/mc-recorder-scene-extractor /mods/scene-extractor-mod/build/install/mc-recorder-scene-extractor

RUN chmod 0755 /mods/scene-extractor-mod/build/install/mc-recorder-scene-extractor/bin/mc-recorder-scene-extractor

ENV PYTHONUNBUFFERED=1
ENV MC_RECORDER_SCENE_EXTRACTOR=/mods/scene-extractor-mod/build/install/mc-recorder-scene-extractor/bin/mc-recorder-scene-extractor

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["minerec", "--help"]
