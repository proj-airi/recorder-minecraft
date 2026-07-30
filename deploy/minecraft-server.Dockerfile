# syntax=docker/dockerfile:1.7

FROM itzg/minecraft-server:2026.7.0-java21

COPY mods/recorder-mod/build/server-image/mc-recorder-mod.jar /opt/recorder-minecraft/mods/mc-recorder-mod.jar

# The image-owned source remains available when operators mount additional
# local mods at /mods. The base image synchronizes both sources into /data/mods.
ENV COPY_MODS_SRC=/opt/recorder-minecraft/mods,/mods \
    TYPE=FABRIC \
    VERSION=1.21.8 \
    FABRIC_LOADER_VERSION=0.19.3 \
    MODRINTH_PROJECTS=server-replay:TbWIikrT \
    MODRINTH_DOWNLOAD_DEPENDENCIES=required
