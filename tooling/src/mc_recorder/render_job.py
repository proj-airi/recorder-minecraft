from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import uuid
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .episodes import iter_epochs, iter_events, sha256_file, validate_episode
from .errors import RecorderError

if TYPE_CHECKING:
    from .config import RecorderConfig


RENDER_JOB_TYPE = "mc-recorder-first-person-render-v1"
OWNER = "mc-recorder"
_FRAME_ARTIFACT_RE = re.compile(r"^frame_[0-9]+\.png$")
_VOXEL_ARTIFACT_RE = re.compile(r"^voxel_[0-9]+\.json\.gz(?:\.inprogress)?$")


@dataclass(frozen=True)
class RenderJobResult:
    directory: Path
    manifest: Path
    replay: Path
    connection_id: str


def _detect_replay_format(path: Path) -> str:
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
    except (OSError, zipfile.BadZipFile) as exc:
        raise RecorderError(f"replay is not a readable ZIP/MCPR archive: {path}") from exc
    if "metadata.json" in names and any(name.endswith(".flashback") for name in names):
        return "flashback"
    if "metaData.json" in names and "recording.tmcpr" in names:
        return "replay_mod"
    raise RecorderError(f"unrecognized ServerReplay archive structure: {path}")


def _stable_file_digest(path: Path, description: str) -> tuple[str, int]:
    try:
        before = path.stat()
        digest = sha256_file(path)
        after = path.stat()
    except OSError as exc:
        raise RecorderError(f"cannot read {description}: {path}") from exc
    identity_before = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
    identity_after = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    if identity_before != identity_after:
        raise RecorderError(f"{description} changed while it was being hashed: {path}")
    return digest, after.st_size


def resolve_replay(replays_root: Path, player_uuid: str, explicit: Path | None) -> Path:
    try:
        normalized_player_uuid = str(uuid.UUID(player_uuid))
    except (ValueError, AttributeError) as exc:
        raise RecorderError(f"invalid player UUID: {player_uuid!r}") from exc
    if explicit is not None:
        unresolved = explicit.expanduser()
        if unresolved.is_symlink():
            raise RecorderError(f"replay not found or is symlinked: {unresolved}")
        path = unresolved.resolve()
        if not path.is_file():
            raise RecorderError(f"replay not found or is symlinked: {path}")
        return path
    player_dir = replays_root / "players" / normalized_player_uuid
    if not player_dir.is_dir() or player_dir.is_symlink():
        raise RecorderError(
            f"no replay directory found for player {normalized_player_uuid}; pass --replay PATH"
        )
    candidates = sorted(
        (
            path
            for path in player_dir.iterdir()
            if path.is_file() and not path.is_symlink() and path.suffix.lower() in {".zip", ".mcpr"}
        ),
        key=lambda path: path.stat().st_mtime_ns,
        reverse=True,
    )
    if not candidates:
        raise RecorderError(
            f"no completed replay found for player {normalized_player_uuid}; pass --replay PATH"
        )
    if len(candidates) > 1:
        raise RecorderError(
            f"multiple replay segments exist for player {normalized_player_uuid}; "
            "pass --replay PATH to select one"
        )
    return candidates[0]


def _player_connections(episode: Path, player_uuid: str) -> dict[str, tuple[int, int]]:
    ticks: dict[str, list[int]] = {}
    for epoch in iter_epochs(episode):
        if epoch.status != "sealed":
            continue
        for _line, record in iter_events(epoch):
            if record.get("record_type") != "player_state" or record.get("player_uuid") != player_uuid:
                continue
            connection = record.get("connection_id")
            tick = record.get("server_tick")
            if isinstance(connection, str) and isinstance(tick, int) and not isinstance(tick, bool):
                ticks.setdefault(connection, []).append(tick)
    return {connection: (min(values), max(values)) for connection, values in ticks.items() if values}


def _select_connection(
    episode: Path, player_uuid: str, requested: str | None
) -> tuple[str, int, int]:
    connections = _player_connections(episode, player_uuid)
    if not connections:
        raise RecorderError(
            f"player {player_uuid} has no connection-tagged state records in sealed epochs"
        )
    if requested is not None:
        if requested not in connections:
            raise RecorderError(f"connection {requested!r} does not belong to player {player_uuid}")
        first, last = connections[requested]
        return requested, first, last
    if len(connections) != 1:
        choices = ", ".join(sorted(connections))
        raise RecorderError(f"player has multiple connections ({choices}); pass --connection ID")
    connection, (first, last) = next(iter(connections.items()))
    return connection, first, last


def _owned_render_directory(path: Path) -> bool:
    manifest = path / "render-job.json"
    try:
        value = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if (
        not isinstance(value, dict)
        or value.get("owner") != OWNER
        or value.get("job_type") != RENDER_JOB_TYPE
    ):
        return False
    try:
        entries = list(path.iterdir())
        if not {entry.name for entry in entries} <= {
            "render-job.json",
            "frames",
            "result.json",
            "result.json.inprogress",
        }:
            return False
        if not (path / "frames").is_dir() or (path / "frames").is_symlink():
            return False
        if manifest.is_symlink():
            return False
        for result_name in ("result.json", "result.json.inprogress"):
            result_path = path / result_name
            if result_path.exists() and (result_path.is_symlink() or not result_path.is_file()):
                return False
        if not _owned_render_artifacts(path / "frames"):
            return False
        expected_frames = (path / "frames").resolve()
        expected_result = (path / "result.json").resolve()
        return (
            Path(str(value.get("output", ""))).expanduser().resolve() == expected_frames
            and Path(str(value.get("result", ""))).expanduser().resolve() == expected_result
        )
    except (OSError, RuntimeError):
        return False


def _owned_render_artifacts(frames: Path) -> bool:
    allowed_indexes = {
        "frames.jsonl",
        "frames.jsonl.inprogress",
        "voxels.jsonl",
        "voxels.jsonl.inprogress",
    }
    try:
        for entry in frames.iterdir():
            if entry.is_symlink():
                return False
            if entry.is_file():
                if entry.name not in allowed_indexes and _FRAME_ARTIFACT_RE.fullmatch(entry.name) is None:
                    return False
                continue
            if entry.name != "voxels" or not entry.is_dir():
                return False
            for voxel in entry.iterdir():
                if (
                    voxel.is_symlink()
                    or not voxel.is_file()
                    or _VOXEL_ARTIFACT_RE.fullmatch(voxel.name) is None
                ):
                    return False
        return True
    except OSError:
        return False


def prepare_render_job(
    episode: Path,
    replay: Path,
    output: Path,
    *,
    player_uuid: str,
    connection_id: str | None,
    width: int,
    height: int,
    fps: int,
    first_tick: int | None,
    last_tick: int | None,
    force: bool,
    voxel_horizontal_radius: int = 0,
    voxel_vertical_radius: int = 0,
) -> RenderJobResult:
    try:
        normalized_player_uuid = str(uuid.UUID(player_uuid))
    except ValueError as exc:
        raise RecorderError(f"invalid player UUID: {player_uuid!r}") from exc
    if not 64 <= width <= 16384 or not 64 <= height <= 16384:
        raise RecorderError("render dimensions must be between 64 and 16384 pixels")
    if fps != 20:
        raise RecorderError("renderer v1 supports exactly 20 FPS")
    if (voxel_horizontal_radius == 0) != (voxel_vertical_radius == 0):
        raise RecorderError("voxel radii must both be zero or both be positive")
    if not 0 <= voxel_horizontal_radius <= 64 or not 0 <= voxel_vertical_radius <= 64:
        raise RecorderError("voxel radii must be between 0 and 64 blocks")
    voxel_cells = (
        (voxel_horizontal_radius * 2 + 1)
        * (voxel_horizontal_radius * 2 + 1)
        * (voxel_vertical_radius * 2 + 1)
    )
    if voxel_horizontal_radius and voxel_cells > 2_000_000:
        raise RecorderError("voxel crop is too large; maximum is 2,000,000 cells per tick")
    validation = validate_episode(episode)
    if not validation.valid or validation.sealed_epochs == 0:
        raise RecorderError("episode must have at least one valid sealed epoch before rendering")
    connection, observed_first, observed_last = _select_connection(
        episode, normalized_player_uuid, connection_id
    )
    selected_first = observed_first if first_tick is None else first_tick
    selected_last = observed_last if last_tick is None else last_tick
    if selected_first < observed_first or selected_last > observed_last or selected_first > selected_last:
        raise RecorderError(
            f"render tick range must be within connection range {observed_first}..{observed_last}"
        )

    unresolved_replay = replay.expanduser()
    if unresolved_replay.is_symlink() or not unresolved_replay.is_file():
        raise RecorderError(f"replay not found or is symlinked: {unresolved_replay}")
    replay = unresolved_replay.resolve()
    replay_format = _detect_replay_format(replay)
    if replay_format != "flashback":
        raise RecorderError("the current renderer supports Flashback replay ZIPs only")
    replay_sha256, replay_bytes = _stable_file_digest(replay, "replay archive")
    requested_output = output.expanduser()
    if requested_output.is_symlink():
        raise RecorderError(f"render job output may not be a symlink: {requested_output}")
    output = requested_output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists() or output.is_symlink():
        if not force:
            raise RecorderError(f"render job output exists: {output}; pass --force to replace it")
        if output.is_symlink() or not output.is_dir() or not _owned_render_directory(output):
            raise RecorderError(
                f"refusing to replace non-owned render directory: {output}; choose an empty output path"
            )
        shutil.rmtree(output)

    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}.tmp-", dir=output.parent))
    try:
        frames = staging / "frames"
        frames.mkdir()
        job: dict[str, Any] = {
            "schema_version": 1,
            "owner": OWNER,
            "job_type": RENDER_JOB_TYPE,
            "status": "prepared",
            "created_at": datetime.now(UTC).isoformat(),
            "replay": str(replay),
            "output": str((output / "frames").resolve()),
            "result": str((output / "result.json").resolve()),
            "session_id": validation.session_id,
            "connection_id": connection,
            "player_uuid": normalized_player_uuid,
            "global_start_tick": selected_first,
            "global_end_tick": selected_last,
            "width": width,
            "height": height,
            "fps": fps,
            "voxel_horizontal_radius": voxel_horizontal_radius,
            "voxel_vertical_radius": voxel_vertical_radius,
            "no_gui": True,
            "stop_when_done": True,
            "episode": {
                "session_id": validation.session_id,
                "path": str(episode.resolve()),
                "manifest_sha256": sha256_file(episode / "manifest.json"),
            },
            "source_replay": {
                "path": str(replay),
                "format": replay_format,
                "sha256": replay_sha256,
                "size_bytes": replay_bytes,
            },
            "subject": {
                "player_uuid": normalized_player_uuid,
                "connection_id": connection,
            },
            "timeline": {
                "from_global_server_tick": selected_first,
                "to_global_server_tick": selected_last,
                "observed_connection_range": [observed_first, observed_last],
                "server_tick_rate_hz": 20,
                "output_fps": fps,
                "alignment": "exact mc_recorder:timeline payload recorded inside Flashback",
                "frame_index_contract": (
                    "frames.jsonl records global server_tick, replay_tick, connection_id, and player_uuid"
                ),
            },
            "camera": {
                "perspective": "first_person_head",
                "width": width,
                "height": height,
            },
            "voxel_crop": {
                "enabled": voxel_horizontal_radius > 0,
                "horizontal_radius": voxel_horizontal_radius,
                "vertical_radius": voxel_vertical_radius,
                "cells_per_tick": voxel_cells if voxel_horizontal_radius > 0 else 0,
                "format": "mc-recorder-voxel-palette-v1",
                "coverage": "explicit bitset; uncovered cells are unknown, not air",
            },
            "output_metadata": {
                "frames_directory": str((output / "frames").resolve()),
                "frame_index": str((output / "frames" / "frames.jsonl").resolve()),
                "result_manifest": str((output / "result.json").resolve()),
                "voxel_index": str((output / "frames" / "voxels.jsonl").resolve()),
            },
            "renderer_contract": {
                "minecraft_version": "1.21.8",
                "flashback_version": "0.39.5",
                "implementation": "renderer-mod",
                "job_property": "mc.recorder.renderJob",
                "job_environment": "MC_RECORDER_RENDER_JOB",
                "completion": "result.json is atomically published after frames.jsonl is durable",
            },
            "limitations": [
                "The rendered view is reconstructed from server-visible packets, not original client pixels.",
                "A five-minute ServerReplay segment may cover only part of a longer player connection.",
                "Voxel V1 materializes block states but not block-entity data; the replay remains source.",
            ],
        }
        manifest = staging / "render-job.json"
        manifest.write_text(json.dumps(job, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        staging.rename(output)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        raise
    return RenderJobResult(
        directory=output,
        manifest=output / "render-job.json",
        replay=replay,
        connection_id=connection,
    )


def launch_render_job(config: RecorderConfig, job: RenderJobResult) -> dict[str, Any]:
    project = config.mods.renderer_project
    if not project.is_dir():
        raise RecorderError(f"renderer mod project not found: {project}")
    wrapper = config.paths.base / "gradlew"
    if not wrapper.is_file():
        raise RecorderError(f"Gradle wrapper not found: {wrapper}")

    environment = dict(os.environ)
    environment["MC_RECORDER_RENDER_JOB"] = str(job.manifest)
    gradle_cache = config.paths.runtime / "gradle-cache"
    gradle_cache.mkdir(parents=True, exist_ok=True)
    environment["GRADLE_USER_HOME"] = str(gradle_cache)
    try:
        process = subprocess.run(
            [
                str(wrapper),
                "--project-dir",
                str(project),
                "runClient",
                "--no-daemon",
                "--console=plain",
            ],
            cwd=config.paths.base,
            env=environment,
            check=False,
        )
    except FileNotFoundError as exc:
        raise RecorderError(f"cannot launch renderer with {wrapper}") from exc

    result_path = job.directory / "result.json"
    result: dict[str, Any] | None = None
    try:
        loaded = json.loads(result_path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            result = loaded
    except (OSError, json.JSONDecodeError):
        pass
    if process.returncode != 0:
        detail = f": {result.get('error')}" if result and result.get("error") else ""
        raise RecorderError(f"renderer client exited with code {process.returncode}{detail}")
    if result is None or result.get("status") != "complete":
        raise RecorderError(f"renderer exited without an atomic complete result at {result_path}")
    job_manifest = _read_job_manifest(job.manifest)
    source_replay = job_manifest.get("source_replay")
    if not isinstance(source_replay, dict):
        raise RecorderError(f"render job lost its replay integrity envelope: {job.manifest}")
    expected_sha = source_replay.get("sha256")
    expected_bytes = source_replay.get("size_bytes")
    actual_sha, actual_bytes = _stable_file_digest(job.replay, "replay archive")
    if (
        result.get("replay_sha256") != expected_sha
        or result.get("replay_bytes") != expected_bytes
        or actual_sha != expected_sha
        or actual_bytes != expected_bytes
    ):
        raise RecorderError("replay integrity does not match the completed renderer result")
    return result


def _read_job_manifest(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RecorderError(f"cannot read render job manifest: {path}") from exc
    if not isinstance(value, dict):
        raise RecorderError(f"render job manifest is not an object: {path}")
    return value
