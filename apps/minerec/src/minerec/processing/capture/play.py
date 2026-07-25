from __future__ import annotations

import hashlib
import json
import os
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, cast

from minerec.errors import RecorderError

MAX_METADATA_BYTES = 4 * 1024 * 1024
MAX_EVENT_LINE_BYTES = 64 * 1024 * 1024


@dataclass(frozen=True)
class CaptureMetadata:
    path: Path
    session_id: str
    player_uuid: str
    connection_id: str
    start_tick: int
    end_tick: int


@dataclass(frozen=True)
class EventsSource:
    path: Path
    sha256: str
    size_bytes: int
    record_count: int


def _canonical_uuid(value: object, label: str) -> str:
    try:
        canonical = str(uuid.UUID(str(value)))
    except (AttributeError, TypeError, ValueError) as exc:
        raise RecorderError(f"{label} must be a UUID") from exc
    if value != canonical:
        raise RecorderError(f"{label} must use canonical UUID spelling")
    return canonical


def _integer(value: object, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise RecorderError(f"{label} must be a non-negative integer")
    return value


def _object(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RecorderError(f"{label} must be an object")
    return cast("dict[str, Any]", value)


def load_capture_metadata(path: Path) -> CaptureMetadata:
    unresolved = path.expanduser()
    try:
        status = unresolved.lstat()
        if stat.S_ISLNK(status.st_mode) or not stat.S_ISREG(status.st_mode):
            raise RecorderError(f"capture metadata must be a non-symlinked regular file: {unresolved}")
        if status.st_size <= 0 or status.st_size > MAX_METADATA_BYTES:
            raise RecorderError(f"capture metadata must be in 1..{MAX_METADATA_BYTES} bytes")
        before = unresolved.stat()
        raw = unresolved.read_bytes()
        after = unresolved.stat()
    except RecorderError:
        raise
    except OSError as exc:
        raise RecorderError(f"cannot read capture metadata: {unresolved}") from exc
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ) or len(raw) != after.st_size:
        raise RecorderError("capture metadata changed while it was being read")
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RecorderError("capture metadata is not valid UTF-8 JSON") from exc
    root = _object(value, "capture metadata")
    if root.get("schema_version") != 1 or root.get("layout_version") != "v1":
        raise RecorderError("capture metadata has an unsupported schema or layout version")
    player = _object(root.get("player"), "capture player")
    connection = _object(root.get("connection"), "capture connection")
    capture = _object(root.get("capture"), "capture inputs")
    if capture != {
        "events": "capture/events.jsonl",
        "replay": "capture/replay.zip",
        "replay_format": "flashback",
    }:
        raise RecorderError("capture metadata does not declare the canonical primitive inputs")
    if connection.get("end_server_tick") is None:
        raise RecorderError("capture is incomplete; metadata end tick must be written before processing")
    session_id = root.get("session_id")
    if not isinstance(session_id, str) or not session_id or len(session_id) > 160:
        raise RecorderError("capture session_id is invalid")
    start_tick = _integer(connection.get("start_server_tick"), "capture start tick")
    end_tick = _integer(connection.get("end_server_tick"), "capture end tick")
    if end_tick < start_tick:
        raise RecorderError("capture end tick precedes its start tick")
    return CaptureMetadata(
        path=unresolved.resolve(),
        session_id=session_id,
        player_uuid=_canonical_uuid(player.get("uuid"), "capture player UUID"),
        connection_id=_canonical_uuid(connection.get("id"), "capture connection ID"),
        start_tick=start_tick,
        end_tick=end_tick,
    )


def scan_capture_events(
    path: Path,
    metadata: CaptureMetadata,
    visit: Callable[[dict[str, Any]], None],
) -> EventsSource:
    unresolved = path.expanduser()
    try:
        status = unresolved.lstat()
    except OSError as exc:
        raise RecorderError(f"cannot inspect capture events: {unresolved}") from exc
    if stat.S_ISLNK(status.st_mode) or not stat.S_ISREG(status.st_mode):
        raise RecorderError(f"capture events must be a non-symlinked regular file: {unresolved}")
    events = unresolved.resolve()
    digest = hashlib.sha256()
    size = 0
    count = 0
    previous_sequence: int | None = None
    try:
        with events.open("rb") as handle:
            before = os.fstat(handle.fileno())
            for line_number, raw_line in enumerate(handle, 1):
                if len(raw_line) > MAX_EVENT_LINE_BYTES:
                    raise RecorderError(f"{events}:{line_number}: event exceeds the size limit")
                if not raw_line.endswith(b"\n") or raw_line.endswith(b"\r\n"):
                    raise RecorderError(f"{events}:{line_number}: events must use newline-terminated LF records")
                if not raw_line.strip():
                    raise RecorderError(f"{events}:{line_number}: event must not be empty")
                digest.update(raw_line)
                size += len(raw_line)
                count += 1
                try:
                    record = json.loads(raw_line)
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise RecorderError(f"{events}:{line_number}: invalid JSON") from exc
                if not isinstance(record, dict):
                    raise RecorderError(f"{events}:{line_number}: event must be an object")
                if record.get("schema_version") != 1:
                    raise RecorderError(f"{events}:{line_number}: unsupported event schema")
                if record.get("session_id") != metadata.session_id:
                    raise RecorderError(f"{events}:{line_number}: event session does not match metadata")
                if record.get("player_uuid") != metadata.player_uuid or record.get("connection_id") != metadata.connection_id:
                    raise RecorderError(f"{events}:{line_number}: event subject does not match metadata")
                tick = _integer(record.get("server_tick"), f"{events}:{line_number}: server_tick")
                if tick < metadata.start_tick or tick > metadata.end_tick:
                    raise RecorderError(f"{events}:{line_number}: event tick is outside capture bounds")
                sequence = _integer(record.get("sequence"), f"{events}:{line_number}: sequence")
                if previous_sequence is not None and sequence <= previous_sequence:
                    raise RecorderError(f"{events}:{line_number}: event sequence is not strictly increasing")
                previous_sequence = sequence
                visit(record)
            after = os.fstat(handle.fileno())
        path_after = events.stat()
    except RecorderError:
        raise
    except OSError as exc:
        raise RecorderError(f"cannot read capture events: {events}") from exc
    identities = {
        (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns),
        (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns),
        (path_after.st_dev, path_after.st_ino, path_after.st_size, path_after.st_mtime_ns),
    }
    if len(identities) != 1 or size != before.st_size:
        raise RecorderError("capture events changed while they were being read")
    if count == 0:
        raise RecorderError("capture events file is empty")
    return EventsSource(events, digest.hexdigest(), size, count)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


__all__ = ["CaptureMetadata", "EventsSource", "load_capture_metadata", "scan_capture_events", "sha256_file"]
