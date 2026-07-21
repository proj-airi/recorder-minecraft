from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import RecorderError


CONFIG_VERSION = 1
DEFAULT_CONFIG_NAME = "recorder.toml"
_MEMORY_RE = re.compile(r"^[1-9][0-9]*(?:[KMGTP]i?B?|%)$", re.IGNORECASE)


def _table(data: dict[str, Any], name: str) -> dict[str, Any]:
    value = data.get(name, {})
    if not isinstance(value, dict):
        raise RecorderError(f"[{name}] must be a TOML table")
    return value


def _value(table: dict[str, Any], key: str, default: Any, expected: type) -> Any:
    value = table.get(key, default)
    if expected is float and isinstance(value, int):
        value = float(value)
    if not isinstance(value, expected):
        raise RecorderError(f"configuration value {key!r} must be {expected.__name__}")
    return value


def _resolve(base: Path, raw: str) -> Path:
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = base / path
    return path.resolve()


@dataclass(frozen=True)
class ServerConfig:
    eula: bool
    image: str
    minecraft_version: str
    fabric_loader_version: str
    port: int
    memory: str
    seed: str
    game_mode: str
    difficulty: str
    view_distance: int
    simulation_distance: int
    online_mode: bool
    max_players: int
    level_name: str
    motd: str


@dataclass(frozen=True)
class PathConfig:
    base: Path
    server_data: Path
    captures: Path
    replays: Path
    exports: Path
    runtime: Path
    compose_file: Path


@dataclass(frozen=True)
class ModConfig:
    recorder_project: Path
    renderer_project: Path
    scene_extractor_project: Path
    recorder_jar: Path | None
    build_on_start: bool
    server_replay_project: str
    extra_modrinth_projects: tuple[str, ...]


@dataclass(frozen=True)
class StorageConfig:
    quota_gib: float
    warn_percent: int
    evict_oldest: bool
    check_interval_seconds: int

    @property
    def quota_bytes(self) -> int:
        return int(self.quota_gib * 1024**3)


@dataclass(frozen=True)
class CaptureConfig:
    epoch_ticks: int


@dataclass(frozen=True)
class DashboardConfig:
    bind: str
    port: int


@dataclass(frozen=True)
class RecorderConfig:
    source: Path
    server: ServerConfig
    paths: PathConfig
    mods: ModConfig
    storage: StorageConfig
    capture: CaptureConfig
    dashboard: DashboardConfig


def load_config(path: str | Path = DEFAULT_CONFIG_NAME) -> RecorderConfig:
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise RecorderError(f"configuration not found: {source}; run 'mc-recorder init' first")
    try:
        with source.open("rb") as handle:
            raw = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise RecorderError(f"cannot read {source}: {exc}") from exc

    version = raw.get("version")
    if version != CONFIG_VERSION:
        raise RecorderError(f"unsupported recorder.toml version {version!r}; expected {CONFIG_VERSION}")

    base = source.parent
    server_raw = _table(raw, "server")
    paths_raw = _table(raw, "paths")
    mods_raw = _table(raw, "mods")
    storage_raw = _table(raw, "storage")
    capture_raw = _table(raw, "capture")
    dashboard_raw = _table(raw, "dashboard")

    server = ServerConfig(
        eula=_value(server_raw, "eula", False, bool),
        image=_value(server_raw, "image", "itzg/minecraft-server:2026.7.0-java21", str),
        minecraft_version=_value(server_raw, "minecraft_version", "1.21.8", str),
        fabric_loader_version=_value(server_raw, "fabric_loader_version", "0.17.2", str),
        port=_value(server_raw, "port", 25565, int),
        memory=_value(server_raw, "memory", "4G", str),
        seed=_value(server_raw, "seed", "", str),
        game_mode=_value(server_raw, "game_mode", "survival", str),
        difficulty=_value(server_raw, "difficulty", "normal", str),
        view_distance=_value(server_raw, "view_distance", 10, int),
        simulation_distance=_value(server_raw, "simulation_distance", 10, int),
        online_mode=_value(server_raw, "online_mode", True, bool),
        max_players=_value(server_raw, "max_players", 20, int),
        level_name=_value(server_raw, "level_name", "world", str),
        motd=_value(server_raw, "motd", "Minecraft dataset recorder", str),
    )

    paths = PathConfig(
        base=base,
        server_data=_resolve(base, _value(paths_raw, "server_data", "artifacts/server", str)),
        captures=_resolve(base, _value(paths_raw, "captures", "artifacts/captures", str)),
        replays=_resolve(base, _value(paths_raw, "replays", "artifacts/replays", str)),
        exports=_resolve(base, _value(paths_raw, "exports", "artifacts/exports", str)),
        runtime=_resolve(base, _value(paths_raw, "runtime", ".mc-recorder", str)),
        compose_file=_resolve(base, _value(paths_raw, "compose_file", "deploy/docker-compose.yml", str)),
    )

    recorder_jar_raw = _value(mods_raw, "recorder_jar", "", str)
    extra_projects = mods_raw.get("extra_modrinth_projects", [])
    if not isinstance(extra_projects, list) or not all(isinstance(item, str) for item in extra_projects):
        raise RecorderError("mods.extra_modrinth_projects must be an array of strings")
    mods = ModConfig(
        recorder_project=_resolve(base, _value(mods_raw, "recorder_project", "recorder-mod", str)),
        renderer_project=_resolve(base, _value(mods_raw, "renderer_project", "renderer-mod", str)),
        scene_extractor_project=_resolve(
            base,
            _value(mods_raw, "scene_extractor_project", "scene-extractor-mod", str),
        ),
        recorder_jar=_resolve(base, recorder_jar_raw) if recorder_jar_raw else None,
        build_on_start=_value(mods_raw, "build_on_start", True, bool),
        server_replay_project=_value(mods_raw, "server_replay_project", "server-replay:TbWIikrT", str),
        extra_modrinth_projects=tuple(extra_projects),
    )

    storage = StorageConfig(
        quota_gib=_value(storage_raw, "quota_gib", 100.0, float),
        warn_percent=_value(storage_raw, "warn_percent", 80, int),
        evict_oldest=_value(storage_raw, "evict_oldest", True, bool),
        check_interval_seconds=_value(storage_raw, "check_interval_seconds", 60, int),
    )
    capture = CaptureConfig(epoch_ticks=_value(capture_raw, "epoch_ticks", 6000, int))
    dashboard = DashboardConfig(
        bind=_value(dashboard_raw, "bind", "0.0.0.0", str),
        port=_value(dashboard_raw, "port", 8765, int),
    )

    _validate(server, paths, mods, storage, capture, dashboard)
    return RecorderConfig(
        source=source,
        server=server,
        paths=paths,
        mods=mods,
        storage=storage,
        capture=capture,
        dashboard=dashboard,
    )


def _validate(
    server: ServerConfig,
    paths: PathConfig,
    mods: ModConfig,
    storage: StorageConfig,
    capture: CaptureConfig,
    dashboard: DashboardConfig,
) -> None:
    if server.minecraft_version != "1.21.8":
        raise RecorderError("v1 supports exactly Minecraft 1.21.8")
    if not 1 <= server.port <= 65535:
        raise RecorderError("server.port must be between 1 and 65535")
    if not _MEMORY_RE.fullmatch(server.memory):
        raise RecorderError("server.memory must look like '4G', '4096M', or '75%'")
    if server.game_mode not in {"survival", "creative", "adventure", "spectator"}:
        raise RecorderError("server.game_mode is invalid")
    if server.difficulty not in {"peaceful", "easy", "normal", "hard"}:
        raise RecorderError("server.difficulty is invalid")
    if not 2 <= server.view_distance <= 32 or not 2 <= server.simulation_distance <= 32:
        raise RecorderError("view and simulation distances must be between 2 and 32")
    if not 1 <= server.max_players <= 1000:
        raise RecorderError("server.max_players must be between 1 and 1000")
    if storage.quota_gib <= 0:
        raise RecorderError("storage.quota_gib must be positive")
    if not 1 <= storage.warn_percent <= 100:
        raise RecorderError("storage.warn_percent must be between 1 and 100")
    if not 10 <= storage.check_interval_seconds <= 3600:
        raise RecorderError("storage.check_interval_seconds must be between 10 and 3600")
    if capture.epoch_ticks < 20:
        raise RecorderError("capture.epoch_ticks must be at least 20")
    if not dashboard.bind.strip():
        raise RecorderError("dashboard.bind must not be empty")
    if not 1 <= dashboard.port <= 65535:
        raise RecorderError("dashboard.port must be between 1 and 65535")
    managed_paths = (
        ("server data", paths.server_data),
        ("capture", paths.captures),
        ("replay", paths.replays),
        ("export", paths.exports),
        ("runtime", paths.runtime),
    )
    for index, (left_label, left_path) in enumerate(managed_paths):
        for right_label, right_path in managed_paths[index + 1 :]:
            if _paths_overlap(left_path, right_path):
                raise RecorderError(
                    f"{left_label} and {right_label} paths must be separate and non-nested"
                )


def _paths_overlap(left: Path, right: Path) -> bool:
    if left == right:
        return True
    return left in right.parents or right in left.parents


def default_config_text(*, accept_eula: bool = False) -> str:
    eula = "true" if accept_eula else "false"
    return f'''version = 1

[server]
# Set true only after accepting https://aka.ms/MinecraftEULA
eula = {eula}
image = "itzg/minecraft-server:2026.7.0-java21"
minecraft_version = "1.21.8"
fabric_loader_version = "0.17.2"
port = 25565
memory = "4G"
seed = ""
game_mode = "survival"
difficulty = "normal"
view_distance = 10
simulation_distance = 10
online_mode = true
max_players = 20
level_name = "world"
motd = "Minecraft dataset recorder"

[paths]
# Relative paths are resolved from this file. All defaults stay in this workspace.
server_data = "artifacts/server"
captures = "artifacts/captures"
replays = "artifacts/replays"
exports = "artifacts/exports"
runtime = ".mc-recorder"
compose_file = "deploy/docker-compose.yml"

[mods]
recorder_project = "recorder-mod"
renderer_project = "renderer-mod"
scene_extractor_project = "scene-extractor-mod"
# Optional explicit JAR. When empty, build/libs is discovered after a local build.
recorder_jar = ""
build_on_start = true
# Immutable Modrinth version ID for ServerReplay 3.0.1+1.21.8.
# Set empty only when ServerReplay and its required dependencies are staged in runtime/mods.
server_replay_project = "server-replay:TbWIikrT"
extra_modrinth_projects = []

[capture]
# Five minutes at 20 ticks/second. Sealed epochs are the unit of retention.
epoch_ticks = 6000

[storage]
# Applies to capture epochs plus completed replay archives; world data is never evicted.
quota_gib = 100.0
warn_percent = 80
evict_oldest = true
check_interval_seconds = 60

[dashboard]
# Bind to all interfaces for the trusted-LAN dashboard. HTTP Basic credentials
# come from MC_RECORDER_DASHBOARD_USERNAME and MC_RECORDER_DASHBOARD_PASSWORD.
bind = "0.0.0.0"
port = 8765
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
    for directory in (
        config.paths.server_data,
        config.paths.captures,
        config.paths.replays,
        config.paths.exports,
        config.paths.runtime,
    ):
        directory.mkdir(parents=True, exist_ok=True)
    return target
