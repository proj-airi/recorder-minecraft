from __future__ import annotations

import os
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from platformdirs import user_cache_path

from .errors import RecorderError

CONFIG_VERSION = 1
DEFAULT_CONFIG_NAME = "recorder.toml"
_MEMORY_RE = re.compile(r"^[1-9][0-9]*(?:[KMGTP]i?B?|%)$", re.IGNORECASE)
APP_NAME = "minerec"
DEFAULT_DOCKER_RABBITMQ_URL = "amqp://guest:guest@rabbitmq:5672/%2F"
DEFAULT_RENDER_TASK_QUEUE = "minerec.render.jobs"
ENV_CONFIG_FILE = "MC_CONFIG_FILE"
ENV_CONTROL_DIR = "MC_CONTROL_DIR"
ENV_DASHBOARD_PASSWORD = "MC_RECORDER_DASHBOARD_PASSWORD"
ENV_DASHBOARD_STATIC_ROOT = "MC_RECORDER_DASHBOARD_STATIC_ROOT"
ENV_DASHBOARD_USERNAME = "MC_RECORDER_DASHBOARD_USERNAME"
ENV_GRADLE_EXECUTABLE = "MC_RECORDER_GRADLE"
ENV_RENDER_JOB = "MC_RECORDER_RENDER_JOB"
ENV_RENDER_TASK_QUEUE = "MC_RECORDER_RENDER_TASK_QUEUE"
ENV_REPLAY_ROOT = "MC_RECORDER_REPLAY_ROOT"
ENV_RABBITMQ_URL = "MC_RECORDER_RABBITMQ_URL"
ENV_SCENE_JOB = "MC_RECORDER_SCENE_JOB"
ENV_STORAGE_CAPTURE_ROOT = "MC_RECORDER_CAPTURE_ROOT"
ENV_STORAGE_CHECK_INTERVAL = "MC_RECORDER_CHECK_INTERVAL"
ENV_STORAGE_EVICT_OLDEST = "MC_RECORDER_EVICT_OLDEST"
ENV_STORAGE_QUOTA_BYTES = "MC_RECORDER_QUOTA_BYTES"
ENV_STORAGE_WARN_PERCENT = "MC_RECORDER_WARN_PERCENT"


def _table(data: dict[str, Any], name: str) -> dict[str, Any]:
    value = data.get(name, {})
    if not isinstance(value, dict):
        raise RecorderError(f"[{name}] must be a TOML table")
    return value


def _value(table: dict[str, Any], key: str, default: Any, expected: type) -> Any:  # noqa: ANN401
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


@dataclass(frozen=True)
class DashboardEnvConfig:
    username: str
    password: str
    static_root: Path | None


@dataclass(frozen=True)
class RenderQueueEnvConfig:
    rabbitmq_url: str
    task_queue: str


@dataclass(frozen=True)
class StorageMonitorEnvConfig:
    capture_root: Path
    quota_bytes: int
    warn_percent: int
    check_interval_seconds: int
    evict_oldest: bool


@dataclass(frozen=True)
class RuntimeEnvConfig:
    dashboard: DashboardEnvConfig
    render_queue: RenderQueueEnvConfig
    replay_root: Path | None
    gradle_executable: str | None
    worker_cache_root: Path


def _env(environ: Mapping[str, str], name: str, default: str = "") -> str:
    return environ.get(name, default)


def _env_path(environ: Mapping[str, str], name: str) -> Path | None:
    value = _env(environ, name).strip()
    return Path(value).expanduser().resolve() if value else None


def _env_bool(environ: Mapping[str, str], name: str, default: bool) -> bool:
    value = environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(environ: Mapping[str, str], name: str, default: int | None = None) -> int:
    value = environ.get(name)
    if value is None:
        if default is None:
            raise RecorderError(f"{name} is required")
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise RecorderError(f"{name} must be an integer") from exc


def current_process_environment() -> dict[str, str]:
    """Return a mutable copy of the host environment.

    Keeping this boundary in one module makes it clear which code is inheriting
    ambient process state and keeps direct environment reads out of business
    logic modules.
    """

    return dict(os.environ)


def load_runtime_env(environ: Mapping[str, str] | None = None) -> RuntimeEnvConfig:
    source = os.environ if environ is None else environ
    return RuntimeEnvConfig(
        dashboard=DashboardEnvConfig(
            username=_env(source, ENV_DASHBOARD_USERNAME),
            password=_env(source, ENV_DASHBOARD_PASSWORD),
            static_root=_env_path(source, ENV_DASHBOARD_STATIC_ROOT),
        ),
        render_queue=RenderQueueEnvConfig(
            rabbitmq_url=_env(source, ENV_RABBITMQ_URL),
            task_queue=_env(source, ENV_RENDER_TASK_QUEUE, DEFAULT_RENDER_TASK_QUEUE),
        ),
        replay_root=_env_path(source, ENV_REPLAY_ROOT),
        gradle_executable=_env(source, ENV_GRADLE_EXECUTABLE) or None,
        worker_cache_root=user_cache_path(APP_NAME),
    )


def load_storage_monitor_env(environ: Mapping[str, str] | None = None) -> StorageMonitorEnvConfig:
    source = os.environ if environ is None else environ
    return StorageMonitorEnvConfig(
        capture_root=Path(_env(source, ENV_STORAGE_CAPTURE_ROOT, "/captures")).expanduser().resolve(),
        quota_bytes=_env_int(source, ENV_STORAGE_QUOTA_BYTES),
        warn_percent=_env_int(source, ENV_STORAGE_WARN_PERCENT, 80),
        check_interval_seconds=_env_int(source, ENV_STORAGE_CHECK_INTERVAL, 60),
        evict_oldest=_env_bool(source, ENV_STORAGE_EVICT_OLDEST, True),
    )


def compose_environment_variables(config: RecorderConfig, *, dashboard_static: Path, minerec_source: Path, render_task_queue: str) -> dict[str, object]:
    return {
        "MC_IMAGE": config.server.image,
        "MC_EULA": "TRUE" if config.server.eula else "FALSE",
        "MC_VERSION": config.server.minecraft_version,
        "MC_FABRIC_LOADER_VERSION": config.server.fabric_loader_version,
        "MC_PORT": config.server.port,
        "MC_MEMORY": config.server.memory,
        "MC_SEED": config.server.seed,
        "MC_GAME_MODE": config.server.game_mode,
        "MC_DIFFICULTY": config.server.difficulty,
        "MC_VIEW_DISTANCE": config.server.view_distance,
        "MC_SIMULATION_DISTANCE": config.server.simulation_distance,
        "MC_ONLINE_MODE": str(config.server.online_mode).lower(),
        "MC_MAX_PLAYERS": config.server.max_players,
        "MC_LEVEL_NAME": config.server.level_name,
        "MC_MOTD": config.server.motd,
        "MC_MODRINTH_PROJECTS": ",".join(project for project in (config.mods.server_replay_project, *config.mods.extra_modrinth_projects) if project.strip()),
        "MC_DATA_DIR": config.paths.server_data,
        "MC_LOCAL_MODS_DIR": config.paths.runtime / "mods",
        "MC_CONFIG_SOURCE_DIR": config.paths.runtime / "config",
        "MC_CAPTURE_DIR": config.paths.captures,
        "MC_REPLAY_DIR": config.paths.replays,
        ENV_CONTROL_DIR: config.paths.runtime / "control",
        "MC_MINEREC_SOURCE_DIR": minerec_source,
        ENV_CONFIG_FILE: config.source,
        "MC_EXPORT_DIR": config.paths.exports,
        "MC_RUNTIME_DIR": config.paths.runtime,
        "MC_DASHBOARD_BIND": config.dashboard.bind,
        "MC_DASHBOARD_PORT": config.dashboard.port,
        "MC_DASHBOARD_STATIC_ROOT": dashboard_static,
        ENV_RABBITMQ_URL: DEFAULT_DOCKER_RABBITMQ_URL,
        ENV_RENDER_TASK_QUEUE: render_task_queue,
        ENV_STORAGE_CAPTURE_ROOT: config.paths.captures,
        ENV_STORAGE_QUOTA_BYTES: config.storage.quota_bytes,
        ENV_STORAGE_WARN_PERCENT: config.storage.warn_percent,
        ENV_STORAGE_EVICT_OLDEST: str(config.storage.evict_oldest).lower(),
        ENV_STORAGE_CHECK_INTERVAL: config.storage.check_interval_seconds,
        ENV_REPLAY_ROOT: config.paths.replays,
    }


def compose_runtime_environment(config: RecorderConfig) -> dict[str, str]:
    """Supply additive Compose variables missing from an older prepared runtime."""

    environment = current_process_environment()
    environment[ENV_CONTROL_DIR] = str(config.paths.runtime / "control")
    return environment


def load_config(path: str | Path = DEFAULT_CONFIG_NAME) -> RecorderConfig:
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise RecorderError(f"configuration not found: {source}; run 'minerec init' first")
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
        fabric_loader_version=_value(server_raw, "fabric_loader_version", "0.19.3", str),
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
        recorder_project=_resolve(base, _value(mods_raw, "recorder_project", "mods/recorder-mod", str)),
        renderer_project=_resolve(base, _value(mods_raw, "renderer_project", "mods/renderer-mod", str)),
        scene_extractor_project=_resolve(
            base,
            _value(mods_raw, "scene_extractor_project", "mods/scene-extractor-mod", str),
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
                raise RecorderError(f"{left_label} and {right_label} paths must be separate and non-nested")


def _paths_overlap(left: Path, right: Path) -> bool:
    if left == right:
        return True
    return left in right.parents or right in left.parents


def default_config_text(*, accept_eula: bool = False) -> str:
    eula = "true" if accept_eula else "false"
    return f"""version = 1

[server]
# Set true only after accepting https://aka.ms/MinecraftEULA
eula = {eula}
image = "itzg/minecraft-server:2026.7.0-java21"
minecraft_version = "1.21.8"
fabric_loader_version = "0.19.3"
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
recorder_project = "mods/recorder-mod"
renderer_project = "mods/renderer-mod"
scene_extractor_project = "mods/scene-extractor-mod"
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
# come from {ENV_DASHBOARD_USERNAME} and {ENV_DASHBOARD_PASSWORD}.
bind = "0.0.0.0"
port = 8765
"""


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
