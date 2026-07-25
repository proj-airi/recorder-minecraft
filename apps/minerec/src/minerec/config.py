from __future__ import annotations

import os
import socket
import tomllib
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import RecorderError

CONFIG_VERSION = 1
DEFAULT_CONFIG_NAME = "recorder.toml"
ENV_GRADLE_EXECUTABLE = "MC_RECORDER_GRADLE"
ENV_RENDER_JOB = "MC_RECORDER_RENDER_JOB"
ENV_SCENE_JOB = "MC_RECORDER_SCENE_JOB"
ENV_SCENE_EXTRACTOR_EXECUTABLE = "MC_RECORDER_SCENE_EXTRACTOR"


def _table(data: dict[str, Any], name: str) -> dict[str, Any]:
    value = data.get(name, {})
    if not isinstance(value, dict):
        raise RecorderError(f"[{name}] must be a TOML table")
    return value


def _require_keys(table: dict[str, Any], allowed: set[str], label: str) -> None:
    unknown = set(table) - allowed
    if unknown:
        raise RecorderError(f"unsupported {label} configuration keys: {', '.join(sorted(unknown))}")


def _value(table: dict[str, Any], key: str, default: Any, expected: type) -> Any:  # noqa: ANN401
    value = table.get(key, default)
    if not isinstance(value, expected):
        raise RecorderError(f"configuration value {key!r} must be {expected.__name__}")
    return value


def _resolve(base: Path, raw: str) -> Path:
    path = Path(raw).expanduser()
    return (base / path).resolve() if not path.is_absolute() else path.resolve()


def _server_name(table: dict[str, Any]) -> str:
    if "name" in table:
        name = _value(table, "name", "", str).strip()
        if not name:
            raise RecorderError("server.name must not be empty")
        return name

    name = socket.gethostname().strip()
    if not name:
        raise RecorderError("machine hostname is empty; configure server.name explicitly")
    return name


@dataclass(frozen=True)
class ServerConfig:
    name: str
    instance_id: str
    eula: bool


@dataclass(frozen=True)
class PathConfig:
    base: Path
    artifacts: Path
    runtime: Path


@dataclass(frozen=True)
class ModConfig:
    recorder_project: Path
    renderer_project: Path
    scene_extractor_project: Path
    scene_extractor_executable: Path


@dataclass(frozen=True)
class RecorderConfig:
    source: Path
    server: ServerConfig
    paths: PathConfig
    mods: ModConfig


def current_process_environment() -> dict[str, str]:
    return dict(os.environ)


def load_config(path: str | Path = DEFAULT_CONFIG_NAME) -> RecorderConfig:
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise RecorderError(f"configuration not found: {source}; run 'minerec init' first")
    try:
        with source.open("rb") as handle:
            raw = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise RecorderError(f"cannot read {source}: {exc}") from exc
    if raw.get("version") != CONFIG_VERSION:
        raise RecorderError(f"unsupported recorder.toml version {raw.get('version')!r}; expected {CONFIG_VERSION}")

    _require_keys(raw, {"version", "server", "paths", "mods"}, "top-level")
    base = source.parent
    server_raw = _table(raw, "server")
    paths_raw = _table(raw, "paths")
    mods_raw = _table(raw, "mods")
    _require_keys(server_raw, {"name", "instance_id", "eula"}, "server")
    _require_keys(paths_raw, {"artifacts", "runtime"}, "paths")
    _require_keys(
        mods_raw,
        {"recorder_project", "renderer_project", "scene_extractor_project", "scene_extractor_executable"},
        "mods",
    )
    name = _server_name(server_raw)
    instance_value = _value(server_raw, "instance_id", "", str)
    try:
        instance_id = str(uuid.UUID(instance_value))
    except ValueError as exc:
        raise RecorderError("configuration value 'server.instance_id' must be a UUID") from exc
    if instance_value != instance_id:
        raise RecorderError("configuration value 'server.instance_id' must use canonical UUID spelling")

    paths = PathConfig(
        base=base,
        artifacts=_resolve(base, _value(paths_raw, "artifacts", "artifacts", str)),
        runtime=_resolve(base, _value(paths_raw, "runtime", ".mc-recorder/runtime", str)),
    )
    managed = (paths.artifacts, paths.runtime)
    for index, left in enumerate(managed):
        for right in managed[index + 1 :]:
            if left == right or left in right.parents or right in left.parents:
                raise RecorderError("artifacts and runtime paths must be separate and non-nested")

    return RecorderConfig(
        source=source,
        server=ServerConfig(
            name=name,
            instance_id=instance_id,
            eula=_value(server_raw, "eula", False, bool),
        ),
        paths=paths,
        mods=ModConfig(
            recorder_project=_resolve(base, _value(mods_raw, "recorder_project", "mods/recorder-mod", str)),
            renderer_project=_resolve(base, _value(mods_raw, "renderer_project", "mods/renderer-mod", str)),
            scene_extractor_project=_resolve(base, _value(mods_raw, "scene_extractor_project", "mods/scene-extractor-mod", str)),
            scene_extractor_executable=_resolve(
                base,
                _value(
                    mods_raw,
                    "scene_extractor_executable",
                    "mods/scene-extractor-mod/build/install/mc-recorder-scene-extractor/bin/mc-recorder-scene-extractor",
                    str,
                ),
            ),
        ),
    )


def default_config_text(*, accept_eula: bool = False, server_instance_id: str | None = None) -> str:
    eula = "true" if accept_eula else "false"
    instance_id = str(uuid.UUID(server_instance_id)) if server_instance_id else str(uuid.uuid4())
    return f'''version = 1

[server]
# Optional artifact display name. When omitted, the machine hostname is used.
# name = "minecraft"
# Accept https://aka.ms/MinecraftEULA before provisioning a recorder server.
instance_id = "{instance_id}"
eula = {eula}

[paths]
# Processing scratch remains outside recorder artifacts.
artifacts = "artifacts"
runtime = ".mc-recorder/runtime"

[mods]
recorder_project = "mods/recorder-mod"
renderer_project = "mods/renderer-mod"
scene_extractor_project = "mods/scene-extractor-mod"
scene_extractor_executable = "mods/scene-extractor-mod/build/install/mc-recorder-scene-extractor/bin/mc-recorder-scene-extractor"
'''


def initialize(path: str | Path, *, accept_eula: bool = False, force: bool = False) -> Path:
    requested = Path(path).expanduser()
    if requested.is_symlink():
        raise RecorderError(f"refusing to initialize through a symlinked configuration path: {requested}")
    target = requested.resolve()
    if target.exists() and not force:
        raise RecorderError(f"refusing to overwrite existing configuration: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(default_config_text(accept_eula=accept_eula), encoding="utf-8")
    config = load_config(target)
    for directory in (config.paths.artifacts, config.paths.runtime):
        directory.mkdir(parents=True, exist_ok=True)
    return target
