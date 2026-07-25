from __future__ import annotations

import hashlib
import json
import os
import stat
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from minerec.errors import RecorderError

FLASHBACK_CAPTURE_CONTRACT = "client_visible_scene_v1"
MAX_REPLAY_METADATA_BYTES = 4 * 1024 * 1024


@dataclass(frozen=True)
class ReplaySource:
    replay_id: str
    player_uuid: str
    connection_id: str
    path: Path
    replay_format: str
    sha256: str
    size_bytes: int
    flashback_capture_contract: str

    def as_json(self) -> dict[str, Any]:
        return {
            "replay_id": self.replay_id,
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


def replay_source(path: Path) -> ReplaySource:
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
    replay_id = _canonical_uuid(recorder.get("replay_id"), "replay ID")
    player_uuid = _canonical_uuid(recorder.get("player_uuid"), "replay player UUID")
    connection_id = _canonical_uuid(recorder.get("connection_id"), "replay connection ID")
    contract = recorder.get("flashback_capture_contract")
    if contract != FLASHBACK_CAPTURE_CONTRACT:
        raise RecorderError(f"replay requires {FLASHBACK_CAPTURE_CONTRACT}")
    digest, size = _stable_digest(replay)
    return ReplaySource(
        replay_id=replay_id,
        player_uuid=player_uuid,
        connection_id=connection_id,
        path=replay,
        replay_format="flashback",
        sha256=digest,
        size_bytes=size,
        flashback_capture_contract=contract,
    )


def verified_replay_source(path: Path, *, player_uuid: str, connection_id: str) -> ReplaySource:
    player = _canonical_uuid(player_uuid, "player UUID")
    connection = _canonical_uuid(connection_id, "connection UUID")
    source = replay_source(path)
    if source.player_uuid != player or source.connection_id != connection:
        raise RecorderError("replay input identity does not match the requested connection")
    return source


__all__ = [
    "FLASHBACK_CAPTURE_CONTRACT",
    "ReplaySource",
    "replay_source",
    "verified_replay_source",
]
