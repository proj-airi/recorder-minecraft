from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from platformdirs import user_cache_path

from .errors import RecorderError

CONFIG_VERSION = 1
DEFAULT_CONFIG_NAME = "recorder.toml"
APP_NAME = "minerec"
DEFAULT_RENDER_TASK_QUEUE = "minerec.render.jobs"
ENV_DASHBOARD_PASSWORD = "MC_RECORDER_DASHBOARD_PASSWORD"
ENV_DASHBOARD_STATIC_ROOT = "MC_RECORDER_DASHBOARD_STATIC_ROOT"
ENV_DASHBOARD_USERNAME = "MC_RECORDER_DASHBOARD_USERNAME"
ENV_GRADLE_EXECUTABLE = "MC_RECORDER_GRADLE"
ENV_RENDER_JOB = "MC_RECORDER_RENDER_JOB"
ENV_RENDER_TASK_QUEUE = "MC_RECORDER_RENDER_TASK_QUEUE"
ENV_REPLAY_ROOT = "MC_RECORDER_REPLAY_ROOT"
ENV_RABBITMQ_URL = "MC_RECORDER_RABBITMQ_URL"
ENV_SCENE_JOB = "MC_RECORDER_SCENE_JOB"
ENV_SCENE_EXTRACTOR_EXECUTABLE = "MC_RECORDER_SCENE_EXTRACTOR"
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


@dataclass(frozen=True)
class PathConfig:
    base: Path
    captures: Path
    replays: Path
    exports: Path
    runtime: Path


@dataclass(frozen=True)
class ModConfig:
    recorder_project: Path
    renderer_project: Path
    scene_extractor_project: Path
    scene_extractor_executable: Path


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
    dashboard_raw = _table(raw, "dashboard")

    server = ServerConfig(
        eula=_value(server_raw, "eula", False, bool),
    )

    paths = PathConfig(
        base=base,
        captures=_resolve(base, _value(paths_raw, "captures", "artifacts/captures", str)),
        replays=_resolve(base, _value(paths_raw, "replays", "artifacts/replays", str)),
        exports=_resolve(base, _value(paths_raw, "exports", "artifacts/exports", str)),
        runtime=_resolve(base, _value(paths_raw, "runtime", ".mc-recorder", str)),
    )

    mods = ModConfig(
        recorder_project=_resolve(base, _value(mods_raw, "recorder_project", "mods/recorder-mod", str)),
        renderer_project=_resolve(base, _value(mods_raw, "renderer_project", "mods/renderer-mod", str)),
        scene_extractor_project=_resolve(
            base,
            _value(mods_raw, "scene_extractor_project", "mods/scene-extractor-mod", str),
        ),
        scene_extractor_executable=_resolve(
            base,
            _value(
                mods_raw,
                "scene_extractor_executable",
                "mods/scene-extractor-mod/build/install/mc-recorder-scene-extractor/bin/mc-recorder-scene-extractor",
                str,
            ),
        ),
    )

    storage = StorageConfig(
        quota_gib=_value(storage_raw, "quota_gib", 100.0, float),
        warn_percent=_value(storage_raw, "warn_percent", 80, int),
        evict_oldest=_value(storage_raw, "evict_oldest", True, bool),
        check_interval_seconds=_value(storage_raw, "check_interval_seconds", 60, int),
    )
    dashboard = DashboardConfig(
        bind=_value(dashboard_raw, "bind", "0.0.0.0", str),
        port=_value(dashboard_raw, "port", 8765, int),
    )

    _validate(paths, storage, dashboard)
    return RecorderConfig(
        source=source,
        server=server,
        paths=paths,
        mods=mods,
        storage=storage,
        dashboard=dashboard,
    )


def _validate(
    paths: PathConfig,
    storage: StorageConfig,
    dashboard: DashboardConfig,
) -> None:
    if storage.quota_gib <= 0:
        raise RecorderError("storage.quota_gib must be positive")
    if not 1 <= storage.warn_percent <= 100:
        raise RecorderError("storage.warn_percent must be between 1 and 100")
    if not 10 <= storage.check_interval_seconds <= 3600:
        raise RecorderError("storage.check_interval_seconds must be between 10 and 3600")
    if not dashboard.bind.strip():
        raise RecorderError("dashboard.bind must not be empty")
    if not 1 <= dashboard.port <= 65535:
        raise RecorderError("dashboard.port must be between 1 and 65535")
    managed_paths = (
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
# Accept https://aka.ms/MinecraftEULA before provisioning a recorder server.
eula = {eula}

[paths]
# Relative paths are resolved from this file. All defaults stay in this workspace.
captures = "artifacts/captures"
replays = "artifacts/replays"
exports = "artifacts/exports"
runtime = ".mc-recorder"

[mods]
recorder_project = "mods/recorder-mod"
renderer_project = "mods/renderer-mod"
scene_extractor_project = "mods/scene-extractor-mod"
scene_extractor_executable = "mods/scene-extractor-mod/build/install/mc-recorder-scene-extractor/bin/mc-recorder-scene-extractor"

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
        config.paths.captures,
        config.paths.replays,
        config.paths.exports,
        config.paths.runtime,
    ):
        directory.mkdir(parents=True, exist_ok=True)
    return target
