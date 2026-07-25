from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from minerec.config import ENV_GRADLE_EXECUTABLE, ENV_RENDER_JOB, current_process_environment
from minerec.errors import RecorderError
from minerec.processing.capture import CaptureMetadata, EventsSource, load_capture_metadata, scan_capture_events
from minerec.processing.capture.play import sha256_file
from minerec.processing.replays import verified_replay_source

if TYPE_CHECKING:
    from minerec.config import RecorderConfig


RENDER_JOB_TYPE = "mc-recorder-first-person-render-v1"
OWNER = "mc-recorder"
_FRAME_ARTIFACT_RE = re.compile(r"^frame_[0-9]+\.png$")


@dataclass(frozen=True)
class RenderJobResult:
    directory: Path
    manifest: Path
    replay: Path
    connection_id: str


def _gradle_executable(environment: dict[str, str]) -> str:
    executable = environment.get(ENV_GRADLE_EXECUTABLE, "").strip()
    return executable or "gradle"


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


def _state_range(events: Path, metadata_path: Path) -> tuple[CaptureMetadata, int, int, EventsSource]:
    metadata = load_capture_metadata(metadata_path)
    ticks: list[int] = []

    def visit(record: dict[str, Any]) -> None:
        if record.get("record_type") == "player_state":
            ticks.append(int(record["server_tick"]))

    source = scan_capture_events(events, metadata, visit)
    if not ticks:
        raise RecorderError("capture has no player_state records")
    if ticks != sorted(set(ticks)) or ticks != list(range(ticks[0], ticks[-1] + 1)):
        raise RecorderError("capture player_state timeline is duplicated, unordered, or incomplete")
    return (metadata, ticks[0], ticks[-1], source)


def _owned_render_directory(path: Path) -> bool:
    manifest = path / "render-job.json"
    try:
        value = json.loads(manifest.read_text(encoding="utf-8"))
    except OSError, json.JSONDecodeError:
        return False
    if not isinstance(value, dict) or value.get("owner") != OWNER or value.get("job_type") != RENDER_JOB_TYPE:
        return False
    try:
        entries = list(path.iterdir())
        if not {entry.name for entry in entries} <= {
            "render-job.json",
            "fpv_frames",
            "result.json",
            "result.json.inprogress",
        }:
            return False
        if not (path / "fpv_frames").is_dir() or (path / "fpv_frames").is_symlink():
            return False
        if manifest.is_symlink():
            return False
        for result_name in ("result.json", "result.json.inprogress"):
            result_path = path / result_name
            if result_path.exists() and (result_path.is_symlink() or not result_path.is_file()):
                return False
        if not _owned_render_artifacts(path / "fpv_frames"):
            return False
        expected_frames = (path / "fpv_frames").resolve()
        expected_result = (path / "result.json").resolve()
        return Path(str(value.get("output", ""))).expanduser().resolve() == expected_frames and Path(str(value.get("result", ""))).expanduser().resolve() == expected_result
    except OSError, RuntimeError:
        return False


def _owned_render_artifacts(frames: Path) -> bool:
    allowed_indexes = {
        "frames.jsonl",
        "frames.jsonl.inprogress",
    }
    try:
        for entry in frames.iterdir():
            if entry.is_symlink():
                return False
            if entry.is_file():
                if entry.name not in allowed_indexes and _FRAME_ARTIFACT_RE.fullmatch(entry.name) is None:
                    return False
                continue
            return False
        return True
    except OSError:
        return False


def prepare_render_job(
    metadata: Path,
    events: Path,
    replay: Path,
    output: Path,
    *,
    width: int,
    height: int,
    fps: int,
    first_tick: int | None,
    last_tick: int | None,
    force: bool,
    no_gui: bool = False,
) -> RenderJobResult:
    if not 64 <= width <= 16384 or not 64 <= height <= 16384:
        raise RecorderError("render dimensions must be between 64 and 16384 pixels")
    if fps != 20:
        raise RecorderError("renderer v1 supports exactly 20 FPS")
    if not isinstance(no_gui, bool):
        raise RecorderError("no_gui must be a boolean")
    capture, observed_first, observed_last, events_source = _state_range(events, metadata)
    normalized_player_uuid = capture.player_uuid
    connection = capture.connection_id
    selected_first = observed_first if first_tick is None else first_tick
    selected_last = observed_last if last_tick is None else last_tick
    if selected_first < observed_first or selected_last > observed_last or selected_first > selected_last:
        raise RecorderError(f"render tick range must be within connection range {observed_first}..{observed_last}")

    replay_source = verified_replay_source(
        replay,
        player_uuid=normalized_player_uuid,
        connection_id=connection,
    )
    replay = replay_source.path
    requested_output = output.expanduser()
    if requested_output.is_symlink():
        raise RecorderError(f"render job output may not be a symlink: {requested_output}")
    output = requested_output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists() or output.is_symlink():
        if not force:
            raise RecorderError(f"render job output exists: {output}; pass --force to replace it")
        if output.is_symlink() or not output.is_dir() or not _owned_render_directory(output):
            raise RecorderError(f"refusing to replace non-owned render directory: {output}; choose an empty output path")
        shutil.rmtree(output)

    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}.tmp-", dir=output.parent))
    try:
        frames = staging / "fpv_frames"
        frames.mkdir()
        job: dict[str, Any] = {
            "schema_version": 1,
            "owner": OWNER,
            "job_type": RENDER_JOB_TYPE,
            "status": "prepared",
            "created_at": datetime.now(UTC).isoformat(),
            "replay": str(replay),
            "output": str((output / "fpv_frames").resolve()),
            "result": str((output / "result.json").resolve()),
            "session_id": capture.session_id,
            "connection_id": connection,
            "player_uuid": normalized_player_uuid,
            "replay_id": replay_source.replay_id,
            "global_start_tick": selected_first,
            "global_end_tick": selected_last,
            "width": width,
            "height": height,
            "fps": fps,
            "no_gui": no_gui,
            "stop_when_done": True,
            "capture": {
                "session_id": capture.session_id,
                "metadata": str(capture.path),
                "events": str(events_source.path),
                "events_sha256": events_source.sha256,
                "events_size_bytes": events_source.size_bytes,
                "event_count": events_source.record_count,
            },
            "source_replay": {
                "path": str(replay),
                "format": replay_source.replay_format,
                "sha256": replay_source.sha256,
                "size_bytes": replay_source.size_bytes,
                "replay_id": replay_source.replay_id,
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
                "range_policy": "intersection",
                "alignment": "exact mc_recorder:timeline payload recorded inside Flashback",
                "frame_index_contract": ("frames.jsonl records global server_tick, replay_tick, connection_id, and player_uuid"),
            },
            "camera": {
                "perspective": "first_person_head",
                "width": width,
                "height": height,
            },
            "output_metadata": {
                "frames_directory": str((output / "fpv_frames").resolve()),
                "frame_index": str((output / "fpv_frames" / "frames.jsonl").resolve()),
                "result_manifest": str((output / "result.json").resolve()),
            },
            "renderer_contract": {
                "minecraft_version": "1.21.8",
                "flashback_version": "0.39.5",
                "implementation": "renderer-mod",
                "job_property": "mc.recorder.renderJob",
                "job_environment": ENV_RENDER_JOB,
                "completion": "result.json is atomically published after frames.jsonl is durable",
            },
            "limitations": [
                "The rendered view is reconstructed from server-visible packets, not original client pixels.",
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


def prepare_renderer_runtime(
    config: RecorderConfig,
    *,
    runner: Callable[..., subprocess.CompletedProcess[Any]] = subprocess.run,
) -> None:
    """Resolve the complete client runtime before a remote job is claimed."""
    project = config.mods.renderer_project
    if not project.is_dir():
        raise RecorderError(f"renderer mod project not found: {project}")

    # Respect Gradle's normal user cache (or an operator-provided
    # GRADLE_USER_HOME). Direct builds and renderer workers then share the same
    # verified dependencies instead of downloading a second private copy.
    environment = current_process_environment()
    command = [
        _gradle_executable(environment),
        "--project-dir",
        str(project),
        "prepareRendererRuntime",
        "--no-daemon",
        "--console=plain",
    ]
    try:
        process = runner(
            command,
            cwd=config.paths.base,
            env=environment,
            check=False,
        )
    except OSError as exc:
        raise RecorderError(f"cannot prepare renderer runtime with Gradle: {exc}") from exc
    if process.returncode != 0:
        raise RecorderError(f"renderer runtime preparation failed with exit code {process.returncode}")


def launch_render_job(
    config: RecorderConfig,
    job: RenderJobResult,
    *,
    offline: bool = False,
) -> dict[str, Any]:
    project = config.mods.renderer_project
    if not project.is_dir():
        raise RecorderError(f"renderer mod project not found: {project}")

    job_manifest = _read_job_manifest(job.manifest)
    environment = current_process_environment()
    environment[ENV_RENDER_JOB] = str(job.manifest)
    command = [
        _gradle_executable(environment),
        "--project-dir",
        str(project),
        "runClient",
        "--no-daemon",
        "--console=plain",
    ]
    if offline:
        command.append("--offline")
    try:
        process = subprocess.run(
            command,
            cwd=config.paths.base,
            env=environment,
            check=False,
        )
    except OSError as exc:
        raise RecorderError(f"cannot launch renderer with Gradle: {exc}") from exc

    result_path = job.directory / "result.json"
    result: dict[str, Any] | None = None
    try:
        loaded = json.loads(result_path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            result = loaded
    except OSError, json.JSONDecodeError:
        pass
    if process.returncode != 0:
        detail = f": {result.get('error')}" if result and result.get("error") else ""
        raise RecorderError(f"renderer client exited with code {process.returncode}{detail}")
    if result is None or result.get("status") not in {"complete", "no_coverage"}:
        raise RecorderError(f"renderer exited without an atomic terminal result at {result_path}")
    expected_no_gui = job_manifest.get("no_gui", True)
    result_no_gui = result.get("no_gui", True)
    if not isinstance(expected_no_gui, bool) or not isinstance(result_no_gui, bool) or result_no_gui != expected_no_gui:
        raise RecorderError("renderer result no_gui does not match the render job")
    if result.get("status") == "no_coverage":
        timeline = job_manifest.get("timeline")
        if not isinstance(timeline, dict) or timeline.get("range_policy") != "intersection":
            raise RecorderError("renderer reported no coverage for a non-intersection render job")
    source_replay = job_manifest.get("source_replay")
    if not isinstance(source_replay, dict):
        raise RecorderError(f"render job lost its replay integrity envelope: {job.manifest}")
    expected_sha = source_replay.get("sha256")
    expected_bytes = source_replay.get("size_bytes")
    actual_sha, actual_bytes = _stable_file_digest(job.replay, "replay archive")
    if result.get("replay_sha256") != expected_sha or result.get("replay_bytes") != expected_bytes or actual_sha != expected_sha or actual_bytes != expected_bytes:
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
