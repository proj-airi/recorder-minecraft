from __future__ import annotations

import hashlib
import json
import stat
import uuid
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

from minerec.errors import RecorderError
from minerec.processing.capture.episodes import EpisodeInfo, list_episodes

MAX_ARCHIVE_METADATA_BYTES = 1024 * 1024
MAX_ARTIFACT_ISSUES = 100
HOTBAR_SNAPSHOT_CONTRACT = "item_stack_copy_v1"
FLASHBACK_CAPTURE_CONTRACT = "client_visible_scene_v1"

CaptureState = Literal["open", "complete", "incomplete", "empty"]


@dataclass(frozen=True)
class CaptureSessionArtifact:
    session_id: str
    relative_path: str
    state: CaptureState
    size_bytes: int
    epoch_count: int
    published_epoch_count: int
    unpublished_epoch_count: int
    first_tick: int | None
    last_tick: int | None

    def as_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ReplayArchiveArtifact:
    artifact_id: str
    relative_path: str
    size_bytes: int
    sha256: str
    session_id: str
    segment_id: str
    segment_ordinal: int
    player_uuid: str
    connection_id: str | None
    flashback_capture_contract: str | None

    def as_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ArtifactIssue:
    artifact_type: Literal["capture", "replay"]
    relative_path: str
    message: str

    def as_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ArtifactCatalogResult:
    capture_sessions: tuple[CaptureSessionArtifact, ...]
    replay_archives: tuple[ReplayArchiveArtifact, ...]
    issues: tuple[ArtifactIssue, ...]

    def as_json(self) -> dict[str, Any]:
        return {
            "capture_sessions": [artifact.as_json() for artifact in self.capture_sessions],
            "replay_archives": [artifact.as_json() for artifact in self.replay_archives],
            "issues": [issue.as_json() for issue in self.issues],
        }


@dataclass(frozen=True)
class _ReplayIdentity:
    schema_version: int
    session_id: str
    segment_id: str
    segment_ordinal: int
    player_uuid: str
    connection_id: str | None
    hotbar_snapshot_contract: str | None
    flashback_capture_contract: str | None


def _capture_state(episode: EpisodeInfo) -> CaptureState:
    if episode.status == "active":
        return "open"
    if episode.status == "sealed":
        return "complete"
    if episode.status == "empty" and episode.epoch_count == 0:
        return "empty"
    return "incomplete"


def _capture_artifact(episode: EpisodeInfo, root: Path) -> CaptureSessionArtifact:
    return CaptureSessionArtifact(
        session_id=episode.session_id,
        relative_path=episode.path.relative_to(root).as_posix(),
        state=_capture_state(episode),
        size_bytes=episode.size_bytes,
        epoch_count=episode.epoch_count,
        published_epoch_count=episode.sealed_epoch_count,
        unpublished_epoch_count=episode.epoch_count - episode.sealed_epoch_count,
        first_tick=episode.first_tick,
        last_tick=episode.last_tick,
    )


def _iter_replay_candidates(root: Path) -> tuple[Path, ...]:
    return tuple(
        sorted(
            (path for path in root.rglob("*") if path.suffix.lower() in {".zip", ".mcpr"}),
            key=lambda path: path.as_posix(),
        )
    )


def _stable_digest(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    try:
        before = path.stat()
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
        after = path.stat()
    except OSError as exc:
        raise RecorderError("cannot hash replay archive") from exc
    before_identity = (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
    )
    after_identity = (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    )
    if before_identity != after_identity:
        raise RecorderError("replay archive changed while it was hashed")
    if after.st_size <= 0:
        raise RecorderError("replay archive is empty")
    return digest.hexdigest(), after.st_size


def _regular_file_identity(path: Path) -> tuple[int, int, int, int]:
    try:
        file_stat = path.lstat()
    except OSError as exc:
        raise RecorderError("cannot stat replay archive") from exc
    if not stat.S_ISREG(file_stat.st_mode):
        raise RecorderError("replay archive changed while it was inspected")
    return (
        file_stat.st_dev,
        file_stat.st_ino,
        file_stat.st_size,
        file_stat.st_mtime_ns,
    )


def _canonical_uuid(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise RecorderError(f"invalid {label}")
    try:
        return str(uuid.UUID(value))
    except (ValueError, AttributeError) as exc:
        raise RecorderError(f"invalid {label}") from exc


def _validated_session_id(value: object) -> str:
    if not isinstance(value, str) or not value or len(value) > 128 or value.startswith(".") or "/" in value or "\\" in value or any(ord(character) < 32 for character in value):
        raise RecorderError("invalid replay session_id")
    return value


def _validated_identity(raw: object) -> _ReplayIdentity:
    if not isinstance(raw, dict):
        raise RecorderError("replay lacks exact mc_recorder segment identity")
    schema_version = raw.get("schema_version")
    if not isinstance(schema_version, int) or isinstance(schema_version, bool) or schema_version not in {1, 2, 3}:
        raise RecorderError("replay has unsupported mc_recorder schema_version")
    segment_ordinal = raw.get("segment_ordinal")
    if not isinstance(segment_ordinal, int) or isinstance(segment_ordinal, bool) or segment_ordinal < 0:
        raise RecorderError("replay has invalid segment_ordinal")

    hotbar_contract = raw.get("hotbar_snapshot_contract")
    flashback_contract = raw.get("flashback_capture_contract")
    if schema_version == 1:
        if hotbar_contract is not None or flashback_contract is not None:
            raise RecorderError("schema-one replay has inconsistent capture contracts")
    elif schema_version == 2:
        if hotbar_contract != HOTBAR_SNAPSHOT_CONTRACT or flashback_contract is not None:
            raise RecorderError("schema-two replay has invalid capture contracts")
    elif hotbar_contract != HOTBAR_SNAPSHOT_CONTRACT or flashback_contract != FLASHBACK_CAPTURE_CONTRACT:
        raise RecorderError("schema-three replay has invalid capture contracts")

    connection = raw.get("connection_id")
    return _ReplayIdentity(
        schema_version=schema_version,
        session_id=_validated_session_id(raw.get("session_id")),
        segment_id=_canonical_uuid(raw.get("segment_id"), "segment_id"),
        segment_ordinal=segment_ordinal,
        player_uuid=_canonical_uuid(raw.get("player_uuid"), "player_uuid"),
        connection_id=(_canonical_uuid(connection, "connection_id") if connection is not None else None),
        hotbar_snapshot_contract=hotbar_contract,
        flashback_capture_contract=flashback_contract,
    )


def _archive_identity(path: Path) -> _ReplayIdentity:
    try:
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
            metadata_entries = [entry for entry in entries if entry.filename == "metadata.json"]
            flashback_entries = [entry for entry in entries if not entry.is_dir() and entry.filename.endswith(".flashback")]
            recorder_entries = [entry for entry in entries if entry.filename == "arcade_replay_meta.json"]
            if len(metadata_entries) != 1 or not flashback_entries:
                raise RecorderError("replay is not a complete Flashback archive")
            if len(recorder_entries) != 1:
                raise RecorderError("replay must contain exactly one arcade_replay_meta.json")
            recorder_entry = recorder_entries[0]
            if recorder_entry.file_size > MAX_ARCHIVE_METADATA_BYTES:
                raise RecorderError("replay mc_recorder metadata is too large")
            with archive.open(recorder_entry) as handle:
                raw_metadata = handle.read(MAX_ARCHIVE_METADATA_BYTES + 1)
            if len(raw_metadata) > MAX_ARCHIVE_METADATA_BYTES:
                raise RecorderError("replay mc_recorder metadata is too large")
    except RecorderError:
        raise
    except (OSError, RuntimeError, zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
        raise RecorderError("replay is not a readable Flashback archive") from exc
    try:
        metadata = json.loads(raw_metadata)
    except (json.JSONDecodeError, UnicodeDecodeError, RecursionError) as exc:
        raise RecorderError("replay mc_recorder metadata is invalid JSON") from exc
    if not isinstance(metadata, dict):
        raise RecorderError("replay lacks exact mc_recorder segment identity")
    return _validated_identity(metadata.get("mc_recorder"))


def _relative_replay_path(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError as exc:
        raise RecorderError("replay candidate is outside the configured root") from exc


def _validate_replay_path(path: Path, root: Path) -> str:
    relative_path = _relative_replay_path(path, root)
    cursor = root
    for part in Path(relative_path).parts[:-1]:
        cursor /= part
        if cursor.is_symlink():
            raise RecorderError("replay candidate traverses a symlinked directory")
    if path.is_symlink():
        raise RecorderError("replay candidate is a symlink")
    try:
        mode = path.lstat().st_mode
        resolved = path.resolve(strict=True)
        resolved.relative_to(root.resolve(strict=True))
    except (OSError, ValueError) as exc:
        raise RecorderError("replay candidate is outside the configured root") from exc
    if not stat.S_ISREG(mode):
        raise RecorderError("replay candidate is not a regular file")
    return relative_path


def _artifact_id(identity: _ReplayIdentity, sha256: str) -> str:
    canonical_identity = json.dumps(
        asdict(identity),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii")
    return hashlib.sha256(canonical_identity + b"\0" + sha256.encode("ascii")).hexdigest()[:32]


def _replay_artifact(path: Path, root: Path) -> ReplayArchiveArtifact:
    relative_path = _validate_replay_path(path, root)
    before_identity = _regular_file_identity(path)
    identity = _archive_identity(path)
    sha256, size_bytes = _stable_digest(path)
    after_identity = _regular_file_identity(path)
    if before_identity != after_identity:
        raise RecorderError("replay archive changed while it was inspected")
    if _validate_replay_path(path, root) != relative_path:
        raise RecorderError("replay candidate changed while it was inspected")
    return ReplayArchiveArtifact(
        artifact_id=_artifact_id(identity, sha256),
        relative_path=relative_path,
        size_bytes=size_bytes,
        sha256=sha256,
        session_id=identity.session_id,
        segment_id=identity.segment_id,
        segment_ordinal=identity.segment_ordinal,
        player_uuid=identity.player_uuid,
        connection_id=identity.connection_id,
        flashback_capture_contract=identity.flashback_capture_contract,
    )


class ArtifactCatalog:
    def __init__(self, captures_root: Path, replays_root: Path) -> None:
        self._captures_root = Path(captures_root).absolute()
        self._replays_root = Path(replays_root).absolute()

    def scan(self) -> ArtifactCatalogResult:
        captures, capture_issues = self._scan_captures()
        replay_archives, replay_issues = self._scan_replays(MAX_ARTIFACT_ISSUES - len(capture_issues))
        return ArtifactCatalogResult(
            capture_sessions=captures,
            replay_archives=replay_archives,
            issues=(capture_issues + replay_issues)[:MAX_ARTIFACT_ISSUES],
        )

    def _scan_captures(
        self,
    ) -> tuple[tuple[CaptureSessionArtifact, ...], tuple[ArtifactIssue, ...]]:
        root = self._captures_root
        if root.is_symlink():
            return (
                (),
                (
                    ArtifactIssue(
                        artifact_type="capture",
                        relative_path=".",
                        message="capture root is not a regular directory",
                    ),
                ),
            )
        if not root.exists():
            return (), ()
        if not root.is_dir():
            return (
                (),
                (
                    ArtifactIssue(
                        artifact_type="capture",
                        relative_path=".",
                        message="capture root is not a regular directory",
                    ),
                ),
            )
        try:
            episodes = list_episodes(root)
        except OSError, RecorderError:
            return (
                (),
                (
                    ArtifactIssue(
                        artifact_type="capture",
                        relative_path=".",
                        message="cannot scan capture root",
                    ),
                ),
            )
        return tuple(_capture_artifact(episode, root) for episode in episodes), ()

    def _scan_replays(
        self,
        issue_limit: int,
    ) -> tuple[tuple[ReplayArchiveArtifact, ...], tuple[ArtifactIssue, ...]]:
        root = self._replays_root
        if root.is_symlink():
            return (
                (),
                (
                    ArtifactIssue(
                        artifact_type="replay",
                        relative_path=".",
                        message="replay root is a symlink",
                    ),
                )[:issue_limit],
            )
        if not root.exists():
            return (), ()
        if not root.is_dir():
            return (
                (),
                (
                    ArtifactIssue(
                        artifact_type="replay",
                        relative_path=".",
                        message="replay root is not a regular directory",
                    ),
                )[:issue_limit],
            )
        try:
            candidates = _iter_replay_candidates(root)
        except OSError:
            return (
                (),
                (
                    ArtifactIssue(
                        artifact_type="replay",
                        relative_path=".",
                        message="cannot scan replay root",
                    ),
                )[:issue_limit],
            )

        artifacts: list[ReplayArchiveArtifact] = []
        issues: list[ArtifactIssue] = []
        for candidate in candidates:
            try:
                artifacts.append(_replay_artifact(candidate, root))
            except (OSError, RecorderError) as exc:
                if len(issues) >= issue_limit:
                    continue
                try:
                    relative_path = _relative_replay_path(candidate, root)
                except RecorderError:
                    relative_path = "."
                issues.append(
                    ArtifactIssue(
                        artifact_type="replay",
                        relative_path=relative_path,
                        message=str(exc),
                    )
                )
        return tuple(artifacts), tuple(issues)
