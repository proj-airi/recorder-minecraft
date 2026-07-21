from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator

from .errors import RecorderError


_EPOCH_RE = re.compile(r"^epoch-(\d+)$")
_REQUIRED_EVENT_FIELDS = (
    "schema_version",
    "record_type",
    "session_id",
    "epoch_index",
    "server_tick",
    "sequence",
    "recorded_at_ns",
)


@dataclass(frozen=True)
class EpochInfo:
    index: int
    path: Path
    status: str
    size_bytes: int
    first_tick: int | None
    last_tick: int | None
    event_count: int | None


@dataclass(frozen=True)
class EpisodeInfo:
    session_id: str
    path: Path
    status: str
    size_bytes: int
    epoch_count: int
    sealed_epoch_count: int
    active_epoch_count: int
    first_tick: int | None
    last_tick: int | None


@dataclass(frozen=True)
class ValidationIssue:
    severity: str
    path: str
    message: str


@dataclass(frozen=True)
class ValidationResult:
    session_id: str
    valid: bool
    sealed_epochs: int
    active_epochs: int
    event_count: int
    issues: tuple[ValidationIssue, ...]

    def as_json(self) -> dict[str, Any]:
        result = asdict(self)
        result["issues"] = [asdict(issue) for issue in self.issues]
        return result


def directory_size(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file() or path.is_symlink():
        try:
            return path.stat().st_size
        except OSError:
            return 0
    total = 0
    for entry in path.rglob("*"):
        if entry.is_symlink() or not entry.is_file():
            continue
        try:
            total += entry.stat().st_size
        except OSError:
            continue
    return total


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _optional_int(mapping: dict[str, Any], *keys: str) -> int | None:
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return None


def _manifest_int(manifest: dict[str, Any], key: str) -> int | None:
    value = manifest.get(key)
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None


def _count_event_records(path: Path) -> int:
    with path.open("rb") as handle:
        return sum(1 for line in handle if line.strip())


def _has_verified_seal(path: Path, manifest: dict[str, Any]) -> bool:
    """Return true only for the immutable envelope written by the recorder.

    A filename is not a commit marker.  Retention and export may treat an epoch
    as sealed only after all manifest integrity fields match the final file.
    """

    events = path / "events.jsonl"
    if manifest.get("sealed") is not True or not events.is_file() or events.is_symlink():
        return False
    record_count = _manifest_int(manifest, "record_count")
    events_bytes = _manifest_int(manifest, "events_bytes")
    events_sha256 = manifest.get("events_sha256")
    if record_count is None or events_bytes is None:
        return False
    if not isinstance(events_sha256, str) or re.fullmatch(r"[0-9a-fA-F]{64}", events_sha256) is None:
        return False
    try:
        before = events.stat()
        if before.st_size != events_bytes:
            return False
        if _count_event_records(events) != record_count:
            return False
        if sha256_file(events).lower() != events_sha256.lower():
            return False
        after = events.stat()
        return (
            before.st_dev == after.st_dev
            and before.st_ino == after.st_ino
            and before.st_size == after.st_size
            and before.st_mtime_ns == after.st_mtime_ns
        )
    except OSError:
        return False


def inspect_epoch(path: Path) -> EpochInfo | None:
    match = _EPOCH_RE.fullmatch(path.name)
    if not match or not path.is_dir() or path.is_symlink():
        return None
    manifest_path = path / "manifest.json"
    manifest = (
        _read_json(manifest_path)
        if manifest_path.is_file() and not manifest_path.is_symlink()
        else None
    )
    manifest = manifest or {}
    has_active = (path / "events.jsonl.inprogress").exists()
    if has_active:
        status = "active"
    elif _has_verified_seal(path, manifest):
        status = "sealed"
    else:
        status = "incomplete"
    return EpochInfo(
        index=int(match.group(1)),
        path=path,
        status=status,
        size_bytes=directory_size(path),
        first_tick=_optional_int(manifest, "first_server_tick", "first_tick", "start_tick", "start_server_tick"),
        last_tick=_optional_int(manifest, "last_server_tick", "last_tick", "end_tick", "end_server_tick"),
        event_count=_manifest_int(manifest, "record_count"),
    )


def iter_epochs(episode: Path) -> Iterator[EpochInfo]:
    epochs_dir = episode / "epochs"
    if not epochs_dir.is_dir():
        return
    infos = [info for child in epochs_dir.iterdir() if (info := inspect_epoch(child))]
    yield from sorted(infos, key=lambda item: item.index)


def _terminal_session_status(path: Path, session_id: str, *, has_unsealed_epochs: bool) -> str | None:
    end_path = path / "session_end.json"
    if not end_path.is_file() or end_path.is_symlink():
        return None
    end = _read_json(end_path)
    if end is None or end.get("session_id") not in (None, session_id):
        return None
    raw_status = end.get("status")
    status = raw_status.strip().lower() if isinstance(raw_status, str) else ""
    clean_shutdown = end.get("clean_shutdown")
    if status == "failed":
        return "failed"
    if status == "incomplete" or clean_shutdown is False:
        return "incomplete"
    # A terminal clean marker cannot make an unsealed epoch complete.  Report
    # the inconsistency as incomplete instead of making it look live forever.
    if status == "complete" and clean_shutdown is True and has_unsealed_epochs:
        return "incomplete"
    return None


def inspect_episode(path: Path) -> EpisodeInfo | None:
    if not path.is_dir() or path.is_symlink() or path.name.startswith("."):
        return None
    manifest = _read_json(path / "manifest.json") or {}
    epochs = list(iter_epochs(path))
    sealed = [epoch for epoch in epochs if epoch.status == "sealed"]
    active = [epoch for epoch in epochs if epoch.status == "active"]
    incomplete = [epoch for epoch in epochs if epoch.status == "incomplete"]
    session_id = manifest.get("session_id", path.name)
    if not isinstance(session_id, str):
        session_id = path.name
    terminal_status = _terminal_session_status(
        path,
        session_id,
        has_unsealed_epochs=bool(active or incomplete),
    )
    if terminal_status is not None:
        status = terminal_status
    elif active:
        status = "active"
    elif incomplete:
        status = "incomplete"
    elif epochs:
        status = "sealed"
    else:
        status = str(manifest.get("status", "empty"))
    first_ticks = [epoch.first_tick for epoch in epochs if epoch.first_tick is not None]
    last_ticks = [epoch.last_tick for epoch in epochs if epoch.last_tick is not None]
    return EpisodeInfo(
        session_id=session_id,
        path=path,
        status=status,
        size_bytes=directory_size(path),
        epoch_count=len(epochs),
        sealed_epoch_count=len(sealed),
        active_epoch_count=len(active),
        first_tick=min(first_ticks) if first_ticks else None,
        last_tick=max(last_ticks) if last_ticks else None,
    )


def list_episodes(captures_root: Path) -> list[EpisodeInfo]:
    if not captures_root.exists():
        return []
    if not captures_root.is_dir():
        raise RecorderError(f"capture root is not a directory: {captures_root}")
    episodes = [info for child in captures_root.iterdir() if (info := inspect_episode(child))]
    return sorted(episodes, key=lambda item: item.path.stat().st_mtime_ns, reverse=True)


def resolve_episode(captures_root: Path, session_id: str) -> Path:
    if not session_id or session_id.startswith(".") or "/" in session_id or "\\" in session_id:
        raise RecorderError(f"invalid episode id: {session_id!r}")
    root = captures_root.resolve()
    unresolved = root / session_id
    if unresolved.is_symlink():
        raise RecorderError(f"episode not found: {session_id}")
    candidate = unresolved.resolve()
    if candidate.parent != root or not candidate.is_dir():
        raise RecorderError(f"episode not found: {session_id}")
    return candidate


def iter_events(epoch: EpochInfo) -> Iterator[tuple[int, dict[str, Any]]]:
    if epoch.status != "sealed":
        return
    with (epoch.path / "events.jsonl").open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise RecorderError(f"{epoch.path / 'events.jsonl'}:{line_number}: invalid JSON: {exc.msg}") from exc
            if not isinstance(record, dict):
                raise RecorderError(f"{epoch.path / 'events.jsonl'}:{line_number}: event must be an object")
            yield line_number, record


def validate_episode(
    episode: Path,
    *,
    epochs: Iterable[EpochInfo] | None = None,
) -> ValidationResult:
    issues: list[ValidationIssue] = []
    manifest_path = episode / "manifest.json"
    manifest = _read_json(manifest_path)
    if manifest is None:
        issues.append(ValidationIssue("error", str(manifest_path), "missing or invalid session manifest"))
        session_id = episode.name
    else:
        raw_session_id = manifest.get("session_id", episode.name)
        session_id = raw_session_id if isinstance(raw_session_id, str) else episode.name
        if raw_session_id != episode.name:
            issues.append(
                ValidationIssue("warning", str(manifest_path), "manifest session_id differs from directory name")
            )

    epoch_infos = list(iter_epochs(episode) if epochs is None else epochs)
    if not epoch_infos:
        issues.append(ValidationIssue("error", str(episode / "epochs"), "no epoch directories found"))
    seen_indexes: set[int] = set()
    sealed_count = 0
    active_count = 0
    event_count = 0
    previous_sequence: int | None = None
    previous_tick: int | None = None

    for epoch in epoch_infos:
        relative = str(epoch.path.relative_to(episode))
        if epoch.index in seen_indexes:
            issues.append(ValidationIssue("error", relative, f"duplicate epoch index {epoch.index}"))
        seen_indexes.add(epoch.index)
        if epoch.status == "active":
            active_count += 1
            issues.append(ValidationIssue("warning", relative, "active epoch is not exportable until sealed"))
            continue
        if epoch.status != "sealed":
            issues.append(
                ValidationIssue(
                    "error",
                    relative,
                    "epoch is not sealed or its manifest integrity envelope does not match events.jsonl",
                )
            )
            continue
        sealed_count += 1
        try:
            records = iter_events(epoch)
            for line_number, record in records:
                event_count += 1
                location = f"{relative}/events.jsonl:{line_number}"
                for field in _REQUIRED_EVENT_FIELDS:
                    if field not in record:
                        issues.append(ValidationIssue("error", location, f"missing event field {field!r}"))
                if record.get("session_id") != session_id:
                    issues.append(ValidationIssue("error", location, "event session_id does not match manifest"))
                if record.get("epoch_index") != epoch.index:
                    issues.append(ValidationIssue("error", location, "event epoch_index does not match directory"))
                sequence = record.get("sequence")
                tick = record.get("server_tick")
                if isinstance(sequence, int) and not isinstance(sequence, bool):
                    if previous_sequence is not None and sequence <= previous_sequence:
                        issues.append(ValidationIssue("error", location, "global event sequence is not strictly increasing"))
                    previous_sequence = sequence
                if isinstance(tick, int) and not isinstance(tick, bool):
                    if previous_tick is not None and tick < previous_tick:
                        issues.append(ValidationIssue("error", location, "server tick moved backwards"))
                    previous_tick = tick
        except (OSError, RecorderError) as exc:
            issues.append(ValidationIssue("error", relative, str(exc)))

        epoch_manifest = _read_json(epoch.path / "manifest.json") or {}
        if epoch_manifest.get("session_id") not in (None, session_id):
            issues.append(ValidationIssue("error", relative, "epoch manifest session_id does not match session"))
        if epoch_manifest.get("epoch_index") not in (None, epoch.index):
            issues.append(ValidationIssue("error", relative, "epoch manifest index does not match directory"))
        expected_count = _optional_int(epoch_manifest, "event_count", "events", "record_count")
        if expected_count is not None:
            actual_count = 0
            try:
                with (epoch.path / "events.jsonl").open("r", encoding="utf-8") as handle:
                    actual_count = sum(1 for line in handle if line.strip())
            except OSError:
                pass
            if actual_count != expected_count:
                issues.append(
                    ValidationIssue(
                        "error", relative, f"manifest event count {expected_count} does not match {actual_count}"
                    )
                )
        expected_bytes = _optional_int(epoch_manifest, "events_bytes")
        events_path = epoch.path / "events.jsonl"
        if expected_bytes is not None:
            try:
                actual_bytes = events_path.stat().st_size
            except OSError:
                actual_bytes = -1
            if actual_bytes != expected_bytes:
                issues.append(
                    ValidationIssue(
                        "error", relative, f"manifest byte count {expected_bytes} does not match {actual_bytes}"
                    )
                )
        expected_sha = epoch_manifest.get("events_sha256")
        if isinstance(expected_sha, str):
            try:
                actual_sha = sha256_file(events_path)
            except OSError:
                actual_sha = ""
            if actual_sha != expected_sha:
                issues.append(ValidationIssue("error", relative, "events SHA-256 does not match manifest"))

    valid = not any(issue.severity == "error" for issue in issues)
    return ValidationResult(
        session_id=session_id,
        valid=valid,
        sealed_epochs=sealed_count,
        active_epochs=active_count,
        event_count=event_count,
        issues=tuple(issues),
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
