from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import uuid
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from minerec.errors import RecorderError
from minerec.processing.capture.episodes import inspect_epoch

SNAPSHOT_FORMAT = "append_prefix_v1"
_READ_CHUNK_BYTES = 1024 * 1024


@dataclass(frozen=True)
class CaptureSnapshot:
    episode: Path
    source_episode: Path
    session_id: str
    player_uuid: str
    connection_id: str
    provenance: dict[str, Any]


@dataclass(frozen=True)
class _SourceBytes:
    data: bytes
    observed_size: int
    device: int
    inode: int


def _regular_path(path: Path, description: str) -> None:
    if path.is_symlink():
        raise RecorderError(f"{description} may not be a symlink: {path}")
    if not path.is_file():
        raise RecorderError(f"{description} is not a regular file: {path}")


def _read_object(path: Path, description: str) -> dict[str, Any]:
    _regular_path(path, description)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RecorderError(f"{description} is not valid JSON: {path}") from exc
    if not isinstance(value, dict):
        raise RecorderError(f"{description} must be a JSON object: {path}")
    return value


def _read_exact(handle: int, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = os.read(handle, min(_READ_CHUNK_BYTES, remaining))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _stable_source_bytes(path: Path, *, active: bool) -> _SourceBytes:
    _regular_path(path, "capture events")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise RecorderError(f"cannot open capture events: {path}") from exc
    try:
        before = os.fstat(descriptor)
        observed = _read_exact(descriptor, before.st_size)
    finally:
        os.close(descriptor)
    if len(observed) != before.st_size:
        raise RecorderError(f"capture events were truncated while reading: {path}")

    retained = observed
    if active and retained and not retained.endswith(b"\n"):
        newline = retained.rfind(b"\n")
        retained = retained[: newline + 1] if newline >= 0 else b""

    try:
        after = path.stat()
    except OSError as exc:
        raise RecorderError(f"capture events disappeared while reading: {path}") from exc
    if after.st_dev != before.st_dev or after.st_ino != before.st_ino:
        raise RecorderError(f"capture events were replaced while reading: {path}")
    if after.st_size < before.st_size:
        raise RecorderError(f"capture events were truncated while reading: {path}")
    if not active and (
        after.st_size != before.st_size or after.st_mtime_ns != before.st_mtime_ns
    ):
        raise RecorderError(f"finalized capture events changed while reading: {path}")

    try:
        verify_descriptor = os.open(path, flags)
    except OSError as exc:
        raise RecorderError(f"cannot reopen capture events: {path}") from exc
    try:
        verify_stat = os.fstat(verify_descriptor)
        verified = _read_exact(verify_descriptor, len(retained))
    finally:
        os.close(verify_descriptor)
    if (
        verify_stat.st_dev != before.st_dev
        or verify_stat.st_ino != before.st_ino
        or verified != retained
    ):
        raise RecorderError(f"capture event prefix changed while reading: {path}")
    return _SourceBytes(
        data=retained,
        observed_size=before.st_size,
        device=before.st_dev,
        inode=before.st_ino,
    )


def _parse_records(
    data: bytes,
    *,
    source: Path,
    session_id: str,
    epoch_index: int,
    previous_sequence: int | None,
    previous_tick: int | None,
) -> tuple[list[dict[str, Any]], int | None, int | None]:
    records: list[dict[str, Any]] = []
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RecorderError(f"{source}: capture events are not UTF-8") from exc
    for line_number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise RecorderError(
                f"{source}:{line_number}: invalid JSON: {exc.msg}"
            ) from exc
        if not isinstance(record, dict):
            raise RecorderError(f"{source}:{line_number}: event must be an object")
        if record.get("session_id") != session_id:
            raise RecorderError(
                f"{source}:{line_number}: event session does not match snapshot"
            )
        if record.get("epoch_index") != epoch_index:
            raise RecorderError(
                f"{source}:{line_number}: event epoch does not match directory"
            )
        sequence = record.get("sequence")
        tick = record.get("server_tick")
        if not isinstance(sequence, int) or isinstance(sequence, bool):
            raise RecorderError(f"{source}:{line_number}: event sequence is invalid")
        if not isinstance(tick, int) or isinstance(tick, bool):
            raise RecorderError(f"{source}:{line_number}: event server tick is invalid")
        if previous_sequence is not None and sequence <= previous_sequence:
            raise RecorderError(
                f"{source}:{line_number}: event sequence is not strictly increasing"
            )
        if previous_tick is not None and tick < previous_tick:
            raise RecorderError(f"{source}:{line_number}: server tick moved backwards")
        previous_sequence = sequence
        previous_tick = tick
        records.append(record)
    return records, previous_sequence, previous_tick


def _epoch_manifest(
    *,
    session_id: str,
    epoch_index: int,
    data: bytes,
    records: list[dict[str, Any]],
) -> dict[str, Any]:
    ticks = [int(record["server_tick"]) for record in records]
    sequences = [int(record["sequence"]) for record in records]
    counts = Counter(str(record.get("record_type", "unknown")) for record in records)
    return {
        "schema_version": 1,
        "session_id": session_id,
        "epoch_index": epoch_index,
        "sealed": True,
        "rotation_reason": "consumer_snapshot",
        "forced_seal": False,
        "record_count": len(records),
        "events_bytes": len(data),
        "events_sha256": hashlib.sha256(data).hexdigest(),
        "first_server_tick": min(ticks),
        "last_server_tick": max(ticks),
        "first_sequence": min(sequences),
        "last_sequence": max(sequences),
        "record_counts": dict(sorted(counts.items())),
    }


def _matches_connection(
    record: dict[str, Any],
    *,
    record_type: str,
    player_uuid: str,
    connection_id: str,
) -> bool:
    return (
        record.get("record_type") == record_type
        and record.get("player_uuid") == player_uuid
        and record.get("connection_id") == connection_id
    )


@contextmanager
def snapshot_connection(
    episode: Path,
    runtime_root: Path,
    *,
    player_uuid: str,
    connection_id: str,
) -> Iterator[CaptureSnapshot]:
    """Materialize a verified private episode for one disconnected connection."""

    try:
        canonical_player = str(uuid.UUID(player_uuid))
        canonical_connection = str(uuid.UUID(connection_id))
    except (ValueError, TypeError, AttributeError) as exc:
        raise RecorderError("snapshot player and connection must be UUIDs") from exc

    unresolved_episode = episode.expanduser()
    if unresolved_episode.is_symlink():
        raise RecorderError(f"capture episode may not be a symlink: {unresolved_episode}")
    source_episode = unresolved_episode.resolve()
    if not source_episode.is_dir():
        raise RecorderError(f"capture episode is not a directory: {source_episode}")
    session_manifest_path = source_episode / "manifest.json"
    session_manifest = _read_object(session_manifest_path, "capture session manifest")
    session_id = session_manifest.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        raise RecorderError("capture session manifest has no session_id")

    epochs_root = source_episode / "epochs"
    if epochs_root.is_symlink() or not epochs_root.is_dir():
        raise RecorderError(f"capture epochs root is invalid: {epochs_root}")

    runtime = runtime_root.expanduser().resolve()
    runtime.mkdir(parents=True, exist_ok=True)
    workspace = Path(tempfile.mkdtemp(prefix=".capture-snapshot-", dir=runtime))
    staging = workspace / ".staging"
    snapshot_episode = workspace / session_id
    segments: list[dict[str, Any]] = []
    all_records: list[dict[str, Any]] = []
    previous_sequence: int | None = None
    previous_tick: int | None = None
    try:
        (staging / "epochs").mkdir(parents=True)
        (staging / "manifest.json").write_text(
            json.dumps(session_manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        candidates = sorted(
            (
                path
                for path in epochs_root.iterdir()
                if path.name.startswith("epoch-") and path.is_dir() and not path.is_symlink()
            ),
            key=lambda path: path.name,
        )
        if not candidates:
            raise RecorderError("capture episode has no epoch directories")

        for epoch_path in candidates:
            info = inspect_epoch(epoch_path)
            if info is None:
                raise RecorderError(f"invalid capture epoch directory: {epoch_path}")
            if info.status == "sealed":
                source_path = epoch_path / "events.jsonl"
                kind = "finalized"
                source = _stable_source_bytes(source_path, active=False)
            elif info.status == "active":
                source_path = epoch_path / "events.jsonl.inprogress"
                kind = "active_prefix"
                source = _stable_source_bytes(source_path, active=True)
            else:
                raise RecorderError(f"capture epoch is incomplete: {epoch_path}")
            if not source.data:
                continue
            records, previous_sequence, previous_tick = _parse_records(
                source.data,
                source=source_path,
                session_id=session_id,
                epoch_index=info.index,
                previous_sequence=previous_sequence,
                previous_tick=previous_tick,
            )
            if not records:
                continue
            destination = staging / "epochs" / epoch_path.name
            destination.mkdir()
            (destination / "events.jsonl").write_bytes(source.data)
            manifest = _epoch_manifest(
                session_id=session_id,
                epoch_index=info.index,
                data=source.data,
                records=records,
            )
            (destination / "manifest.json").write_text(
                json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            segments.append(
                {
                    "epoch_index": info.index,
                    "kind": kind,
                    "source": str(source_path.relative_to(source_episode)),
                    "observed_bytes": source.observed_size,
                    "bytes": len(source.data),
                    "sha256": manifest["events_sha256"],
                    "record_count": len(records),
                    "first_sequence": manifest["first_sequence"],
                    "last_sequence": manifest["last_sequence"],
                    "first_server_tick": manifest["first_server_tick"],
                    "last_server_tick": manifest["last_server_tick"],
                }
            )
            all_records.extend(records)

        if not any(
            _matches_connection(
                record,
                record_type="player_join",
                player_uuid=canonical_player,
                connection_id=canonical_connection,
            )
            for record in all_records
        ):
            raise RecorderError("selected connection has no player join record")
        if not any(
            _matches_connection(
                record,
                record_type="player_leave",
                player_uuid=canonical_player,
                connection_id=canonical_connection,
            )
            for record in all_records
        ):
            raise RecorderError("selected connection has no player leave record")

        os.replace(staging, snapshot_episode)
        provenance = {
            "format": SNAPSHOT_FORMAT,
            "source_episode": str(source_episode),
            "session_id": session_id,
            "player_uuid": canonical_player,
            "connection_id": canonical_connection,
            "segments": segments,
        }
        yield CaptureSnapshot(
            episode=snapshot_episode,
            source_episode=source_episode,
            session_id=session_id,
            player_uuid=canonical_player,
            connection_id=canonical_connection,
            provenance=provenance,
        )
    finally:
        shutil.rmtree(workspace, ignore_errors=True)
