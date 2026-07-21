from __future__ import annotations

import json
import fcntl
import os
import shutil
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable

from .config import RecorderConfig
from .episodes import iter_epochs, iter_events, sha256_file, validate_episode
from .errors import RecorderError
from .render_sources import ReplaySegmentSource, resolve_replay_segments


SCENE_JOB_TYPE = "mc-recorder-scene-extraction-job-v1"
SCENE_RESULT_TYPE = "mc-recorder-scene-extraction-result-v1"
SCENE_STREAM_FORMAT = "mc-recorder-scene-stream-v1"
MAX_RESULT_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True)
class SceneJob:
    directory: Path
    manifest: Path
    result: Path
    stream: Path
    job_id: str
    session_id: str
    player_uuid: str
    connection_id: str
    first_tick: int
    last_tick: int
    state_ticks: tuple[int, ...]
    sources: tuple[ReplaySegmentSource, ...]


def _canonical_uuid(value: object, label: str) -> str:
    try:
        canonical = str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError) as exc:
        raise RecorderError(f"invalid {label}: {value!r}") from exc
    if value != canonical:
        raise RecorderError(f"{label} must use canonical UUID spelling")
    return canonical


def subject_state_ticks(
    episode: Path,
    *,
    player_uuid: str,
    connection_id: str,
    first_tick: int | None = None,
    last_tick: int | None = None,
) -> tuple[int, ...]:
    """Return the exact selected player-state timeline from immutable epochs."""

    player = _canonical_uuid(player_uuid, "player UUID")
    connection = _canonical_uuid(connection_id, "connection UUID")
    ticks: list[int] = []
    for epoch in iter_epochs(episode):
        if epoch.status != "sealed":
            continue
        for _line, record in iter_events(epoch):
            if (
                record.get("record_type") != "player_state"
                or record.get("player_uuid") != player
                or record.get("connection_id") != connection
            ):
                continue
            tick = record.get("server_tick")
            if not isinstance(tick, int) or isinstance(tick, bool) or tick < 0:
                raise RecorderError("player_state has an invalid server_tick")
            if first_tick is not None and tick < first_tick:
                continue
            if last_tick is not None and tick > last_tick:
                continue
            ticks.append(tick)
    if not ticks:
        raise RecorderError("the selected subject has no player_state ticks in sealed epochs")
    if ticks != sorted(set(ticks)):
        raise RecorderError("the selected subject timeline is duplicated or out of order")
    return tuple(ticks)


def _owned_scene_job(path: Path) -> bool:
    manifest = path / "scene-job.json"
    try:
        value = json.loads(manifest.read_text(encoding="utf-8"))
        names = {entry.name for entry in path.iterdir()}
    except (OSError, json.JSONDecodeError):
        return False
    return (
        not path.is_symlink()
        and not manifest.is_symlink()
        and isinstance(value, dict)
        and value.get("schema_version") == 1
        and isinstance(value.get("job_id"), str)
        and names <= {"scene-job.json", "stream", "result.json", "result.json.inprogress"}
    )


def prepare_scene_job(
    config: RecorderConfig,
    episode: Path,
    *,
    player_uuid: str,
    connection_id: str,
    first_tick: int | None = None,
    last_tick: int | None = None,
    output: Path | None = None,
    force: bool = False,
) -> SceneJob:
    validation = validate_episode(episode)
    if not validation.valid or validation.sealed_epochs == 0:
        raise RecorderError("episode must have at least one valid sealed epoch before scene extraction")
    player = _canonical_uuid(player_uuid, "player UUID")
    connection = _canonical_uuid(connection_id, "connection UUID")
    ticks = subject_state_ticks(
        episode,
        player_uuid=player,
        connection_id=connection,
        first_tick=first_tick,
        last_tick=last_tick,
    )
    selected_first = ticks[0]
    selected_last = ticks[-1]

    sources = tuple(
        resolve_replay_segments(
            control_root=config.paths.runtime / "control",
            replays_root=config.paths.replays,
            session_id=validation.session_id,
            player_uuid=player,
            connection_id=connection,
        )
    )
    job_id = str(uuid.uuid4())
    requested = output or (config.paths.runtime / "scene-jobs" / job_id)
    unresolved = requested.expanduser()
    if unresolved.is_symlink():
        raise RecorderError(f"scene job output may not be a symlink: {unresolved}")
    directory = unresolved.resolve()
    directory.parent.mkdir(parents=True, exist_ok=True)
    if directory.exists() or directory.is_symlink():
        if not force:
            raise RecorderError(f"scene job output exists: {directory}; pass --force to replace it")
        if directory.is_symlink() or not directory.is_dir() or not _owned_scene_job(directory):
            raise RecorderError(f"refusing to replace non-owned scene job directory: {directory}")
        shutil.rmtree(directory)

    staging = Path(tempfile.mkdtemp(prefix=f".{directory.name}.tmp-", dir=directory.parent))
    try:
        final_stream = directory / "stream"
        result_path = directory / "result.json"
        manifest_value = {
            "schema_version": 1,
            "job_id": job_id,
            "session_id": validation.session_id,
            "subject": {
                "player_uuid": player,
                "connection_id": connection,
            },
            "global_start_tick": selected_first,
            "global_end_tick": selected_last,
            "scope": "client_visible",
            "metadata_policy": "full_packet_metadata",
            "source_replays": [
                {
                    "segment_id": source.segment_id,
                    "segment_ordinal": source.segment_ordinal,
                    "path": str(source.path),
                    "sha256": source.sha256,
                    "size_bytes": source.size_bytes,
                    "format": source.replay_format,
                }
                for source in sources
            ],
            "output": str(final_stream),
            "stop_when_done": True,
        }
        manifest = staging / "scene-job.json"
        manifest.write_text(
            json.dumps(manifest_value, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        staging.rename(directory)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        raise

    return SceneJob(
        directory=directory,
        manifest=directory / "scene-job.json",
        result=directory / "result.json",
        stream=directory / "stream",
        job_id=job_id,
        session_id=validation.session_id,
        player_uuid=player,
        connection_id=connection,
        first_tick=selected_first,
        last_tick=selected_last,
        state_ticks=ticks,
        sources=sources,
    )


def _read_terminal_result(path: Path) -> dict[str, Any]:
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_RESULT_BYTES:
            raise RecorderError(f"scene extractor did not publish a regular bounded result: {path}")
        value = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise RecorderError(f"cannot read scene extractor result: {path}") from exc
    except (json.JSONDecodeError, RecursionError) as exc:
        raise RecorderError(f"scene extractor result is invalid JSON: {path}") from exc
    if not isinstance(value, dict):
        raise RecorderError("scene extractor result must be an object")
    return value


def _validate_result(job: SceneJob, value: dict[str, Any]) -> None:
    if value.get("schema_version") != 1 or value.get("result_type") != SCENE_RESULT_TYPE:
        raise RecorderError("scene extractor published an unsupported result contract")
    error = value.get("error")
    if value.get("status") != "complete":
        raise RecorderError(f"scene extraction failed: {error or value.get('status') or 'unknown error'}")
    expected = {
        "job_id": job.job_id,
        "session_id": job.session_id,
        "player_uuid": job.player_uuid,
        "connection_id": job.connection_id,
        "global_start_tick": job.first_tick,
        "global_end_tick": job.last_tick,
    }
    if any(value.get(key) != expected_value for key, expected_value in expected.items()):
        raise RecorderError("scene extractor result identity does not match its job")
    stream = value.get("stream")
    if (
        not isinstance(stream, dict)
        or stream.get("format") != SCENE_STREAM_FORMAT
        or Path(str(stream.get("path", ""))).resolve() != job.stream
        or stream.get("frames_index") != "frames.jsonl"
        or stream.get("changes_index") != "changes.jsonl"
        or not isinstance(stream.get("frame_count"), int)
        or isinstance(stream.get("frame_count"), bool)
    ):
        raise RecorderError("scene extractor result has an invalid stream envelope")


def _assert_sources_unchanged(sources: Iterable[ReplaySegmentSource]) -> None:
    for source in sources:
        try:
            size = source.path.stat().st_size
            digest = sha256_file(source.path)
        except OSError as exc:
            raise RecorderError(f"source replay disappeared after extraction: {source.path}") from exc
        if size != source.size_bytes or digest != source.sha256:
            raise RecorderError(f"source replay changed during extraction: {source.path}")


@contextmanager
def _locked_sources(sources: Iterable[ReplaySegmentSource]):
    handles = []
    try:
        for source in sources:
            handle = None
            try:
                handle = source.path.open("rb")
                fcntl.flock(handle.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)
            except (OSError, BlockingIOError) as exc:
                if handle is not None:
                    handle.close()
                raise RecorderError(
                    f"source replay is being retained or evicted by another operation: {source.path}"
                ) from exc
            handles.append(handle)
        yield
    finally:
        for handle in reversed(handles):
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            finally:
                handle.close()


def launch_scene_job(
    config: RecorderConfig,
    job: SceneJob,
    *,
    capture_output: bool = False,
) -> dict[str, Any]:
    project = config.mods.scene_extractor_project
    if not project.is_dir():
        raise RecorderError(f"scene extractor mod project not found: {project}")
    wrapper = config.paths.base / "gradlew"
    if not wrapper.is_file():
        raise RecorderError(f"Gradle wrapper not found: {wrapper}")
    if not config.server.eula:
        raise RecorderError("scene extraction requires the configured Minecraft EULA acceptance")
    run_directory = project / "run"
    run_directory.mkdir(parents=True, exist_ok=True)
    eula = run_directory / "eula.txt"
    if eula.is_symlink():
        raise RecorderError(f"scene extractor EULA file may not be a symlink: {eula}")
    eula.write_text("eula=true\n", encoding="utf-8")

    environment = dict(os.environ)
    environment["MC_RECORDER_SCENE_JOB"] = str(job.manifest)
    gradle_cache = config.paths.runtime / "gradle-cache"
    gradle_cache.mkdir(parents=True, exist_ok=True)
    environment["GRADLE_USER_HOME"] = str(gradle_cache)
    with _locked_sources(job.sources):
        _assert_sources_unchanged(job.sources)
        try:
            process = subprocess.run(
                [
                    str(wrapper),
                    "--project-dir",
                    str(project),
                    "runServer",
                    "--no-daemon",
                    "--console=plain",
                ],
                cwd=config.paths.base,
                env=environment,
                check=False,
                text=True,
                stdout=subprocess.PIPE if capture_output else None,
                stderr=subprocess.PIPE if capture_output else None,
            )
        except FileNotFoundError as exc:
            raise RecorderError(f"cannot launch scene extractor with {wrapper}") from exc

        value = _read_terminal_result(job.result)
        if process.returncode != 0:
            detail = value.get("error")
            if not detail and capture_output and process.stderr:
                detail = process.stderr.strip()[-2048:]
            raise RecorderError(
                f"scene extractor exited with code {process.returncode}"
                + (f": {detail}" if detail else "")
            )
        _validate_result(job, value)
        _assert_sources_unchanged(job.sources)
        return value


__all__ = [
    "SCENE_JOB_TYPE",
    "SCENE_RESULT_TYPE",
    "SCENE_STREAM_FORMAT",
    "SceneJob",
    "launch_scene_job",
    "prepare_scene_job",
    "subject_state_ticks",
]
