from __future__ import annotations

import hashlib
import json
import uuid
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from .errors import RecorderError

MAX_LEDGER_BYTES = 8 * 1024 * 1024
MAX_ARCHIVE_METADATA_BYTES = 1024 * 1024
HOTBAR_SNAPSHOT_CONTRACT = "item_stack_copy_v1"


class ReplayNotReadyError(RecorderError):
    """The exact replay exists but ServerReplay has not published immutable bytes yet."""


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

    def as_json(self) -> dict[str, Any]:
        value = asdict(self)
        value["path"] = str(self.path)
        return value


def _canonical_uuid(value: object, label: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError) as exc:
        raise RecorderError(f"invalid {label}: {value!r}") from exc


def _session_id(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 128
        or value.startswith(".")
        or "/" in value
        or "\\" in value
        or any(ord(character) < 32 for character in value)
    ):
        raise RecorderError(f"invalid episode id: {value!r}")
    return value


def _read_ledger(path: Path) -> dict[str, Any]:
    try:
        if path.is_symlink() or not path.is_file():
            raise RecorderError(f"replay segment ledger is unavailable: {path}")
        before = path.stat()
        if before.st_size > MAX_LEDGER_BYTES:
            raise RecorderError(f"replay segment ledger is too large: {path}")
        raw = path.read_bytes()
        after = path.stat()
    except OSError as exc:
        raise RecorderError(f"cannot read replay segment ledger: {path}") from exc
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ):
        raise RecorderError(f"replay segment ledger changed while it was read: {path}")
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError, RecursionError) as exc:
        raise RecorderError(f"replay segment ledger is invalid JSON: {path}") from exc
    if not isinstance(value, dict):
        raise RecorderError(f"replay segment ledger must be an object: {path}")
    return value


def _host_replay_path(raw: object, replays_root: Path) -> Path:
    if not isinstance(raw, str) or not raw:
        raise RecorderError("saved replay segment has no output path")
    root = replays_root.resolve()
    container_path = PurePosixPath(raw)
    if container_path.is_absolute() and container_path.parts[:2] == ("/", "replays"):
        relative_parts = container_path.parts[2:]
        unresolved = root.joinpath(*relative_parts)
    else:
        unresolved = Path(raw)
        if not unresolved.is_absolute():
            raise RecorderError("saved replay output path must be absolute")
    if unresolved.is_symlink():
        raise RecorderError(f"saved replay may not be a symlink: {unresolved}")
    try:
        candidate = unresolved.resolve(strict=True)
        candidate.relative_to(root)
    except (OSError, ValueError) as exc:
        raise RecorderError(f"saved replay escapes the configured replay root: {unresolved}") from exc
    cursor = root
    for part in candidate.relative_to(root).parts[:-1]:
        cursor /= part
        if cursor.is_symlink():
            raise RecorderError(f"saved replay traverses a symlinked directory: {unresolved}")
    if not candidate.is_file():
        raise RecorderError(f"saved replay is not a regular file: {candidate}")
    return candidate


def _stable_digest(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    try:
        before = path.stat()
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
        after = path.stat()
    except OSError as exc:
        raise RecorderError(f"cannot hash replay archive: {path}") from exc
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ):
        raise RecorderError(f"replay archive changed while it was hashed: {path}")
    if after.st_size <= 0:
        raise RecorderError(f"replay archive is empty: {path}")
    return digest.hexdigest(), after.st_size


def _archive_identity(path: Path) -> dict[str, Any]:
    try:
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
            flashback_metadata = [item for item in entries if item.filename == "metadata.json"]
            matching = [
                item for item in entries if item.filename == "arcade_replay_meta.json"
            ]
            if len(flashback_metadata) != 1 or not any(
                item.filename.endswith(".flashback") for item in entries
            ):
                raise RecorderError(f"replay is not a complete Flashback archive: {path}")
            if len(matching) != 1:
                raise RecorderError(
                    f"ServerReplay archive must contain one arcade_replay_meta.json: {path}"
                )
            item = matching[0]
            if item.file_size > MAX_ARCHIVE_METADATA_BYTES:
                raise RecorderError(f"ServerReplay metadata is too large: {path}")
            raw = archive.read(item)
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        raise RecorderError(f"replay is not a readable Flashback archive: {path}") from exc
    try:
        metadata = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError, RecursionError) as exc:
        raise RecorderError(f"ServerReplay metadata is invalid JSON: {path}") from exc
    if not isinstance(metadata, dict) or not isinstance(metadata.get("mc_recorder"), dict):
        raise RecorderError(f"replay lacks exact mc_recorder segment identity: {path}")
    return metadata["mc_recorder"]


def resolve_replay_segments(
    *,
    control_root: Path,
    replays_root: Path,
    session_id: str,
    player_uuid: str,
    connection_id: str,
) -> list[ReplaySegmentSource]:
    """Resolve and integrity-envelope every saved segment for one exact connection."""

    session = _session_id(session_id)
    player = _canonical_uuid(player_uuid, "player UUID")
    connection = _canonical_uuid(connection_id, "connection UUID")
    ledger_path = control_root / "sessions" / f"{session}.replay-segments.json"
    ledger = _read_ledger(ledger_path)
    if ledger.get("schema_version") != 1 or ledger.get("session_id") != session:
        raise RecorderError("replay segment ledger does not match the requested session")
    raw_segments = ledger.get("segments")
    if not isinstance(raw_segments, list):
        raise RecorderError("replay segment ledger has no segments array")

    sources: list[ReplaySegmentSource] = []
    seen_ids: set[str] = set()
    seen_ordinals: set[int] = set()
    for raw in raw_segments:
        if not isinstance(raw, dict):
            raise RecorderError("replay segment ledger contains a non-object row")
        if raw.get("player_uuid") != player or raw.get("connection_id") != connection:
            continue
        if raw.get("state") != "saved":
            continue
        segment_id = _canonical_uuid(raw.get("segment_id"), "replay segment ID")
        ordinal = raw.get("segment_ordinal")
        if not isinstance(ordinal, int) or isinstance(ordinal, bool) or ordinal < 0:
            raise RecorderError(f"replay segment {segment_id} has an invalid ordinal")
        if segment_id in seen_ids or ordinal in seen_ordinals:
            raise RecorderError("replay segment ledger has duplicate identities or ordinals")
        seen_ids.add(segment_id)
        seen_ordinals.add(ordinal)

        path = _host_replay_path(raw.get("output"), replays_root)
        archive_identity = _archive_identity(path)
        expected_identity = {
            "session_id": session,
            "segment_id": segment_id,
            "segment_ordinal": ordinal,
            "player_uuid": player,
            "connection_id": connection,
        }
        if any(archive_identity.get(key) != value for key, value in expected_identity.items()):
            raise RecorderError(f"replay archive identity does not match its segment ledger: {path}")
        archive_schema = archive_identity.get("schema_version")
        ledger_hotbar_contract = raw.get("hotbar_snapshot_contract")
        archive_hotbar_contract = archive_identity.get("hotbar_snapshot_contract")
        if archive_schema == 1:
            if ledger_hotbar_contract is not None or archive_hotbar_contract is not None:
                raise RecorderError(
                    f"legacy replay archive has inconsistent hotbar snapshot metadata: {path}"
                )
        elif archive_schema == 2:
            if (
                ledger_hotbar_contract != HOTBAR_SNAPSHOT_CONTRACT
                or archive_hotbar_contract != HOTBAR_SNAPSHOT_CONTRACT
            ):
                raise RecorderError(
                    f"replay archive hotbar snapshot contract does not match its segment ledger: {path}"
                )
        else:
            raise RecorderError(f"replay archive has an unsupported mc_recorder schema: {path}")
        sha256, size_bytes = _stable_digest(path)
        declared_size = raw.get("output_size_bytes")
        if declared_size is not None and declared_size != size_bytes:
            raise RecorderError(f"replay archive size does not match its segment ledger: {path}")
        replay_format = raw.get("replay_format")
        if not isinstance(replay_format, str) or replay_format.lower() != "flashback":
            raise RecorderError(f"replay segment {segment_id} is not a Flashback archive")
        sources.append(
            ReplaySegmentSource(
                segment_id=segment_id,
                segment_ordinal=ordinal,
                player_uuid=player,
                connection_id=connection,
                path=path,
                replay_format="flashback",
                sha256=sha256,
                size_bytes=size_bytes,
            )
        )

    if not sources:
        pending = any(
            isinstance(raw, dict)
            and raw.get("player_uuid") == player
            and raw.get("connection_id") == connection
            and raw.get("state") == "recording"
            for raw in raw_segments
        )
        if pending:
            raise ReplayNotReadyError("the exact replay segment is still being saved")
        raise RecorderError("no exact saved replay segments exist for this connection")
    return sorted(sources, key=lambda source: source.segment_ordinal)
