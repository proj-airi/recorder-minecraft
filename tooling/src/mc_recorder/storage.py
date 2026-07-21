from __future__ import annotations

import json
import fcntl
import os
import shutil
import time
import uuid
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .episodes import directory_size, inspect_epoch
from .errors import RecorderError


DEFAULT_REPLAY_STABLE_SECONDS = 300


@dataclass(frozen=True)
class EvictedEpoch:
    """Compatibility name for an evicted immutable source unit."""

    session_id: str
    epoch: str
    size_bytes: int
    source_kind: str = "capture_epoch"
    source_path: str = ""


@dataclass(frozen=True)
class StorageReport:
    status: str
    before_bytes: int
    after_bytes: int
    quota_bytes: int
    warn_bytes: int
    evicted: tuple[EvictedEpoch, ...]
    message: str

    def as_json(self) -> dict[str, Any]:
        value = asdict(self)
        value["evicted"] = [asdict(item) for item in self.evicted]
        return value


@dataclass(frozen=True)
class _Candidate:
    source_kind: str
    path: Path
    timestamp_ns: int


def _modified_ns(path: Path) -> int:
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return 0


def _epoch_sort_timestamp(path: Path) -> int:
    manifest_path = path / "manifest.json"
    timestamp = 0
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if isinstance(manifest, dict):
            for key in ("ended_at_ns", "end_time_ns", "sealed_at_ns"):
                value = manifest.get(key)
                if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                    timestamp = value
                    break
    except (OSError, json.JSONDecodeError):
        pass
    if timestamp:
        return timestamp
    return _modified_ns(path)


def sealed_epoch_paths(captures_root: Path) -> list[Path]:
    """List only epochs whose immutable envelope currently verifies."""

    if not captures_root.is_dir():
        return []
    candidates: list[Path] = []
    for session in captures_root.iterdir():
        if not session.is_dir() or session.is_symlink() or session.name.startswith("."):
            continue
        epochs_dir = session / "epochs"
        if not epochs_dir.is_dir() or epochs_dir.is_symlink():
            continue
        for child in epochs_dir.iterdir():
            info = inspect_epoch(child)
            if info is not None and info.status == "sealed":
                candidates.append(child)
    return sorted(candidates, key=lambda path: (_epoch_sort_timestamp(path), str(path)))


def _completed_replay(path: Path, root: Path, *, stable_before_ns: int) -> bool:
    if path.suffix.lower() not in {".zip", ".mcpr"} or path.is_symlink():
        return False
    try:
        relative_path = path.relative_to(root)
        cursor = root
        for part in relative_path.parts[:-1]:
            cursor /= part
            if cursor.is_symlink():
                return False
        candidate = path.resolve()
        candidate.relative_to(root)
        before = candidate.stat()
        if not candidate.is_file() or before.st_mtime_ns > stable_before_ns:
            return False
        # Opening the central directory rejects a partially-written ZIP even
        # when its filename already has the final extension.  The structure
        # check also prevents deleting an unrelated ZIP placed under this root.
        with zipfile.ZipFile(candidate, "r") as archive:
            names = {item.filename for item in archive.infolist()}
            is_flashback = "metadata.json" in names and any(
                name.endswith(".flashback") for name in names
            )
            is_replay_mod = "metaData.json" in names and "recording.tmcpr" in names
            if not is_flashback and not is_replay_mod:
                return False
        after = candidate.stat()
        return (
            before.st_dev == after.st_dev
            and before.st_ino == after.st_ino
            and before.st_size == after.st_size
            and before.st_mtime_ns == after.st_mtime_ns
        )
    except (OSError, ValueError, zipfile.BadZipFile):
        return False


def completed_replay_paths(
    replays_root: Path,
    *,
    stable_seconds: int = DEFAULT_REPLAY_STABLE_SECONDS,
    now_ns: int | None = None,
) -> list[Path]:
    """List stable, completed replay archives; never returns directories or temp files."""

    if stable_seconds < 0:
        raise RecorderError("replay stable duration cannot be negative")
    if not replays_root.is_dir() or replays_root.is_symlink():
        return []
    root = replays_root.resolve()
    current_ns = time.time_ns() if now_ns is None else now_ns
    stable_before_ns = current_ns - stable_seconds * 1_000_000_000
    candidates = [
        path
        for path in root.rglob("*")
        if _completed_replay(path, root, stable_before_ns=stable_before_ns)
    ]
    return sorted(candidates, key=lambda path: (_modified_ns(path), str(path)))


def _evict_epoch(captures_root: Path, epoch: Path) -> EvictedEpoch:
    root = captures_root.resolve()
    if epoch.is_symlink():
        raise RecorderError(f"refusing to evict symlinked epoch: {epoch}")
    candidate = epoch.resolve()
    try:
        relative = candidate.relative_to(root)
    except ValueError as exc:
        raise RecorderError(f"refusing to evict path outside capture root: {epoch}") from exc
    if len(relative.parts) != 3 or relative.parts[1] != "epochs":
        raise RecorderError(f"refusing to evict unexpected path: {epoch}")
    # Re-run the complete integrity check immediately before the atomic move.
    info = inspect_epoch(candidate)
    if info is None or info.status != "sealed":
        raise RecorderError(f"refusing to evict unverified epoch: {epoch}")

    size = info.size_bytes
    trash = root / f".evicting-epoch-{uuid.uuid4().hex}"
    candidate.rename(trash)
    try:
        shutil.rmtree(trash)
    except OSError as exc:
        raise RecorderError(f"failed to remove evicted epoch staged at {trash}: {exc}") from exc
    return EvictedEpoch(
        session_id=relative.parts[0],
        epoch=relative.parts[2],
        size_bytes=size,
        source_kind="capture_epoch",
        source_path=str(relative),
    )


def _evict_replay(
    replays_root: Path, replay: Path, *, stable_seconds: int
) -> EvictedEpoch | None:
    root = replays_root.resolve()
    if replay.is_symlink():
        raise RecorderError(f"refusing to evict symlinked replay: {replay}")
    candidate = replay.resolve()
    try:
        relative = candidate.relative_to(root)
    except ValueError as exc:
        raise RecorderError(f"refusing to evict replay outside replay root: {replay}") from exc
    try:
        handle = candidate.open("rb")
    except OSError as exc:
        raise RecorderError(f"could not open replay before eviction: {replay}") from exc
    try:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return None
        if not _completed_replay(
            candidate,
            root,
            stable_before_ns=time.time_ns() - stable_seconds * 1_000_000_000,
        ):
            raise RecorderError(f"refusing to evict active or incomplete replay: {replay}")
        try:
            size = candidate.stat().st_size
        except OSError as exc:
            raise RecorderError(f"could not stat replay before eviction: {replay}") from exc
        trash = root / f".evicting-replay-{uuid.uuid4().hex}"
        candidate.rename(trash)
        try:
            trash.unlink()
        except OSError as exc:
            raise RecorderError(f"failed to remove evicted replay staged at {trash}: {exc}") from exc
    finally:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()
    parent = str(relative.parent) if relative.parent != Path(".") else "replays"
    return EvictedEpoch(
        session_id=parent,
        epoch=relative.name,
        size_bytes=size,
        source_kind="replay_archive",
        source_path=str(relative),
    )


def _replays_root(captures_root: Path, explicit: Path | None) -> Path:
    if explicit is not None:
        return explicit.resolve()
    configured = os.environ.get("MC_RECORDER_REPLAY_ROOT")
    if configured:
        return Path(configured).resolve()
    return (captures_root.parent / "replays").resolve()


def _ensure_disjoint_roots(captures_root: Path, replays_root: Path) -> None:
    if captures_root == replays_root:
        raise RecorderError("capture and replay roots must be different directories")
    try:
        captures_root.relative_to(replays_root)
    except ValueError:
        pass
    else:
        raise RecorderError("capture root cannot be nested inside replay root")
    try:
        replays_root.relative_to(captures_root)
    except ValueError:
        pass
    else:
        raise RecorderError("replay root cannot be nested inside capture root")


def enforce_quota(
    captures_root: Path,
    *,
    quota_bytes: int,
    warn_percent: int,
    evict_oldest: bool,
    replays_root: Path | None = None,
    replay_stable_seconds: int = DEFAULT_REPLAY_STABLE_SECONDS,
) -> StorageReport:
    capture_root = captures_root.resolve()
    replay_root = _replays_root(capture_root, replays_root)
    _ensure_disjoint_roots(capture_root, replay_root)
    capture_root.mkdir(parents=True, exist_ok=True)
    replay_root.mkdir(parents=True, exist_ok=True)
    if quota_bytes <= 0:
        raise RecorderError("storage quota must be positive")
    if not 1 <= warn_percent <= 100:
        raise RecorderError("warning percentage must be between 1 and 100")
    if replay_stable_seconds < 0:
        raise RecorderError("replay stable duration cannot be negative")

    def used_bytes() -> int:
        return directory_size(capture_root) + directory_size(replay_root)

    before = used_bytes()
    warn_bytes = quota_bytes * warn_percent // 100
    evicted: list[EvictedEpoch] = []

    if before >= quota_bytes and evict_oldest:
        candidates = [
            _Candidate("capture_epoch", path, _epoch_sort_timestamp(path))
            for path in sealed_epoch_paths(capture_root)
        ]
        candidates.extend(
            _Candidate("replay_archive", path, _modified_ns(path))
            for path in completed_replay_paths(replay_root, stable_seconds=replay_stable_seconds)
        )
        candidates.sort(key=lambda item: (item.timestamp_ns, item.source_kind, str(item.path)))
        # Hysteresis prevents deleting another immutable unit on every small write.
        for candidate in candidates:
            if used_bytes() <= warn_bytes:
                break
            if candidate.source_kind == "capture_epoch":
                evicted.append(_evict_epoch(capture_root, candidate.path))
            else:
                replay_eviction = _evict_replay(
                    replay_root, candidate.path, stable_seconds=replay_stable_seconds
                )
                if replay_eviction is not None:
                    evicted.append(replay_eviction)

    after = used_bytes()
    if after >= quota_bytes:
        status = "full"
        if evict_oldest:
            message = (
                "capture and replay storage is full; no more verified sealed epochs or stable "
                "completed replay archives can be safely evicted"
            )
        else:
            message = "capture and replay storage is full; eviction is disabled"
    elif after >= warn_bytes:
        status = "warning"
        message = f"capture and replay storage remains at or above {warn_percent}% of quota"
    elif evicted:
        status = "evicted"
        kinds = sorted({item.source_kind for item in evicted})
        message = f"evicted {len(evicted)} oldest immutable source unit(s): {', '.join(kinds)}"
    else:
        status = "ok"
        message = "capture and replay storage is below warning threshold"
    return StorageReport(
        status=status,
        before_bytes=before,
        after_bytes=after,
        quota_bytes=quota_bytes,
        warn_bytes=warn_bytes,
        evicted=tuple(evicted),
        message=message,
    )


def human_bytes(value: int) -> str:
    amount = float(value)
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            return f"{amount:.1f} {unit}" if unit != "B" else f"{int(amount)} B"
        amount /= 1024
    return f"{amount:.1f} TiB"
