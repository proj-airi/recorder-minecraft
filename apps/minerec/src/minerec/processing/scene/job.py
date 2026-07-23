from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from minerec.config import ENV_SCENE_EXTRACTOR_EXECUTABLE, RecorderConfig, current_process_environment
from minerec.errors import RecorderError
from minerec.processing.capture.episodes import (
    EpochInfo,
    inspect_epoch,
    iter_epochs,
    sha256_file,
    validate_episode,
)
from minerec.processing.scene.integrity import (
    SceneStreamIntegrity,
    SceneStreamIntegrityError,
    VerifiedSceneStream,
    freeze_json_value,
    scene_stream_integrity_from_mapping,
    verify_scene_stream,
)
from minerec.render.control.sources import (
    FLASHBACK_CAPTURE_CONTRACT,
    ReplaySegmentSource,
    resolve_replay_segments,
)

SCENE_JOB_TYPE = "mc-recorder-scene-extraction-job-v1"
SCENE_RESULT_TYPE = "mc-recorder-scene-extraction-result-v1"
SCENE_STREAM_FORMAT = "mc-recorder-scene-stream-v1"
MAX_RESULT_BYTES = 8 * 1024 * 1024
SCENE_JOB_OWNER_TYPE = "mc-recorder-owned-scene-job-v1"
SUBJECT_POSE_FORMAT = "mc-recorder-subject-poses-v1"
SUBJECT_POSE_FILE = "subject-poses.jsonl"
MAX_SUBJECT_POSE_BYTES = 256 * 1024 * 1024
MAX_SOURCE_EVENT_LINE_BYTES = 64 * 1024 * 1024
_RESOURCE_LOCATION = re.compile(r"[a-z0-9_.-]+:[a-z0-9/._-]+")


@dataclass(frozen=True)
class SubjectPoseSourceEpoch:
    epoch_index: int
    events_sha256: str
    events_size_bytes: int
    record_count: int

    def as_dict(self) -> dict[str, object]:
        return {
            "epoch_index": self.epoch_index,
            "events_sha256": self.events_sha256,
            "events_size_bytes": self.events_size_bytes,
            "record_count": self.record_count,
        }


@dataclass(frozen=True)
class SubjectPoseEnvelope:
    path: Path
    sha256: str
    size_bytes: int
    record_count: int
    first_tick: int
    last_tick: int
    source_epochs: tuple[SubjectPoseSourceEpoch, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "format": SUBJECT_POSE_FORMAT,
            "path": str(self.path),
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "record_count": self.record_count,
            "first_tick": self.first_tick,
            "last_tick": self.last_tick,
            "source_epochs": [source.as_dict() for source in self.source_epochs],
        }


@dataclass(frozen=True)
class _SubjectPoseSelection:
    data: bytes
    ticks: tuple[int, ...]
    source_epochs: tuple[SubjectPoseSourceEpoch, ...]


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
    subject_poses: SubjectPoseEnvelope


@dataclass(frozen=True)
class _TerminalResult:
    value: dict[str, Any]
    sha256: str


def _canonical_uuid(value: object, label: str) -> str:
    try:
        canonical = str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError) as exc:
        raise RecorderError(f"invalid {label}: {value!r}") from exc
    if value != canonical:
        raise RecorderError(f"{label} must use canonical UUID spelling")
    return canonical


def _finite_number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RecorderError(f"player_state {label} must be a finite number")
    try:
        normalized = float(value)
    except (OverflowError, ValueError) as exc:
        raise RecorderError(f"player_state {label} must be a finite number") from exc
    if not math.isfinite(normalized):
        raise RecorderError(f"player_state {label} must be a finite number")
    return normalized


def _vector(value: object, label: str) -> dict[str, float]:
    if not isinstance(value, dict):
        raise RecorderError(f"player_state {label} must be an object")
    return {axis: _finite_number(value.get(axis), f"{label}.{axis}") for axis in ("x", "y", "z")}


def _subject_pose_record(
    record: dict[str, Any],
    *,
    session_id: str,
    player_uuid: str,
    connection_id: str,
) -> dict[str, object]:
    if record.get("session_id") != session_id:
        raise RecorderError("player_state session_id does not match the selected episode")
    tick = record.get("server_tick")
    if not isinstance(tick, int) or isinstance(tick, bool) or tick < 0:
        raise RecorderError("player_state has an invalid server_tick")
    entity_id = record.get("entity_id")
    if not isinstance(entity_id, int) or isinstance(entity_id, bool) or entity_id < 0 or entity_id > 2_147_483_647:
        raise RecorderError("player_state has an invalid entity_id")
    dimension = record.get("dimension")
    if not isinstance(dimension, str) or len(dimension) > 32_767 or _RESOURCE_LOCATION.fullmatch(dimension) is None:
        raise RecorderError("player_state has an invalid dimension")
    rotation = record.get("rotation")
    if not isinstance(rotation, dict):
        raise RecorderError("player_state rotation must be an object")
    on_ground = record.get("on_ground")
    if not isinstance(on_ground, bool):
        raise RecorderError("player_state on_ground must be boolean")
    return {
        "schema_version": 1,
        "server_tick": tick,
        "session_id": session_id,
        "player_uuid": player_uuid,
        "connection_id": connection_id,
        "entity_id": entity_id,
        "dimension": dimension,
        "position": _vector(record.get("position"), "position"),
        "velocity": _vector(record.get("velocity"), "velocity"),
        "yaw": _finite_number(rotation.get("yaw"), "rotation.yaw"),
        "pitch": _finite_number(rotation.get("pitch"), "rotation.pitch"),
        "head_yaw": _finite_number(rotation.get("head_yaw"), "rotation.head_yaw"),
        "on_ground": on_ground,
    }


def _epoch_subject_poses(
    epoch: EpochInfo,
    *,
    session_id: str,
    player_uuid: str,
    connection_id: str,
    first_tick: int | None,
    last_tick: int | None,
) -> tuple[list[dict[str, object]], SubjectPoseSourceEpoch]:
    if epoch.status != "sealed" or epoch.event_count is None or epoch.events_bytes is None or epoch.events_sha256 is None:
        raise RecorderError(f"subject poses require a verified sealed epoch: {epoch.path}")
    events = epoch.path / "events.jsonl"
    if events.is_symlink() or not events.is_file():
        raise RecorderError(f"sealed epoch events must be a regular non-symlink file: {events}")
    digest = hashlib.sha256()
    byte_count = 0
    record_count = 0
    poses: list[dict[str, object]] = []
    try:
        with events.open("rb") as handle:
            before = os.fstat(handle.fileno())
            for line_number, raw_line in enumerate(handle, 1):
                if len(raw_line) > MAX_SOURCE_EVENT_LINE_BYTES:
                    raise RecorderError(f"{events}:{line_number}: event exceeds the size limit")
                digest.update(raw_line)
                byte_count += len(raw_line)
                if not raw_line.strip():
                    raise RecorderError(f"{events}:{line_number}: sealed event cannot be empty")
                record_count += 1
                try:
                    record = json.loads(raw_line)
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise RecorderError(f"{events}:{line_number}: invalid JSON") from exc
                if not isinstance(record, dict):
                    raise RecorderError(f"{events}:{line_number}: event must be an object")
                if record.get("record_type") != "player_state" or record.get("player_uuid") != player_uuid or record.get("connection_id") != connection_id:
                    continue
                tick = record.get("server_tick")
                if not isinstance(tick, int) or isinstance(tick, bool) or tick < 0:
                    raise RecorderError(f"{events}:{line_number}: player_state has an invalid server_tick")
                if first_tick is not None and tick < first_tick:
                    continue
                if last_tick is not None and tick > last_tick:
                    continue
                poses.append(
                    _subject_pose_record(
                        record,
                        session_id=session_id,
                        player_uuid=player_uuid,
                        connection_id=connection_id,
                    )
                )
            after = os.fstat(handle.fileno())
        path_after = events.stat()
    except OSError as exc:
        raise RecorderError(f"cannot read sealed epoch events: {events}") from exc
    identities = {
        (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns),
        (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns),
        (path_after.st_dev, path_after.st_ino, path_after.st_size, path_after.st_mtime_ns),
    }
    if len(identities) != 1:
        raise RecorderError(f"sealed epoch events changed while reading subject poses: {events}")
    if byte_count != epoch.events_bytes or record_count != epoch.event_count or digest.hexdigest() != epoch.events_sha256:
        raise RecorderError(f"sealed epoch integrity changed while reading subject poses: {events}")
    return poses, SubjectPoseSourceEpoch(
        epoch_index=epoch.index,
        events_sha256=epoch.events_sha256,
        events_size_bytes=epoch.events_bytes,
        record_count=epoch.event_count,
    )


def _select_subject_poses(
    *,
    session_id: str,
    player_uuid: str,
    connection_id: str,
    first_tick: int | None = None,
    last_tick: int | None = None,
    epochs: Iterable[EpochInfo],
) -> _SubjectPoseSelection:
    """Build canonical pose values from one immutable, integrity-bound epoch snapshot."""

    player = _canonical_uuid(player_uuid, "player UUID")
    connection = _canonical_uuid(connection_id, "connection UUID")
    encoded = bytearray()
    ticks: list[int] = []
    sources: list[SubjectPoseSourceEpoch] = []
    for epoch in epochs:
        if epoch.status != "sealed":
            continue
        poses, source = _epoch_subject_poses(
            epoch,
            session_id=session_id,
            player_uuid=player,
            connection_id=connection,
            first_tick=first_tick,
            last_tick=last_tick,
        )
        if poses:
            for pose in poses:
                line = _encode_subject_pose(pose)
                if len(encoded) + len(line) > MAX_SUBJECT_POSE_BYTES:
                    raise RecorderError(f"subject pose stream exceeds the {MAX_SUBJECT_POSE_BYTES}-byte safety limit")
                encoded.extend(line)
                ticks.append(int(pose["server_tick"]))  # ty:ignore[invalid-argument-type]
            sources.append(source)
    if not ticks:
        raise RecorderError("the selected subject has no player_state ticks in sealed epochs")
    if ticks != sorted(set(ticks)):
        raise RecorderError("the selected subject timeline is duplicated or out of order")
    if ticks != list(range(ticks[0], ticks[-1] + 1)):
        raise RecorderError("the selected subject timeline is not contiguous")
    return _SubjectPoseSelection(bytes(encoded), tuple(ticks), tuple(sources))


def subject_state_ticks(
    episode: Path,
    *,
    player_uuid: str,
    connection_id: str,
    first_tick: int | None = None,
    last_tick: int | None = None,
    epochs: Iterable[EpochInfo] | None = None,
) -> tuple[int, ...]:
    """Return the exact selected player-state timeline from immutable epochs."""

    snapshot = tuple(iter_epochs(episode) if epochs is None else epochs)
    validation = validate_episode(episode, epochs=snapshot)
    if not validation.valid:
        raise RecorderError("episode must be valid before reading subject state ticks")
    selection = _select_subject_poses(
        session_id=validation.session_id,
        player_uuid=player_uuid,
        connection_id=connection_id,
        first_tick=first_tick,
        last_tick=last_tick,
        epochs=snapshot,
    )
    return selection.ticks


def _encode_subject_pose(record: dict[str, object]) -> bytes:
    try:
        line = (
            json.dumps(
                record,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            + b"\n"
        )
    except (TypeError, ValueError) as exc:
        raise RecorderError("subject pose is not canonical JSON") from exc
    if len(line) > 16 * 1024:
        raise RecorderError("subject pose record exceeds the 16 KiB safety limit")
    return line


def _pinned_epoch_snapshot(
    episode: Path,
    pinned_epoch_paths: Iterable[Path],
) -> tuple[EpochInfo, ...]:
    """Load only the exact epoch set protected by the caller's shared locks."""

    epochs_root = episode.resolve() / "epochs"
    requested = tuple(Path(path) for path in pinned_epoch_paths)
    expected = tuple(path.resolve() for path in requested)
    if len(expected) != len(set(expected)) or any(requested_path.is_symlink() or path.parent != epochs_root or not path.is_dir() for requested_path, path in zip(requested, expected, strict=True)):
        raise RecorderError("pinned scene epoch set contains an unsafe or duplicate path")

    declared: set[Path] = set()
    try:
        candidates = tuple(epochs_root.iterdir())
    except OSError as exc:
        raise RecorderError(f"cannot inspect pinned scene epochs: {epochs_root}") from exc
    for candidate in candidates:
        manifest = candidate / "manifest.json"
        if candidate.is_symlink() or not candidate.is_dir() or manifest.is_symlink():
            continue
        try:
            value = json.loads(manifest.read_text(encoding="utf-8"))
        except OSError, json.JSONDecodeError:
            continue
        if isinstance(value, dict) and value.get("sealed") is True:
            declared.add(candidate.resolve())
    if declared != set(expected):
        raise RecorderError("sealed epoch set changed while it was being pinned; retry scene extraction")

    snapshot: list[EpochInfo] = []
    for path in expected:
        info = inspect_epoch(path)
        if info is None or info.status != "sealed":
            raise RecorderError(f"pinned sealed epoch no longer verifies: {path}")
        snapshot.append(info)
    return tuple(sorted(snapshot, key=lambda item: item.index))


def _owned_scene_job(path: Path, *, expected_job_id: str | None = None) -> bool:
    manifest = path / "scene-job.json"
    marker = path / ".mc-recorder-scene-job"
    try:
        value = json.loads(manifest.read_text(encoding="utf-8"))
        ownership = json.loads(marker.read_text(encoding="utf-8"))
    except OSError, json.JSONDecodeError:
        return False
    job_id = value.get("job_id") if isinstance(value, dict) else None
    return (
        not path.is_symlink()
        and not manifest.is_symlink()
        and not marker.is_symlink()
        and isinstance(value, dict)
        and isinstance(ownership, dict)
        and value.get("schema_version") == 1
        and isinstance(job_id, str)
        and (expected_job_id is None or job_id == expected_job_id)
        and ownership
        == {
            "schema_version": 1,
            "marker_type": SCENE_JOB_OWNER_TYPE,
            "job_id": job_id,
        }
    )


def cleanup_scene_job(job: SceneJob) -> None:
    """Remove one exact owned scene job without following links."""

    directory = job.directory
    if directory.is_symlink() or job.manifest != directory / "scene-job.json" or job.result != directory / "result.json" or job.stream != directory / "stream" or not _owned_scene_job(directory, expected_job_id=job.job_id):
        raise RecorderError(f"refusing to remove non-owned scene job directory: {directory}")
    _remove_owned_scene_job_directory(directory, expected_job_id=job.job_id)


def _remove_owned_scene_job_directory(directory: Path, *, expected_job_id: str) -> None:
    try:
        for root, directories, files in os.walk(directory, followlinks=False):
            for name in (*directories, *files):
                if (Path(root) / name).is_symlink():
                    raise RecorderError(f"refusing to remove scene job containing a symlink: {directory}")
    except OSError as exc:
        raise RecorderError(f"cannot inspect scene job before cleanup: {directory}") from exc
    if not _owned_scene_job(directory, expected_job_id=expected_job_id):
        raise RecorderError(f"scene job changed before cleanup: {directory}")
    shutil.rmtree(directory)


def cleanup_stale_scene_jobs(runtime: Path, *, keep: int = 0) -> tuple[Path, ...]:
    """Remove marker-owned jobs under ``runtime/scene-jobs`` and ignore unsafe entries."""

    if not isinstance(keep, int) or isinstance(keep, bool) or keep < 0:
        raise RecorderError("scene job cleanup keep must be a non-negative integer")
    root = Path(runtime) / "scene-jobs"
    if root.is_symlink() or not root.is_dir():
        return ()
    owned: list[tuple[int, str, Path, str]] = []
    try:
        entries = list(root.iterdir())
    except OSError as exc:
        raise RecorderError(f"cannot enumerate stale scene jobs: {root}") from exc
    for entry in entries:
        if entry.is_symlink() or not entry.is_dir() or not _owned_scene_job(entry):
            continue
        try:
            manifest = json.loads((entry / "scene-job.json").read_text(encoding="utf-8"))
            job_id = manifest["job_id"]
            modified = entry.stat().st_mtime_ns
        except OSError, json.JSONDecodeError, KeyError, TypeError:
            continue
        if isinstance(job_id, str):
            owned.append((modified, entry.name, entry, job_id))
    owned.sort(reverse=True)
    removed: list[Path] = []
    for _modified, _name, entry, job_id in owned[keep:]:
        try:
            _remove_owned_scene_job_directory(entry, expected_job_id=job_id)
        except RecorderError:
            continue
        removed.append(entry)
    return tuple(removed)


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
    pinned_epoch_paths: Iterable[Path] | None = None,
) -> SceneJob:
    epoch_snapshot = tuple(iter_epochs(episode)) if pinned_epoch_paths is None else _pinned_epoch_snapshot(episode, pinned_epoch_paths)
    validation = validate_episode(episode, epochs=epoch_snapshot)
    if not validation.valid or validation.sealed_epochs == 0:
        raise RecorderError("episode must have at least one valid sealed epoch before scene extraction")
    player = _canonical_uuid(player_uuid, "player UUID")
    connection = _canonical_uuid(connection_id, "connection UUID")
    selection = _select_subject_poses(
        session_id=validation.session_id,
        player_uuid=player,
        connection_id=connection,
        first_tick=first_tick,
        last_tick=last_tick,
        epochs=epoch_snapshot,
    )
    ticks = selection.ticks
    pose_bytes = selection.data
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
    if any(source.flashback_capture_contract != FLASHBACK_CAPTURE_CONTRACT for source in sources):
        raise RecorderError(f"scene extraction requires replay archives captured under {FLASHBACK_CAPTURE_CONTRACT}")
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
        pose_path = directory / SUBJECT_POSE_FILE
        subject_poses = SubjectPoseEnvelope(
            path=pose_path,
            sha256=hashlib.sha256(pose_bytes).hexdigest(),
            size_bytes=len(pose_bytes),
            record_count=len(selection.ticks),
            first_tick=selected_first,
            last_tick=selected_last,
            source_epochs=selection.source_epochs,
        )
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
            "flashback_capture_contract": FLASHBACK_CAPTURE_CONTRACT,
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
            "subject_poses": subject_poses.as_dict(),
            "output": str(final_stream),
            "stop_when_done": True,
        }
        (staging / SUBJECT_POSE_FILE).write_bytes(pose_bytes)
        manifest = staging / "scene-job.json"
        manifest.write_text(
            json.dumps(manifest_value, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        (staging / ".mc-recorder-scene-job").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "marker_type": SCENE_JOB_OWNER_TYPE,
                    "job_id": job_id,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
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
        subject_poses=subject_poses,
    )


def _read_terminal_result(path: Path) -> _TerminalResult:
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_RESULT_BYTES:
            raise RecorderError(f"scene extractor did not publish a regular bounded result: {path}")
        before = path.stat()
        data = path.read_bytes()
        after = path.stat()
        if (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ) or len(data) != after.st_size:
            raise RecorderError("scene extractor result changed while it was being read")
        value = json.loads(data)
    except OSError as exc:
        raise RecorderError(f"cannot read scene extractor result: {path}") from exc
    except (json.JSONDecodeError, RecursionError) as exc:
        raise RecorderError(f"scene extractor result is invalid JSON: {path}") from exc
    if not isinstance(value, dict):
        raise RecorderError("scene extractor result must be an object")
    return _TerminalResult(value=value, sha256=hashlib.sha256(data).hexdigest())


def _validate_result(job: SceneJob, value: dict[str, Any]) -> SceneStreamIntegrity:
    if value.get("schema_version") != 1 or value.get("result_type") != SCENE_RESULT_TYPE:
        raise RecorderError("scene extractor published an unsupported result contract")
    error = value.get("error")
    if value.get("status") != "complete":
        raise RecorderError(f"scene extraction failed: {error or value.get('status') or 'unknown error'}")
    expected_keys = {
        "schema_version",
        "result_type",
        "status",
        "job_id",
        "session_id",
        "player_uuid",
        "connection_id",
        "global_start_tick",
        "global_end_tick",
        "scope",
        "metadata_policy",
        "flashback_capture_contract",
        "source_replays",
        "subject_poses",
        "stream",
        "ignored_packet_counts",
        "covered_tick_count",
    }
    if set(value) != expected_keys:
        raise RecorderError("scene extractor result keys do not match the complete contract")
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
    if value.get("scope") != "client_visible" or value.get("metadata_policy") != "full_packet_metadata" or value.get("flashback_capture_contract") != FLASHBACK_CAPTURE_CONTRACT:
        raise RecorderError("scene extractor result policy or capture contract does not match its job")
    if value.get("subject_poses") != job.subject_poses.as_dict():
        raise RecorderError("scene extractor result subject poses do not match its job")
    expected_sources = [
        {
            "segment_id": source.segment_id,
            "segment_ordinal": source.segment_ordinal,
            "path": str(source.path),
            "sha256": source.sha256,
            "size_bytes": source.size_bytes,
            "format": source.replay_format,
        }
        for source in job.sources
    ]
    contributing_sources = value.get("source_replays")
    if not isinstance(contributing_sources, list) or not contributing_sources:
        raise RecorderError("scene extractor result source replays must be a non-empty ordered job subset")
    expected_index = 0
    for contributing in contributing_sources:
        while expected_index < len(expected_sources) and expected_sources[expected_index] != contributing:
            expected_index += 1
        if expected_index == len(expected_sources):
            raise RecorderError("scene extractor result source replays are not an ordered exact subset of its job")
        expected_index += 1
    ignored = value.get("ignored_packet_counts")
    if not isinstance(ignored, dict) or any(not isinstance(name, str) or not name or not isinstance(count, int) or isinstance(count, bool) or count < 0 for name, count in ignored.items()):
        raise RecorderError("scene extractor result ignored packet counts are invalid")
    if value.get("covered_tick_count") != len(job.state_ticks):
        raise RecorderError("scene extractor result does not cover every selected state tick")
    stream = value.get("stream")
    if not isinstance(stream, dict) or set(stream) != {
        "format",
        "path",
        "frames_index",
        "changes_index",
        "blobs_directory",
        "frame_count",
        "change_count",
        "blob_count",
        "blob_bytes",
        "frames_sha256",
        "frames_size_bytes",
        "changes_sha256",
        "changes_size_bytes",
    }:
        raise RecorderError("scene extractor result has an invalid stream envelope")
    if Path(str(stream.get("path", ""))).resolve() != job.stream:
        raise RecorderError("scene extractor result stream path does not match its job")
    try:
        integrity = scene_stream_integrity_from_mapping({key: item for key, item in stream.items() if key != "path"})
    except SceneStreamIntegrityError as exc:
        raise RecorderError(f"scene extractor result has an invalid stream envelope: {exc}") from exc
    if integrity.frame_count != len(job.state_ticks):
        raise RecorderError("scene extractor frame count does not match selected state ticks")
    if integrity.frames_size_bytes <= 0 or integrity.changes_size_bytes <= 0:
        raise RecorderError("scene extractor indexes must not be empty")
    return integrity


def _assert_sources_unchanged(sources: Iterable[ReplaySegmentSource]) -> None:
    for source in sources:
        try:
            size = source.path.stat().st_size
            digest = sha256_file(source.path)
        except OSError as exc:
            raise RecorderError(f"source replay disappeared after extraction: {source.path}") from exc
        if size != source.size_bytes or digest != source.sha256:
            raise RecorderError(f"source replay changed during extraction: {source.path}")


def _assert_subject_poses_unchanged(job: SceneJob) -> None:
    source = job.subject_poses
    if source.path != job.directory / SUBJECT_POSE_FILE:
        raise RecorderError("subject pose path is outside its owned scene job")
    try:
        if source.path.is_symlink() or not source.path.is_file():
            raise RecorderError("subject pose stream must be a regular non-symlink file")
        before = source.path.stat()
        size = before.st_size
        digest = sha256_file(source.path)
        after = source.path.stat()
    except OSError as exc:
        raise RecorderError(f"cannot verify subject pose stream: {source.path}") from exc
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) or size != source.size_bytes or digest != source.sha256:
        raise RecorderError("subject pose stream changed during extraction")


def _resolve_scene_extractor_executable(config: RecorderConfig, environment: dict[str, str]) -> str:
    override = environment.get(ENV_SCENE_EXTRACTOR_EXECUTABLE)
    candidate = Path(override).expanduser() if override else config.mods.scene_extractor_executable
    if not candidate.is_absolute():
        raise RecorderError(f"{ENV_SCENE_EXTRACTOR_EXECUTABLE} must be an absolute executable path")
    if not candidate.is_file() or not os.access(candidate, os.X_OK):
        raise RecorderError(f"scene extractor executable not found or not executable: {candidate}; run `pixi run build-scene-extractor-mod`")
    return str(candidate)


@contextmanager
def _locked_sources(sources: Iterable[ReplaySegmentSource]) -> Iterator[None]:
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
                raise RecorderError(f"source replay is being retained or evicted by another operation: {source.path}") from exc
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
) -> VerifiedSceneStream:
    environment = current_process_environment()
    extractor_executable = _resolve_scene_extractor_executable(config, environment)
    with _locked_sources(job.sources):
        _assert_sources_unchanged(job.sources)
        _assert_subject_poses_unchanged(job)
        try:
            process = subprocess.run(
                [
                    extractor_executable,
                    "--job",
                    str(job.manifest),
                ],
                cwd=job.directory,
                env=environment,
                check=False,
                text=True,
                stdout=subprocess.PIPE if capture_output else None,
                stderr=subprocess.PIPE if capture_output else None,
            )
        except OSError as exc:
            raise RecorderError(f"cannot launch scene extractor executable {extractor_executable}: {exc}") from exc

        terminal = _read_terminal_result(job.result)
        value = terminal.value
        if process.returncode != 0:
            detail = value.get("error")
            if not detail and capture_output and process.stderr:
                detail = process.stderr.strip()[-2048:]
            raise RecorderError(f"scene extractor exited with code {process.returncode}" + (f": {detail}" if detail else ""))
        integrity = _validate_result(job, value)
        try:
            verify_scene_stream(job.stream, integrity)
        except SceneStreamIntegrityError as exc:
            raise RecorderError(f"scene extractor spool failed integrity verification: {exc}") from exc
        _assert_sources_unchanged(job.sources)
        _assert_subject_poses_unchanged(job)
        return VerifiedSceneStream(
            job_id=job.job_id,
            stream_path=job.stream,
            result=freeze_json_value(value),
            result_sha256=terminal.sha256,
            integrity=integrity,
        )


__all__ = [
    "SCENE_JOB_TYPE",
    "SCENE_RESULT_TYPE",
    "SCENE_STREAM_FORMAT",
    "SceneJob",
    "SubjectPoseEnvelope",
    "SubjectPoseSourceEpoch",
    "VerifiedSceneStream",
    "cleanup_scene_job",
    "cleanup_stale_scene_jobs",
    "launch_scene_job",
    "prepare_scene_job",
    "subject_state_ticks",
]
