from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import stat
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from fractions import Fraction
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from minerec.config import RecorderConfig
from minerec.errors import RecorderError
from minerec.processing.bundle.model import (
    ArtifactSource,
    BundleIdentity,
    BundleRequest,
    PublishedBundle,
    RenderAttachment,
    ReplaySegment,
)
from minerec.processing.bundle.publisher import publish_bundle
from minerec.processing.scene.store import (
    SceneStoreInfo,
    validate_scene_attachment_provenance,
    validate_scene_store,
)
from minerec.processing.scene.store_v2 import finalize_scene_store_v2, validate_scene_store_v2
from minerec.render.control.sources import ReplaySegmentSource, resolve_replay_segments

MAX_DATASET_MANIFEST_BYTES = 4 * 1024 * 1024
MAX_DATASET_FILES = 64
MAX_STATE_LINE_BYTES = 64 * 1024 * 1024
MAX_STATES = 20 * 60 * 60 * 24
KNOWN_MODALITY_GAPS = (
    "audio_not_captured",
    "particle_lifecycle_not_captured",
    "raw_device_input_not_captured",
    "unopened_container_contents_unknown",
)


@dataclass(frozen=True)
class _Dataset:
    directory: Path
    manifest: Mapping[str, Any]
    files: Mapping[str, ArtifactSource]


@dataclass(frozen=True)
class _StateIdentity:
    session_id: str
    player_uuid: str
    player_name: str
    connection_id: str
    ticks: tuple[int, ...]
    started_at: str
    ended_at: str


def _strict_json(data: bytes, description: str) -> Any:  # noqa: ANN401
    try:
        return json.loads(
            data,
            parse_constant=lambda token: (_ for _ in ()).throw(ValueError(f"non-finite {token}")),
        )
    except (ValueError, UnicodeDecodeError, RecursionError) as exc:
        raise RecorderError(f"{description} is invalid JSON") from exc


def _canonical_uuid(value: object, description: str) -> str:
    if not isinstance(value, str):
        raise RecorderError(f"{description} must be a canonical UUID")
    try:
        canonical = str(uuid.UUID(value))
    except ValueError as exc:
        raise RecorderError(f"{description} must be a canonical UUID") from exc
    if value != canonical:
        raise RecorderError(f"{description} must use canonical UUID spelling")
    return canonical


def _regular_file(path: Path, description: str) -> os.stat_result:
    try:
        status = path.lstat()
    except OSError as exc:
        raise RecorderError(f"cannot inspect {description}: {path}") from exc
    if stat.S_ISLNK(status.st_mode) or not stat.S_ISREG(status.st_mode) or status.st_nlink != 1:
        raise RecorderError(f"{description} must be a non-linked regular file: {path}")
    return status


def _file_identity(status: os.stat_result) -> tuple[int, int, int, int]:
    return status.st_dev, status.st_ino, status.st_size, status.st_mtime_ns


def _stable_artifact(path: Path, description: str) -> ArtifactSource:
    before = _regular_file(path, description)
    digest = hashlib.sha256()
    observed = 0
    try:
        with path.open("rb") as handle:
            opened = os.fstat(handle.fileno())
            if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
                raise RecorderError(f"{description} changed while being opened")
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
                observed += len(chunk)
            after_open = os.fstat(handle.fileno())
        after = path.lstat()
    except OSError as exc:
        raise RecorderError(f"cannot hash {description}: {path}") from exc
    identities = (
        (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns),
        (after_open.st_dev, after_open.st_ino, after_open.st_size, after_open.st_mtime_ns),
        (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns),
    )
    if identities[0] != identities[1] or identities[0] != identities[2] or observed != before.st_size:
        raise RecorderError(f"{description} changed while being hashed")
    return ArtifactSource(path=path, sha256=digest.hexdigest(), size_bytes=observed)


def _dataset_entry_name(name: object) -> str:
    if not isinstance(name, str) or not name or "\\" in name or "\x00" in name:
        raise RecorderError("dataset manifest contains an invalid file name")
    value = PurePosixPath(name)
    if value.is_absolute() or any(part in {"", ".", ".."} for part in name.split("/")):
        raise RecorderError("dataset manifest contains an escaping file name")
    return name


def _load_dataset(directory: Path) -> _Dataset:
    root = directory.expanduser().resolve()
    if directory.is_symlink() or not root.is_dir() or not root.name.endswith(".dataset"):
        raise RecorderError("bundle finalization requires a non-symlinked *.dataset directory")
    manifest_path = root / "manifest.json"
    status = _regular_file(manifest_path, "dataset manifest")
    if status.st_size > MAX_DATASET_MANIFEST_BYTES:
        raise RecorderError("dataset manifest exceeds the byte limit")
    manifest = _strict_json(manifest_path.read_bytes(), "dataset manifest")
    if not isinstance(manifest, dict):
        raise RecorderError("dataset manifest must be an object")
    if manifest.get("schema_version") != 2 or manifest.get("owner") != "mc-recorder" or manifest.get("format") != "mc-recorder-jsonl-v2":
        raise RecorderError("bundle finalization requires a Dataset V2 export")
    declarations = manifest.get("files")
    if not isinstance(declarations, dict) or not 1 <= len(declarations) <= MAX_DATASET_FILES:
        raise RecorderError("dataset manifest file inventory is invalid")

    actual: set[str] = set()
    for candidate in root.rglob("*"):
        if candidate.is_symlink():
            raise RecorderError(f"dataset contains a symlink: {candidate.relative_to(root)}")
        if candidate.is_dir():
            continue
        if not candidate.is_file():
            raise RecorderError(f"dataset contains a non-regular entry: {candidate.relative_to(root)}")
        relative = candidate.relative_to(root).as_posix()
        if relative != "manifest.json":
            actual.add(relative)
    declared = {_dataset_entry_name(name) for name in declarations}
    if actual != declared:
        raise RecorderError("dataset files do not exactly match manifest inventory")

    files: dict[str, ArtifactSource] = {}
    for name in sorted(declared):
        descriptor = declarations[name]
        if not isinstance(descriptor, dict) or set(descriptor) != {"sha256", "size_bytes"}:
            raise RecorderError(f"dataset manifest file descriptor is invalid: {name}")
        source = _stable_artifact(root.joinpath(*name.split("/")), f"dataset file {name}")
        if descriptor.get("sha256") != source.sha256 or descriptor.get("size_bytes") != source.size_bytes:
            raise RecorderError(f"dataset file fails its manifest hash or size: {name}")
        files[name] = source
    for required in ("actions.jsonl", "states.jsonl", "scene/scene-v1.sqlite3"):
        if required not in files:
            raise RecorderError(f"dataset lacks required bundle source {required}")
    if _file_identity(_regular_file(manifest_path, "dataset manifest")) != _file_identity(status):
        raise RecorderError("dataset manifest changed during validation")
    return _Dataset(root, manifest, files)


def _utc_from_millis(value: object, description: str) -> tuple[int, str]:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise RecorderError(f"{description} must be a non-negative Unix millisecond timestamp")
    try:
        moment = datetime.fromtimestamp(value / 1000, UTC)
    except (OSError, OverflowError, ValueError) as exc:
        raise RecorderError(f"{description} is outside the supported UTC range") from exc
    return value, moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _inspect_states(path: Path) -> _StateIdentity:
    values: tuple[str, str, str, str] | None = None
    ticks: list[int] = []
    wall_times: list[int] = []
    before = _regular_file(path, "authoritative states JSONL")
    try:
        with path.open("rb") as handle:
            for line_number, raw in enumerate(handle, 1):
                if line_number > MAX_STATES:
                    raise RecorderError("authoritative states exceed the connection tick limit")
                if len(raw) > MAX_STATE_LINE_BYTES:
                    raise RecorderError(f"states.jsonl line {line_number} exceeds the byte limit")
                if not raw.endswith(b"\n") or not raw.strip():
                    raise RecorderError(f"states.jsonl line {line_number} is blank or unterminated")
                record = _strict_json(raw, f"states.jsonl line {line_number}")
                if not isinstance(record, dict):
                    raise RecorderError(f"states.jsonl line {line_number} must be an object")
                identity = (
                    record.get("session_id"),
                    record.get("player_uuid"),
                    record.get("player_name"),
                    record.get("connection_id"),
                )
                if not all(isinstance(item, str) and item for item in identity):
                    raise RecorderError(f"states.jsonl line {line_number} lacks connection identity")
                typed_identity = (identity[0], identity[1], identity[2], identity[3])
                if values is None:
                    values = typed_identity  # type: ignore[assignment]
                elif values != typed_identity:
                    raise RecorderError("states.jsonl contains more than one player connection")
                tick = record.get("server_tick")
                if not isinstance(tick, int) or isinstance(tick, bool) or tick < 0:
                    raise RecorderError(f"states.jsonl line {line_number} has invalid server_tick")
                if ticks and tick != ticks[-1] + 1:
                    raise RecorderError("states.jsonl ticks must be contiguous and strictly increasing")
                ticks.append(tick)
                wall_time, _timestamp = _utc_from_millis(
                    record.get("recorded_at_unix_ms"),
                    f"states.jsonl line {line_number} recorded_at_unix_ms",
                )
                wall_times.append(wall_time)
    except OSError as exc:
        raise RecorderError("cannot read authoritative states JSONL") from exc
    after = path.lstat()
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ):
        raise RecorderError("authoritative states changed while being inspected")
    if values is None or not ticks:
        raise RecorderError("states.jsonl contains no player states")
    session_id, player_uuid, player_name, connection_id = values
    _canonical_uuid(player_uuid, "state player_uuid")
    _canonical_uuid(connection_id, "state connection_id")
    _start_ms, started_at = _utc_from_millis(min(wall_times), "connection start")
    _end_ms, ended_at = _utc_from_millis(max(wall_times), "connection end")
    return _StateIdentity(
        session_id=session_id,
        player_uuid=player_uuid,
        player_name=player_name,
        connection_id=connection_id,
        ticks=tuple(ticks),
        started_at=started_at,
        ended_at=ended_at,
    )


def _replay_segments(
    scene_path: Path,
    scene: SceneStoreInfo,
    sources: list[ReplaySegmentSource],
) -> tuple[ReplaySegment, ...]:
    expected = {(item["segment_id"], item["segment_ordinal"]): (item["sha256"], item["size_bytes"]) for item in scene.source_replays}
    actual = {(item.segment_id, item.segment_ordinal): (item.sha256, item.size_bytes) for item in sources}
    if actual != expected:
        raise RecorderError("cataloged replay segments do not exactly match Scene V1 provenance")
    frames: dict[str, list[tuple[int, int]]] = {source.segment_id: [] for source in sources}
    connection = sqlite3.connect(f"file:{scene_path}?mode=ro&immutable=1", uri=True)
    try:
        for server_tick, frame_id, replay_tick in connection.execute("SELECT server_tick, frame_id, replay_tick FROM frames ORDER BY server_tick"):
            matches = [source.segment_id for source in sources if frame_id.startswith(f"{source.segment_id}:")]
            if len(matches) != 1 or not isinstance(replay_tick, int) or isinstance(replay_tick, bool) or replay_tick < 0:
                raise RecorderError(f"scene frame at tick {server_tick} lacks exact replay-segment alignment")
            frames[matches[0]].append((server_tick, replay_tick))
    except sqlite3.Error as exc:
        raise RecorderError("cannot read replay alignment from Scene V1") from exc
    finally:
        connection.close()

    result: list[ReplaySegment] = []
    for source in sorted(sources, key=lambda item: item.segment_ordinal):
        aligned = frames[source.segment_id]
        if not aligned:
            raise RecorderError(f"Scene V1 replay segment {source.segment_id} has no selected frame coverage")
        result.append(
            ReplaySegment(
                segment_id=source.segment_id,
                source=ArtifactSource(source.path, source.sha256, source.size_bytes),
                start_server_tick=min(item[0] for item in aligned),
                end_server_tick=max(item[0] for item in aligned),
                start_replay_tick=min(item[1] for item in aligned),
                end_replay_tick=max(item[1] for item in aligned),
            )
        )
    return tuple(result)


def _probe_render(path: Path) -> dict[str, Any]:
    executable = shutil.which("ffprobe")
    if executable is None:
        raise RecorderError("ffprobe is required to validate an FPV attachment; run through Pixi")
    try:
        process = subprocess.run(
            (
                executable,
                "-v",
                "error",
                "-count_frames",
                "-show_entries",
                "stream=codec_type,codec_name,pix_fmt,width,height,avg_frame_rate,r_frame_rate,nb_read_frames",
                "-of",
                "json",
                str(path),
            ),
            check=False,
            capture_output=True,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RecorderError("ffprobe could not inspect the FPV attachment") from exc
    if process.returncode != 0:
        raise RecorderError("ffprobe rejected the FPV attachment")
    value = _strict_json(process.stdout, "ffprobe output")
    streams = value.get("streams") if isinstance(value, dict) else None
    if not isinstance(streams, list) or len(streams) != 1 or not isinstance(streams[0], dict):
        raise RecorderError("FPV attachment must contain exactly one video stream and no audio")
    stream = streams[0]
    if stream.get("codec_type") != "video" or stream.get("codec_name") != "h264" or stream.get("pix_fmt") != "yuv420p":
        raise RecorderError("FPV attachment must be H.264 with yuv420p pixels")
    try:
        fps = Fraction(str(stream.get("avg_frame_rate")))
        frame_count = int(stream.get("nb_read_frames"))
        width = int(stream.get("width"))
        height = int(stream.get("height"))
    except (ValueError, TypeError, ZeroDivisionError) as exc:
        raise RecorderError("FPV attachment lacks usable frame-rate, count, or dimensions") from exc
    if fps != 20 or width <= 0 or height <= 0 or frame_count <= 0:
        raise RecorderError("FPV attachment must be constant 20 FPS with positive dimensions and frames")
    return {"frame_count": frame_count, "width": width, "height": height}


def validate_fpv_render(path: Path, descriptor: Mapping[str, Any]) -> None:
    observed = _probe_render(path)
    expected = {
        "frame_count": descriptor.get("frame_count"),
        "width": descriptor.get("width"),
        "height": descriptor.get("height"),
    }
    if observed != expected:
        raise RecorderError("FPV attachment does not match its bundle descriptor")


def _render_attachment(
    video_path: Path,
    timeline_path: Path,
    *,
    expected_frames: int,
) -> RenderAttachment:
    video = _stable_artifact(video_path.expanduser().resolve(), "FPV render")
    timeline = _stable_artifact(timeline_path.expanduser().resolve(), "FPV render timeline")
    observed = _probe_render(video.path)
    if observed["frame_count"] != expected_frames:
        raise RecorderError("FPV frame count must equal the connection tick count")
    return RenderAttachment(
        video=video,
        timeline=timeline,
        frame_count=observed["frame_count"],
        width=observed["width"],
        height=observed["height"],
    )


def finalize_dataset_bundle(
    config: RecorderConfig,
    dataset_path: Path,
    *,
    fpv_path: Path | None = None,
    fpv_timeline_path: Path | None = None,
) -> PublishedBundle:
    """Finalize one verified, connection-scoped Dataset V2 as an immutable bundle."""

    if config.server.instance_id is None:
        raise RecorderError("server.instance_id is missing; add one canonical UUID to recorder.toml before publishing")
    server_instance_id = _canonical_uuid(config.server.instance_id, "server.instance_id")
    if (fpv_path is None) != (fpv_timeline_path is None):
        raise RecorderError("--fpv and --fpv-timeline must be provided together")
    dataset = _load_dataset(dataset_path)
    states = _inspect_states(dataset.files["states.jsonl"].path)
    scene_v1 = dataset.files["scene/scene-v1.sqlite3"].path
    scene_info = validate_scene_store(scene_v1)
    validate_scene_attachment_provenance(scene_info)
    expected_identity = (
        states.session_id,
        states.player_uuid,
        states.connection_id,
    )
    observed_identity = (
        scene_info.identity.session_id,
        scene_info.identity.player_uuid,
        scene_info.identity.connection_id,
    )
    if observed_identity != expected_identity or scene_info.ticks != states.ticks or not scene_info.coverage_complete:
        raise RecorderError("Scene V1 identity or complete tick coverage does not match authoritative states")
    sources = resolve_replay_segments(
        replays_root=config.paths.replays,
        session_id=states.session_id,
        player_uuid=states.player_uuid,
        connection_id=states.connection_id,
    )
    replay_segments = _replay_segments(scene_v1, scene_info, sources)

    config.paths.runtime.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="play-bundle-finalize-", dir=config.paths.runtime) as temporary:
        scene_v2_path = Path(temporary) / "scene.sqlite3"
        scene_v2 = finalize_scene_store_v2(
            scene_v1,
            dataset.files["states.jsonl"].path,
            scene_v2_path,
        )
        render = (
            _render_attachment(
                fpv_path,
                fpv_timeline_path,
                expected_frames=len(states.ticks),
            )
            if fpv_path is not None and fpv_timeline_path is not None
            else None
        )
        request = BundleRequest(
            identity=BundleIdentity(
                server_name=config.server.name,
                server_instance_id=server_instance_id,
                player_name=states.player_name,
                player_uuid=states.player_uuid,
                session_id=states.session_id,
                connection_id=states.connection_id,
                started_at=states.started_at,
                ended_at=states.ended_at,
                start_tick=states.ticks[0],
                end_tick=states.ticks[-1],
                sensitivity="private" if scene_v2.sensitive else "internal",
                known_modality_gaps=KNOWN_MODALITY_GAPS,
            ),
            actions=dataset.files["actions.jsonl"],
            scene=ArtifactSource(scene_v2_path, scene_v2.sha256, scene_v2.size_bytes),
            replay_segments=replay_segments,
            render=render,
        )
        return publish_bundle(
            request,
            config.paths.bundles,
            scene_validator=validate_scene_store_v2,
            render_validator=validate_fpv_render if render is not None else None,
        )


__all__ = ["finalize_dataset_bundle", "validate_fpv_render"]
