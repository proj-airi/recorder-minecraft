from __future__ import annotations

import hashlib
import json
import os
import stat
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from minerec.errors import RecorderError

FLASHBACK_CAPTURE_CONTRACT = "client_visible_scene_v1"
MAX_REPLAY_METADATA_BYTES = 4 * 1024 * 1024


@dataclass(frozen=True)
class ReplaySegmentSource:
    segment_id: str
    segment_ordinal: int
    player_uuid: str
    connection_id: str
    path: Path
    replay_format: str
    sha256: str
    size_bytes: int
    flashback_capture_contract: str

    def as_json(self) -> dict[str, Any]:
        return {
            "segment_id": self.segment_id,
            "segment_ordinal": self.segment_ordinal,
            "player_uuid": self.player_uuid,
            "connection_id": self.connection_id,
            "replay_format": self.replay_format,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "flashback_capture_contract": self.flashback_capture_contract,
        }


def _canonical_uuid(value: object, label: str) -> str:
    try:
        canonical = str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError) as exc:
        raise RecorderError(f"invalid {label}: {value!r}") from exc
    if value != canonical:
        raise RecorderError(f"{label} must use canonical UUID spelling")
    return canonical


def _stable_digest(path: Path) -> tuple[str, int]:
    before = path.lstat()
    digest = hashlib.sha256()
    observed = 0
    try:
        with path.open("rb") as handle:
            opened = os.fstat(handle.fileno())
            if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
                raise RecorderError("replay changed while it was being opened")
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
                observed += len(chunk)
            after_open = os.fstat(handle.fileno())
        after = path.lstat()
    except OSError as exc:
        raise RecorderError(f"cannot read replay: {path}") from exc
    identities = {
        (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns),
        (after_open.st_dev, after_open.st_ino, after_open.st_size, after_open.st_mtime_ns),
        (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns),
    }
    if len(identities) != 1 or observed != before.st_size:
        raise RecorderError("replay changed while it was being read")
    return digest.hexdigest(), observed


def replay_segment_source(path: Path) -> ReplaySegmentSource:
    unresolved = path.expanduser()
    try:
        status = unresolved.lstat()
    except OSError as exc:
        raise RecorderError(f"cannot inspect replay: {unresolved}") from exc
    if stat.S_ISLNK(status.st_mode) or not stat.S_ISREG(status.st_mode):
        raise RecorderError(f"replay must be a non-symlinked regular file: {unresolved}")
    replay = unresolved.resolve()
    try:
        with zipfile.ZipFile(replay) as archive:
            info = archive.getinfo("metadata.json")
            if info.file_size > MAX_REPLAY_METADATA_BYTES:
                raise RecorderError("Flashback metadata exceeds the byte limit")
            value = json.loads(archive.read(info))
    except RecorderError:
        raise
    except (KeyError, OSError, ValueError, zipfile.BadZipFile) as exc:
        raise RecorderError(f"replay is not a readable Flashback ZIP: {replay}") from exc
    if not isinstance(value, dict) or not isinstance(value.get("chunks"), dict):
        raise RecorderError("replay does not contain Flashback metadata")
    recorder = value.get("mc_recorder")
    if not isinstance(recorder, dict):
        raise RecorderError("Flashback replay lacks mc_recorder identity metadata")
    segment_id = _canonical_uuid(recorder.get("segment_id"), "replay segment ID")
    player_uuid = _canonical_uuid(recorder.get("player_uuid"), "replay player UUID")
    connection_id = _canonical_uuid(recorder.get("connection_id"), "replay connection ID")
    ordinal = recorder.get("segment_ordinal")
    if not isinstance(ordinal, int) or isinstance(ordinal, bool) or ordinal < 0:
        raise RecorderError("replay segment ordinal must be a non-negative integer")
    contract = recorder.get("flashback_capture_contract")
    if contract != FLASHBACK_CAPTURE_CONTRACT:
        raise RecorderError(f"replay requires {FLASHBACK_CAPTURE_CONTRACT}")
    digest, size = _stable_digest(replay)
    return ReplaySegmentSource(
        segment_id=segment_id,
        segment_ordinal=ordinal,
        player_uuid=player_uuid,
        connection_id=connection_id,
        path=replay,
        replay_format="flashback",
        sha256=digest,
        size_bytes=size,
        flashback_capture_contract=contract,
    )


def resolve_replay_segments(
    replays: Iterable[Path],
    *,
    player_uuid: str,
    connection_id: str,
) -> tuple[ReplaySegmentSource, ...]:
    player = _canonical_uuid(player_uuid, "player UUID")
    connection = _canonical_uuid(connection_id, "connection UUID")
    sources = tuple(replay_segment_source(path) for path in replays)
    if not sources:
        raise RecorderError("at least one explicit Flashback replay input is required")
    if any(source.player_uuid != player or source.connection_id != connection for source in sources):
        raise RecorderError("replay input identity does not match the requested connection")
    ordinals = [source.segment_ordinal for source in sources]
    segment_ids = [source.segment_id for source in sources]
    if len(set(ordinals)) != len(ordinals) or len(set(segment_ids)) != len(segment_ids):
        raise RecorderError("replay inputs repeat a segment ordinal or ID")
    return tuple(sorted(sources, key=lambda source: source.segment_ordinal))


__all__ = [
    "FLASHBACK_CAPTURE_CONTRACT",
    "ReplaySegmentSource",
    "replay_segment_source",
    "resolve_replay_segments",
]
