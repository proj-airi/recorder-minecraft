from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Sequence

from .config import RecorderConfig
from .errors import RecorderError
from .operations import operation_lock
from .storage import StorageReport, enforce_quota


_IGNORED_JAR_SUFFIXES = ("-sources.jar", "-javadoc.jar", "-dev.jar", "-all-dev.jar")
_MAX_STATUS_MESSAGE_CHARS = 2048


def _bounded_status_message(value: object, fallback: str) -> str:
    message = str(value or fallback).strip() or fallback
    return message[:_MAX_STATUS_MESSAGE_CHARS]


def _run(
    command: Sequence[str],
    *,
    cwd: Path | None = None,
    capture: bool = False,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            list(command),
            cwd=cwd,
            check=False,
            text=True,
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.PIPE if capture else None,
            env=env,
        )
    except FileNotFoundError as exc:
        raise RecorderError(f"required executable not found: {command[0]}") from exc


def _ensure_success(result: subprocess.CompletedProcess[str], description: str) -> None:
    if result.returncode == 0:
        return
    detail = ""
    if result.stderr:
        detail = f": {result.stderr.strip()}"
    raise RecorderError(f"{description} failed with exit code {result.returncode}{detail}")


def _jar_metadata(path: Path) -> dict[str, object]:
    try:
        with zipfile.ZipFile(path) as archive:
            raw = archive.read("fabric.mod.json")
        value = json.loads(raw)
    except (OSError, KeyError, zipfile.BadZipFile, json.JSONDecodeError) as exc:
        raise RecorderError(f"not a valid Fabric mod JAR: {path}") from exc
    if not isinstance(value, dict):
        raise RecorderError(f"fabric.mod.json is not an object in {path}")
    return value


def _candidate_jars(project: Path) -> list[Path]:
    libs = project / "build" / "libs"
    if not libs.is_dir():
        return []
    candidates: list[Path] = []
    for path in libs.glob("*.jar"):
        if any(path.name.endswith(suffix) for suffix in _IGNORED_JAR_SUFFIXES):
            continue
        try:
            metadata = _jar_metadata(path)
        except RecorderError:
            continue
        mod_id = metadata.get("id", "")
        if isinstance(mod_id, str) and ("recorder" in mod_id or "capture" in mod_id):
            candidates.append(path)
    return sorted(candidates, key=lambda item: (item.stat().st_mtime_ns, item.name), reverse=True)


def _build_recorder_mod(config: RecorderConfig) -> None:
    project = config.mods.recorder_project
    if not project.is_dir():
        raise RecorderError(f"local capture mod project not found: {project}")
    wrapper = project / "gradlew"
    if wrapper.is_file():
        command = [str(wrapper), "build"]
        cwd = project
    else:
        shared_wrapper = config.paths.base / "gradlew"
        if not shared_wrapper.is_file():
            raise RecorderError(
                f"Gradle wrapper not found at {wrapper} or shared fallback {shared_wrapper}"
            )
        command = [str(shared_wrapper), "--project-dir", str(project), "build"]
        cwd = config.paths.base
    build_environment = dict(os.environ)
    gradle_cache = config.paths.runtime / "gradle-cache"
    gradle_cache.mkdir(parents=True, exist_ok=True)
    build_environment["GRADLE_USER_HOME"] = str(gradle_cache)
    result = _run(command, cwd=cwd, env=build_environment)
    _ensure_success(result, "capture mod build")


def resolve_recorder_jar(config: RecorderConfig, *, build: bool) -> Path:
    if config.mods.recorder_jar is not None:
        jar = config.mods.recorder_jar
        if not jar.is_file():
            raise RecorderError(f"configured capture mod JAR not found: {jar}")
        _jar_metadata(jar)
        return jar

    if build and config.mods.build_on_start:
        _build_recorder_mod(config)
    candidates = _candidate_jars(config.mods.recorder_project)
    if not candidates:
        guidance = "enable mods.build_on_start or set mods.recorder_jar"
        raise RecorderError(f"no locally built capture mod JAR found; {guidance}")
    return candidates[0]


def _same_content(left: Path, right: Path) -> bool:
    if not left.is_file() or not right.is_file() or left.stat().st_size != right.stat().st_size:
        return False
    digest_left = hashlib.sha256(left.read_bytes()).digest()
    digest_right = hashlib.sha256(right.read_bytes()).digest()
    return digest_left == digest_right


def provision_local_mod(config: RecorderConfig, *, build: bool = True) -> Path:
    source = resolve_recorder_jar(config, build=build)
    mods_dir = config.paths.runtime / "mods"
    mods_dir.mkdir(parents=True, exist_ok=True)
    destination = mods_dir / "mc-recorder-capture.jar"
    if not _same_content(source, destination):
        temporary = destination.with_suffix(".jar.tmp")
        shutil.copy2(source, temporary)
        temporary.replace(destination)
    return destination


def _write_json(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def write_mod_configs(config: RecorderConfig) -> None:
    config_root = config.paths.runtime / "config"
    server_replay = {
        "default_encoding": "flashback",
        "world_name": config.server.level_name,
        "server_name": "mc-recorder",
        "chunk_recording_path": "/replays/chunks",
        "player_recording_path": "/replays/players",
        "player_recording_name": "{uuid}",
        "max_file_size": "0 B",
        "restart_after_max_file_size": False,
        "max_duration": "5m",
        "restart_after_max_duration": True,
        "recover_unsaved_replays": True,
        "delete_replays_after_duration": "0s",
        "log_deleted_replays": True,
        "chunk_recorder_load_radius": -1,
        "chunk_recording_strategy": "always",
        "pause_notify_players": False,
        "notify_admins_of_status": True,
        "include_resource_packs": True,
        "ignore_custom_payloads": False,
        "ignore_sound_packets": False,
        "ignore_light_packets": False,
        "ignore_chat_packets": True,
        "ignore_action_bar_packets": False,
        "ignore_scoreboard_packets": False,
        "optimize_explosion_packets": True,
        "optimize_entity_packets": False,
        "record_hotbar": True,
        "record_voice_chat": False,
        "replay_server_ip": None,
        "allow_downloading_replays": False,
        "automatically_record": True,
        "player_predicate": {"type": "all"},
        "chunks": [],
    }
    _write_json(config_root / "server-replay" / "config.json", server_replay)

    capture = {
        "capture_root": "/captures",
        "control_root": "/control",
        "epoch_ticks": config.capture.epoch_ticks,
        "writer_queue_capacity": 65536,
        "include_inventory_components": True,
        "record_all_players": True,
    }
    _write_json(config_root / "mc-recorder.json", capture)


def _dotenv_quote(value: object) -> str:
    text = str(value)
    text = text.replace("\\", "\\\\").replace('"', '\\"').replace("$", "$$")
    return f'"{text}"'


def _project_name(config: RecorderConfig) -> str:
    digest = hashlib.sha256(str(config.source).encode()).hexdigest()[:8]
    return f"mc-recorder-{digest}"


def write_compose_env(config: RecorderConfig) -> Path:
    config.paths.runtime.mkdir(parents=True, exist_ok=True)
    tooling_source = config.paths.base / "tooling" / "src"
    if not tooling_source.is_dir():
        raise RecorderError(f"tooling source directory not found: {tooling_source}")
    values: dict[str, object] = {
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
        "MC_MODRINTH_PROJECTS": ",".join(
            project
            for project in (config.mods.server_replay_project, *config.mods.extra_modrinth_projects)
            if project.strip()
        ),
        "MC_DATA_DIR": config.paths.server_data,
        "MC_LOCAL_MODS_DIR": config.paths.runtime / "mods",
        "MC_CONFIG_SOURCE_DIR": config.paths.runtime / "config",
        "MC_CAPTURE_DIR": config.paths.captures,
        "MC_REPLAY_DIR": config.paths.replays,
        "MC_CONTROL_DIR": config.paths.runtime / "control",
        "MC_TOOLING_SOURCE_DIR": tooling_source,
        "MC_CAPTURE_QUOTA_BYTES": config.storage.quota_bytes,
        "MC_CAPTURE_WARN_PERCENT": config.storage.warn_percent,
        "MC_CAPTURE_EVICT_OLDEST": str(config.storage.evict_oldest).lower(),
        "MC_STORAGE_CHECK_INTERVAL": config.storage.check_interval_seconds,
    }
    target = config.paths.runtime / "compose.env"
    temporary = target.with_suffix(".env.tmp")
    temporary.write_text("".join(f"{key}={_dotenv_quote(value)}\n" for key, value in values.items()), encoding="utf-8")
    temporary.replace(target)
    try:
        target.chmod(0o600)
    except OSError:
        pass
    return target


def prepare_runtime(config: RecorderConfig, *, build_mod: bool) -> Path | None:
    for directory in (
        config.paths.server_data,
        config.paths.captures,
        config.paths.replays,
        config.paths.exports,
        config.paths.runtime,
        config.paths.runtime / "mods",
        config.paths.runtime / "config",
        config.paths.runtime / "control",
        config.paths.runtime / "control" / "requests",
        config.paths.runtime / "control" / "responses",
    ):
        directory.mkdir(parents=True, exist_ok=True)
    jar = provision_local_mod(config, build=build_mod) if build_mod else None
    write_mod_configs(config)
    write_compose_env(config)
    if not config.paths.compose_file.is_file():
        raise RecorderError(f"Docker Compose file not found: {config.paths.compose_file}")
    return jar


def check_storage(config: RecorderConfig) -> StorageReport:
    return enforce_quota(
        config.paths.captures,
        quota_bytes=config.storage.quota_bytes,
        warn_percent=config.storage.warn_percent,
        evict_oldest=config.storage.evict_oldest,
        replays_root=config.paths.replays,
    )


def compose_command(config: RecorderConfig, *arguments: str) -> list[str]:
    return [
        "docker",
        "compose",
        "--env-file",
        str(config.paths.runtime / "compose.env"),
        "--project-name",
        _project_name(config),
        "--file",
        str(config.paths.compose_file),
        *arguments,
    ]


def _compose_environment(config: RecorderConfig) -> dict[str, str]:
    """Supply additive Compose variables missing from an older prepared runtime."""

    environment = dict(os.environ)
    environment["MC_CONTROL_DIR"] = str(config.paths.runtime / "control")
    return environment


def _require_prepared_runtime(config: RecorderConfig) -> None:
    env_path = config.paths.runtime / "compose.env"
    if not env_path.is_file():
        raise RecorderError("server runtime is not prepared; run 'mc-recorder server start' first")
    if not config.paths.compose_file.is_file():
        raise RecorderError(f"Docker Compose file not found: {config.paths.compose_file}")


def verify_docker() -> None:
    result = _run(["docker", "compose", "version"], capture=True)
    _ensure_success(result, "Docker Compose check")


def start_server(
    config: RecorderConfig,
    *,
    wait: bool = False,
    capture_output: bool = False,
) -> tuple[Path, StorageReport]:
    with operation_lock(config.paths.runtime, "server_start"):
        if not config.server.eula:
            raise RecorderError(
                "Minecraft EULA has not been accepted; review https://aka.ms/MinecraftEULA "
                "and set server.eula=true or rerun init with --accept-eula"
            )
        storage_report = check_storage(config)
        jar = prepare_runtime(config, build_mod=True)
        assert jar is not None
        verify_docker()
        arguments = ["up", "--detach", "--remove-orphans"]
        if wait:
            arguments.extend(("--wait", "--wait-timeout", "180"))
        result = _run(compose_command(config, *arguments), capture=capture_output)
        _ensure_success(result, "server start")
        return jar, storage_report


def stop_server(
    config: RecorderConfig,
    *,
    timeout_seconds: int = 120,
    capture_output: bool = False,
) -> StorageReport:
    with operation_lock(config.paths.runtime, "server_stop"):
        _require_prepared_runtime(config)
        (config.paths.runtime / "control").mkdir(parents=True, exist_ok=True)
        write_compose_env(config)
        verify_docker()
        result = _run(
            compose_command(config, "stop", "--timeout", str(timeout_seconds)),
            capture=capture_output,
        )
        _ensure_success(result, "server stop")
        return check_storage(config)


def compose_status(config: RecorderConfig) -> dict[str, object]:
    """Return a stable dashboard status without printing or mutating runtime."""

    if not (config.paths.runtime / "compose.env").is_file():
        return {"state": "unprepared", "services": [], "message": None}
    if not config.paths.compose_file.is_file():
        return {
            "state": "unprepared",
            "services": [],
            "message": _bounded_status_message(
                f"Docker Compose file not found: {config.paths.compose_file}",
                "Docker Compose file not found",
            ),
        }
    try:
        version = _run(["docker", "compose", "version"], capture=True)
        if version.returncode != 0:
            return {
                "state": "docker_unavailable",
                "services": [],
                "message": _bounded_status_message(
                    version.stderr or version.stdout,
                    "Docker Compose check failed",
                ),
            }
        result = _run(
            compose_command(config, "ps", "--all", "--format", "json"),
            capture=True,
            env=_compose_environment(config),
        )
    except RecorderError as exc:
        return {
            "state": "docker_unavailable",
            "services": [],
            "message": _bounded_status_message(exc, "Docker Compose status failed"),
        }
    if result.returncode != 0:
        return {
            "state": "docker_unavailable",
            "services": [],
            "message": _bounded_status_message(
                result.stderr or result.stdout,
                "Docker Compose status failed",
            ),
        }

    raw = (result.stdout or "").strip()
    try:
        if not raw:
            rows: list[dict[str, object]] = []
        elif raw.startswith("["):
            value = json.loads(raw)
            rows = [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []
        else:
            rows = [value for line in raw.splitlines() if isinstance((value := json.loads(line)), dict)]
    except json.JSONDecodeError as exc:
        return {
            "state": "docker_unavailable",
            "services": [],
            "message": _bounded_status_message(
                f"invalid Compose status: {exc.msg}",
                "invalid Compose status",
            ),
        }

    services = []
    for row in rows:
        services.append(
            {
                "service": row.get("Service") or row.get("Name"),
                "state": str(row.get("State") or "unknown").lower(),
                "health": str(row.get("Health") or "").lower() or None,
                "status": row.get("Status"),
                "exit_code": row.get("ExitCode"),
            }
        )
    minecraft = next((item for item in services if item["service"] == "minecraft"), None)
    if minecraft is None:
        state = "stopped"
    elif minecraft["state"] in {"removing", "stopping", "paused"}:
        state = "stopping"
    elif minecraft["state"] in {"created", "restarting"}:
        state = "starting"
    elif minecraft["state"] == "running":
        if minecraft["health"] == "unhealthy":
            state = "unhealthy"
        elif minecraft["health"] in {"starting", None}:
            state = "starting" if minecraft["health"] == "starting" else "running"
        else:
            state = "running"
    elif minecraft["state"] in {"exited", "dead"}:
        exit_code = minecraft["exit_code"]
        state = "stopped" if exit_code in {None, 0, "0"} else "unhealthy"
    else:
        state = "unhealthy"
    return {"state": state, "services": services, "message": None}


def show_status(config: RecorderConfig) -> int:
    _require_prepared_runtime(config)
    verify_docker()
    return _run(compose_command(config, "ps"), env=_compose_environment(config)).returncode


def show_logs(config: RecorderConfig, *, follow: bool, tail: int, service: str) -> int:
    _require_prepared_runtime(config)
    verify_docker()
    arguments = ["logs", "--tail", str(tail)]
    if follow:
        arguments.append("--follow")
    if service != "all":
        arguments.append(service)
    try:
        return _run(compose_command(config, *arguments), env=_compose_environment(config)).returncode
    except KeyboardInterrupt:
        print("", file=sys.stderr)
        return 130
