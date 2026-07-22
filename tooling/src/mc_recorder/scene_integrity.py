from __future__ import annotations

import hashlib
import json
import os
import zlib
from dataclasses import asdict, dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from .errors import RecorderError

SCENE_STREAM_FORMAT = "mc-recorder-scene-stream-v1"
MAX_BLOB_UNCOMPRESSED_BYTES = 64 * 1024 * 1024
_SHA256_CHARACTERS = frozenset("0123456789abcdef")


class SceneStreamIntegrityError(RecorderError):
    """A normalized scene spool no longer matches its terminal result."""


@dataclass(frozen=True)
class SceneStreamIntegrity:
    format: str
    frames_index: str
    frames_size_bytes: int
    frames_sha256: str
    frame_count: int
    changes_index: str
    changes_size_bytes: int
    changes_sha256: str
    change_count: int
    blobs_directory: str
    blob_count: int
    blob_bytes: int

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class VerifiedSceneStream:
    job_id: str
    stream_path: Path
    result: Mapping[str, Any]
    result_sha256: str
    integrity: SceneStreamIntegrity


def freeze_json_value(value: Any) -> Any:
    """Return an immutable tree for one already validated JSON value."""

    if isinstance(value, dict):
        return MappingProxyType(
            {str(key): freeze_json_value(item) for key, item in value.items()}
        )
    if isinstance(value, list):
        return tuple(freeze_json_value(item) for item in value)
    return value


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in _SHA256_CHARACTERS for character in value)
    )


def _required_nonnegative_int(value: object, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise SceneStreamIntegrityError(f"{label} must be a non-negative integer")
    return value


def scene_stream_integrity_from_mapping(value: Mapping[str, Any]) -> SceneStreamIntegrity:
    expected_keys = {
        "format",
        "frames_index",
        "frames_size_bytes",
        "frames_sha256",
        "frame_count",
        "changes_index",
        "changes_size_bytes",
        "changes_sha256",
        "change_count",
        "blobs_directory",
        "blob_count",
        "blob_bytes",
    }
    if set(value) != expected_keys:
        raise SceneStreamIntegrityError("scene stream integrity keys do not match the contract")
    if value.get("format") != SCENE_STREAM_FORMAT:
        raise SceneStreamIntegrityError("scene stream format is unsupported")
    if value.get("frames_index") != "frames.jsonl":
        raise SceneStreamIntegrityError("scene frames index must be exactly frames.jsonl")
    if value.get("changes_index") != "changes.jsonl":
        raise SceneStreamIntegrityError("scene changes index must be exactly changes.jsonl")
    if value.get("blobs_directory") != "blobs":
        raise SceneStreamIntegrityError("scene blobs directory must be exactly blobs")
    frames_sha256 = value.get("frames_sha256")
    changes_sha256 = value.get("changes_sha256")
    if not _is_sha256(frames_sha256) or not _is_sha256(changes_sha256):
        raise SceneStreamIntegrityError("scene stream indexes require lowercase SHA-256 digests")
    return SceneStreamIntegrity(
        format=SCENE_STREAM_FORMAT,
        frames_index="frames.jsonl",
        frames_size_bytes=_required_nonnegative_int(
            value.get("frames_size_bytes"), "frames_size_bytes"
        ),
        frames_sha256=frames_sha256,
        frame_count=_required_nonnegative_int(value.get("frame_count"), "frame_count"),
        changes_index="changes.jsonl",
        changes_size_bytes=_required_nonnegative_int(
            value.get("changes_size_bytes"), "changes_size_bytes"
        ),
        changes_sha256=changes_sha256,
        change_count=_required_nonnegative_int(value.get("change_count"), "change_count"),
        blobs_directory="blobs",
        blob_count=_required_nonnegative_int(value.get("blob_count"), "blob_count"),
        blob_bytes=_required_nonnegative_int(value.get("blob_bytes"), "blob_bytes"),
    )


def _safe_file_bytes(path: Path, label: str) -> bytes:
    try:
        if path.is_symlink() or not path.is_file():
            raise SceneStreamIntegrityError(f"{label} must be a regular non-symlink file")
        before = path.stat()
        data = path.read_bytes()
        after = path.stat()
    except OSError as exc:
        raise SceneStreamIntegrityError(f"cannot read {label}: {path}") from exc
    before_identity = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
    after_identity = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    if before_identity != after_identity or len(data) != after.st_size:
        raise SceneStreamIntegrityError(f"{label} changed while it was being verified")
    return data


def _verify_index(
    path: Path,
    *,
    label: str,
    expected_size: int,
    expected_sha256: str,
    expected_count: int,
    collect_blob_references: bool = False,
) -> set[str]:
    if path.is_symlink() or not path.is_file():
        raise SceneStreamIntegrityError(f"{label} must be a regular non-symlink file")
    digest = hashlib.sha256()
    size = 0
    count = 0
    referenced: set[str] = set()
    try:
        with path.open("rb") as handle:
            before = os.fstat(handle.fileno())
            for count, line in enumerate(handle, start=1):
                if not line.strip():
                    raise SceneStreamIntegrityError(f"{label} contains an empty record")
                digest.update(line)
                size += len(line)
                if collect_blob_references:
                    referenced.update(_referenced_blobs(line, first_line=count))
            after = os.fstat(handle.fileno())
        path_after = path.stat()
    except OSError as exc:
        raise SceneStreamIntegrityError(f"cannot read {label}: {path}") from exc
    before_identity = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
    after_identity = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    path_identity = (
        path_after.st_dev,
        path_after.st_ino,
        path_after.st_size,
        path_after.st_mtime_ns,
    )
    if before_identity != after_identity or before_identity != path_identity:
        raise SceneStreamIntegrityError(f"{label} changed while it was being verified")
    if size != expected_size:
        raise SceneStreamIntegrityError(f"{label} size does not match the terminal result")
    if digest.hexdigest() != expected_sha256:
        raise SceneStreamIntegrityError(f"{label} SHA-256 does not match the terminal result")
    if count != expected_count:
        raise SceneStreamIntegrityError(f"{label} count does not match the terminal result")
    return referenced


def _referenced_blobs(changes: bytes, *, first_line: int = 1) -> set[str]:
    referenced: set[str] = set()
    for line_number, line in enumerate(changes.splitlines(), start=first_line):
        try:
            value = json.loads(line)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise SceneStreamIntegrityError(
                f"scene changes line {line_number} is not valid JSON"
            ) from exc
        if not isinstance(value, dict):
            raise SceneStreamIntegrityError(
                f"scene changes line {line_number} must contain an object"
            )
        digest = value.get("blob_sha256")
        if digest is None:
            body = value.get("body")
            if isinstance(body, dict):
                digest = body.get("blob_sha256", body.get("blob"))
        if digest is None:
            continue
        if not _is_sha256(digest):
            raise SceneStreamIntegrityError(
                f"scene changes line {line_number} has an invalid blob SHA-256"
            )
        referenced.add(digest)
    return referenced


def _verify_blobs(
    directory: Path, referenced: set[str], expected: SceneStreamIntegrity
) -> None:
    try:
        if directory.is_symlink() or not directory.is_dir():
            raise SceneStreamIntegrityError(
                "scene blobs must be a regular non-symlink directory"
            )
        entries = list(directory.iterdir())
    except OSError as exc:
        raise SceneStreamIntegrityError(f"cannot enumerate scene blobs: {directory}") from exc
    actual: set[str] = set()
    compressed_bytes = 0
    for entry in entries:
        name = entry.name
        digest = name[:-5] if name.endswith(".zlib") else ""
        if not _is_sha256(digest):
            raise SceneStreamIntegrityError(f"unexpected scene blob entry: {name}")
        compressed = _safe_file_bytes(entry, f"scene blob {digest}")
        compressed_bytes += len(compressed)
        inflater = zlib.decompressobj()
        try:
            uncompressed = inflater.decompress(
                compressed, MAX_BLOB_UNCOMPRESSED_BYTES + 1
            )
            if len(uncompressed) > MAX_BLOB_UNCOMPRESSED_BYTES or inflater.unconsumed_tail:
                raise SceneStreamIntegrityError(f"scene blob {digest} exceeds the size limit")
            uncompressed += inflater.flush()
        except zlib.error as exc:
            raise SceneStreamIntegrityError(f"scene blob {digest} is not valid zlib data") from exc
        if (
            len(uncompressed) > MAX_BLOB_UNCOMPRESSED_BYTES
            or not inflater.eof
            or inflater.unused_data
        ):
            raise SceneStreamIntegrityError(f"scene blob {digest} has an invalid zlib boundary")
        if hashlib.sha256(uncompressed).hexdigest() != digest:
            raise SceneStreamIntegrityError(f"scene blob content does not match its name: {digest}")
        actual.add(digest)
    if actual != referenced:
        raise SceneStreamIntegrityError(
            "scene blob files do not exactly match the blobs referenced by changes"
        )
    if len(actual) != expected.blob_count:
        raise SceneStreamIntegrityError("scene blob count does not match the terminal result")
    if compressed_bytes != expected.blob_bytes:
        raise SceneStreamIntegrityError("scene blob bytes do not match the terminal result")


def verify_scene_stream(directory: Path, expected: SceneStreamIntegrity) -> None:
    if directory.is_symlink() or not directory.is_dir():
        raise SceneStreamIntegrityError(f"scene stream is not a safe directory: {directory}")
    _verify_index(
        directory / expected.frames_index,
        label="scene frames index",
        expected_size=expected.frames_size_bytes,
        expected_sha256=expected.frames_sha256,
        expected_count=expected.frame_count,
    )
    referenced = _verify_index(
        directory / expected.changes_index,
        label="scene changes index",
        expected_size=expected.changes_size_bytes,
        expected_sha256=expected.changes_sha256,
        expected_count=expected.change_count,
        collect_blob_references=True,
    )
    _verify_blobs(directory / expected.blobs_directory, referenced, expected)


__all__ = [
    "SCENE_STREAM_FORMAT",
    "SceneStreamIntegrity",
    "SceneStreamIntegrityError",
    "VerifiedSceneStream",
    "freeze_json_value",
    "scene_stream_integrity_from_mapping",
    "verify_scene_stream",
]
