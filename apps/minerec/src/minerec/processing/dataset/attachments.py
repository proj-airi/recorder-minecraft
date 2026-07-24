from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, cast

from minerec.errors import RecorderError
from minerec.processing.capture.exporter import FrameKey, load_frame_attachments

ATTACHMENT_ROOT_NAME = ".dataset-attachments"
RGB_ATTACHMENT_SCHEMA_VERSION = 1
RGB_ATTACHMENT_TYPE = "mc-recorder-rgb-attachment-v1"
MAX_ATTACHMENT_MANIFEST_BYTES = 64 * 1024 * 1024

_DATASET_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class RgbAttachmentSet:
    frames: dict[FrameKey, dict[str, Any]]
    sources: tuple[dict[str, Any], ...]
    sha256: str
    signature: tuple[int, int, int, int, int]


def rgb_attachment_path(exports_root: Path, dataset_id: str) -> Path:
    if _DATASET_ID_RE.fullmatch(dataset_id) is None:
        raise RecorderError("RGB attachment has an invalid dataset ID")
    root = Path(exports_root).expanduser().resolve()
    return root / ATTACHMENT_ROOT_NAME / dataset_id / "rgb.json"


def _frame_row(key: FrameKey, attached: Any) -> dict[str, Any]:  # noqa: ANN401
    session_id, player_uuid, connection_id, server_tick = key
    return {
        "session_id": session_id,
        "player_uuid": player_uuid,
        "connection_id": connection_id,
        "server_tick": server_tick,
        "available": True,
        "valid": True,
        "reference": attached.reference,
        "reason": None,
        "frame": attached.row.get("frame"),
        "replay_tick": attached.row.get("replay_tick"),
        "partial_tick": attached.row.get("partial_tick"),
        "frames_index_sha256": attached.index_sha256,
        "artifact_sha256": attached.artifact_sha256,
        "artifact_bytes": attached.artifact_bytes,
        "width": attached.width,
        "height": attached.height,
    }


def _validate_frame(value: object) -> tuple[FrameKey, dict[str, Any]]:
    if not isinstance(value, dict):
        raise RecorderError("RGB attachment frame must be an object")
    session_id = value.get("session_id")
    player_uuid = value.get("player_uuid")
    connection_id = value.get("connection_id")
    server_tick = value.get("server_tick")
    reference = value.get("reference")
    artifact_sha256 = value.get("artifact_sha256")
    artifact_bytes = value.get("artifact_bytes")
    width = value.get("width")
    height = value.get("height")
    if not all(isinstance(item, str) and item for item in (session_id, player_uuid, connection_id)):
        raise RecorderError("RGB attachment frame identity is invalid")
    if not isinstance(server_tick, int) or isinstance(server_tick, bool) or server_tick < 0:
        raise RecorderError("RGB attachment frame tick is invalid")
    if value.get("available") is not True or value.get("valid") is not True or value.get("reason") is not None:
        raise RecorderError("RGB attachment frame availability is invalid")
    if not isinstance(reference, str) or not reference or "\x00" in reference:
        raise RecorderError("RGB attachment frame reference is invalid")
    if not isinstance(artifact_sha256, str) or _SHA256_RE.fullmatch(artifact_sha256) is None:
        raise RecorderError("RGB attachment frame SHA-256 is invalid")
    if not isinstance(artifact_bytes, int) or isinstance(artifact_bytes, bool) or artifact_bytes <= 0:
        raise RecorderError("RGB attachment frame byte size is invalid")
    for dimension, label in ((width, "width"), (height, "height")):
        if not isinstance(dimension, int) or isinstance(dimension, bool) or dimension <= 0:
            raise RecorderError(f"RGB attachment frame {label} is invalid")
    frame = cast(dict[str, Any], value)
    return (
        (cast(str, session_id), cast(str, player_uuid), cast(str, connection_id), server_tick),
        dict(frame),
    )


def load_rgb_attachments(
    exports_root: Path,
    dataset_id: str,
    *,
    dataset_manifest_sha256: str,
    samples_sha256: str,
) -> RgbAttachmentSet | None:
    path = rgb_attachment_path(exports_root, dataset_id)
    if not path.exists():
        return None
    if path.is_symlink() or not path.is_file():
        raise RecorderError(f"RGB attachment manifest is not a regular file: {path}")
    for parent in (path.parent, path.parent.parent):
        if parent.is_symlink() or not parent.is_dir():
            raise RecorderError(f"RGB attachment directory is unsafe: {parent}")
    try:
        before = path.stat()
        if before.st_size > MAX_ATTACHMENT_MANIFEST_BYTES:
            raise RecorderError("RGB attachment manifest exceeds the size limit")
        encoded = path.read_bytes()
        after = path.stat()
        value = json.loads(encoded)
    except OSError as exc:
        raise RecorderError(f"cannot read RGB attachment manifest: {path}") from exc
    except (ValueError, RecursionError) as exc:
        raise RecorderError(f"RGB attachment manifest is invalid JSON: {path}") from exc
    signature = (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    )
    if signature != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    ) or after.st_size != len(encoded):
        raise RecorderError("RGB attachment manifest changed while it was read")
    if not isinstance(value, dict):
        raise RecorderError("RGB attachment manifest must be an object")
    binding = value.get("dataset")
    if (
        value.get("schema_version") != RGB_ATTACHMENT_SCHEMA_VERSION
        or value.get("owner") != "mc-recorder"
        or value.get("attachment_type") != RGB_ATTACHMENT_TYPE
        or not isinstance(binding, dict)
        or binding.get("dataset_id") != dataset_id
        or binding.get("manifest_sha256") != dataset_manifest_sha256
        or binding.get("samples_sha256") != samples_sha256
    ):
        raise RecorderError("RGB attachment manifest does not bind the current Dataset core")
    raw_frames = value.get("frames")
    raw_sources = value.get("sources")
    if not isinstance(raw_frames, list) or not isinstance(raw_sources, list):
        raise RecorderError("RGB attachment manifest lacks frames or sources")
    frames: dict[FrameKey, dict[str, Any]] = {}
    for raw in raw_frames:
        key, frame = _validate_frame(raw)
        if key in frames:
            raise RecorderError("RGB attachment manifest repeats a frame identity")
        frames[key] = frame
    if not all(isinstance(source, dict) for source in raw_sources):
        raise RecorderError("RGB attachment source provenance is invalid")
    return RgbAttachmentSet(
        frames=frames,
        sources=tuple(dict(source) for source in raw_sources),
        sha256=hashlib.sha256(encoded).hexdigest(),
        signature=signature,
    )


def publish_rgb_attachments(
    exports_root: Path,
    dataset_directory: Path,
    dataset_id: str,
    *,
    dataset_manifest_sha256: str,
    samples_sha256: str,
    session_id: str,
    job_id: str,
    frame_inputs: Iterable[Path],
) -> RgbAttachmentSet:
    exports = Path(exports_root).expanduser().resolve()
    dataset = Path(dataset_directory).expanduser().resolve()
    loaded, sources = load_frame_attachments(
        frame_inputs,
        session_id,
        exports_root=exports,
        dataset_directory=dataset,
    )
    existing = load_rgb_attachments(
        exports,
        dataset_id,
        dataset_manifest_sha256=dataset_manifest_sha256,
        samples_sha256=samples_sha256,
    )
    frames = dict(existing.frames) if existing is not None else {}
    frames.update({key: _frame_row(key, attached) for key, attached in loaded.items()})
    source_rows = list(existing.sources) if existing is not None else []
    source_rows.extend({**source, "job_id": job_id} for source in sources)
    sources_by_value = {json.dumps(source, sort_keys=True, separators=(",", ":"), ensure_ascii=False): source for source in source_rows}

    value = {
        "schema_version": RGB_ATTACHMENT_SCHEMA_VERSION,
        "owner": "mc-recorder",
        "attachment_type": RGB_ATTACHMENT_TYPE,
        "updated_at": datetime.now(UTC).isoformat(),
        "dataset": {
            "dataset_id": dataset_id,
            "manifest_sha256": dataset_manifest_sha256,
            "samples_sha256": samples_sha256,
        },
        "sources": [sources_by_value[key] for key in sorted(sources_by_value)],
        "frames": [frames[key] for key in sorted(frames)],
    }
    encoded = (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()
    if len(encoded) > MAX_ATTACHMENT_MANIFEST_BYTES:
        raise RecorderError("RGB attachment manifest exceeds the size limit")

    destination = rgb_attachment_path(exports, dataset_id)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.parent.is_symlink() or destination.parent.parent.is_symlink():
        raise RecorderError("RGB attachment destination may not contain symlinked directories")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".rgb.json.tmp-",
        dir=destination.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
        directory_descriptor = os.open(destination.parent, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    result = load_rgb_attachments(
        exports,
        dataset_id,
        dataset_manifest_sha256=dataset_manifest_sha256,
        samples_sha256=samples_sha256,
    )
    if result is None:
        raise RecorderError("RGB attachment publication disappeared")
    return result


__all__ = [
    "ATTACHMENT_ROOT_NAME",
    "RGB_ATTACHMENT_TYPE",
    "RgbAttachmentSet",
    "load_rgb_attachments",
    "publish_rgb_attachments",
    "rgb_attachment_path",
]
