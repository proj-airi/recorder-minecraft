from __future__ import annotations

import hashlib
import json
import os
import stat
import struct
import uuid
import zipfile
import zlib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, BinaryIO, Literal

from minerec.errors import RecorderError
from minerec.processing.capture.episodes import EpisodeInfo, list_episodes

MAX_ARCHIVE_METADATA_BYTES = 1024 * 1024
MAX_ARTIFACT_ISSUES = 100
MAX_REPLAY_CANDIDATES = 512
MAX_REPLAY_TRAVERSAL_ENTRIES = 4096
MAX_ZIP_CENTRAL_DIRECTORY_BYTES = 16 * 1024 * 1024
MAX_ZIP_ENTRIES = 4096
HOTBAR_SNAPSHOT_CONTRACT = "item_stack_copy_v1"
FLASHBACK_CAPTURE_CONTRACT = "client_visible_scene_v1"

CaptureState = Literal["open", "complete", "incomplete", "empty"]

_ZIP_EOCD = struct.Struct("<4s4H2LH")
_ZIP_EOCD_SIGNATURE = b"PK\x05\x06"
_MAX_ZIP_TAIL_BYTES = _ZIP_EOCD.size + 0xFFFF


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
    truncated: bool = False

    def as_json(self) -> dict[str, Any]:
        return {
            "capture_sessions": [artifact.as_json() for artifact in self.capture_sessions],
            "replay_archives": [artifact.as_json() for artifact in self.replay_archives],
            "issues": [issue.as_json() for issue in self.issues],
            "truncated": self.truncated,
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


def _file_identity(file_stat: os.stat_result) -> tuple[int, int, int, int]:
    return (
        file_stat.st_dev,
        file_stat.st_ino,
        file_stat.st_size,
        file_stat.st_mtime_ns,
    )


def _stable_digest(file_descriptor: int) -> tuple[str, int]:
    digest = hashlib.sha256()
    try:
        before = os.fstat(file_descriptor)
        with os.fdopen(os.dup(file_descriptor), "rb") as handle:
            handle.seek(0)
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
        after = os.fstat(file_descriptor)
    except OSError as exc:
        raise RecorderError("cannot hash replay archive") from exc
    if _file_identity(before) != _file_identity(after):
        raise RecorderError("replay archive changed while it was hashed")
    if after.st_size <= 0:
        raise RecorderError("replay archive is empty")
    return digest.hexdigest(), after.st_size


def _regular_file_identity(file_descriptor: int) -> tuple[int, int, int, int]:
    file_stat = os.fstat(file_descriptor)
    if not stat.S_ISREG(file_stat.st_mode):
        raise RecorderError("replay candidate is not a regular file")
    return _file_identity(file_stat)


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


def _preflight_zip_entry_count(source: BinaryIO) -> None:
    original_offset = source.tell()
    try:
        source.seek(0, os.SEEK_END)
        archive_size = source.tell()
        tail_size = min(archive_size, _MAX_ZIP_TAIL_BYTES)
        source.seek(archive_size - tail_size)
        tail = source.read(tail_size)
    except OSError as exc:
        raise RecorderError("replay ZIP directory cannot be inspected") from exc
    finally:
        source.seek(original_offset)
    if len(tail) != tail_size:
        raise RecorderError("replay ZIP directory cannot be inspected")

    offset = tail.rfind(_ZIP_EOCD_SIGNATURE)
    end_record: tuple[bytes, int, int, int, int, int, int, int] | None = None
    while offset >= 0:
        if offset + _ZIP_EOCD.size <= len(tail):
            candidate = _ZIP_EOCD.unpack_from(tail, offset)
            if offset + _ZIP_EOCD.size + candidate[-1] == len(tail):
                end_record = candidate
                break
        offset = tail.rfind(_ZIP_EOCD_SIGNATURE, 0, offset)
    if end_record is None:
        raise RecorderError("replay ZIP end record is missing")

    _, _, _, entries_on_disk, total_entries, central_size, central_offset, _ = end_record
    if entries_on_disk == 0xFFFF or total_entries == 0xFFFF or central_size == 0xFFFFFFFF or central_offset == 0xFFFFFFFF:
        raise RecorderError("replay ZIP64 archives are not supported")
    if total_entries > MAX_ZIP_ENTRIES:
        raise RecorderError("replay ZIP entry limit exceeded")
    if central_size > MAX_ZIP_CENTRAL_DIRECTORY_BYTES:
        raise RecorderError("replay ZIP central directory byte limit exceeded")
    end_record_offset = archive_size - tail_size + offset
    if central_offset > end_record_offset or central_size > end_record_offset - central_offset:
        raise RecorderError("replay ZIP central directory range is invalid")


def _archive_identity(source: BinaryIO) -> _ReplayIdentity:
    try:
        source.seek(0)
        _preflight_zip_entry_count(source)
        with zipfile.ZipFile(source) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_ZIP_ENTRIES:
                raise RecorderError("replay ZIP entry limit exceeded")
            entry_name_counts: dict[str, int] = {}
            for entry in entries:
                entry_name_counts[entry.filename] = entry_name_counts.get(entry.filename, 0) + 1
            duplicate_security_entries = {
                entry.filename for entry in entries if entry_name_counts[entry.filename] > 1 and (entry.filename in {"metadata.json", "arcade_replay_meta.json"} or (not entry.is_dir() and entry.filename.endswith(".flashback")))
            }
            if duplicate_security_entries:
                raise RecorderError("replay contains a duplicate security-relevant ZIP entry")
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
    except (
        EOFError,
        NotImplementedError,
        OSError,
        RuntimeError,
        zipfile.BadZipFile,
        zipfile.LargeZipFile,
        zlib.error,
    ) as exc:
        raise RecorderError("replay is not a readable Flashback archive") from exc
    try:
        metadata = json.loads(raw_metadata)
    except (json.JSONDecodeError, UnicodeDecodeError, RecursionError) as exc:
        raise RecorderError("replay mc_recorder metadata is invalid JSON") from exc
    if not isinstance(metadata, dict):
        raise RecorderError("replay lacks exact mc_recorder segment identity")
    return _validated_identity(metadata.get("mc_recorder"))


def _path_parts(relative_path: str) -> tuple[str, ...]:
    parts = tuple(relative_path.split("/"))
    if not parts or any(part in {"", ".", ".."} or "/" in part for part in parts):
        raise RecorderError("replay candidate is outside the configured root")
    return parts


def _open_replay_candidate(root_descriptor: int, relative_path: str) -> int:
    parts = _path_parts(relative_path)
    directory_descriptor = os.dup(root_descriptor)
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    file_flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0)
    try:
        for part in parts[:-1]:
            try:
                child_descriptor = os.open(
                    part,
                    directory_flags,
                    dir_fd=directory_descriptor,
                )
            except OSError as exc:
                raise RecorderError("replay candidate traverses a symlink or non-directory component") from exc
            os.close(directory_descriptor)
            directory_descriptor = child_descriptor
        try:
            file_descriptor = os.open(
                parts[-1],
                file_flags,
                dir_fd=directory_descriptor,
            )
        except OSError as exc:
            raise RecorderError("replay candidate is a symlink or cannot be opened") from exc
        try:
            _regular_file_identity(file_descriptor)
        except OSError, RecorderError:
            os.close(file_descriptor)
            raise
        return file_descriptor
    finally:
        os.close(directory_descriptor)


def _iter_replay_candidates(
    root_descriptor: int,
    issue_limit: int,
) -> tuple[
    tuple[str, ...],
    tuple[ArtifactIssue, ...],
    Literal["candidate_limit", "issue_limit", "traversal_limit"] | None,
]:
    candidates: list[str] = []
    issues: list[ArtifactIssue] = []
    truncation_reason: Literal["candidate_limit", "issue_limit", "traversal_limit"] | None = None
    visited_entries = 0
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)

    def add_issue(relative_path: str, message: str) -> bool:
        nonlocal truncation_reason
        if len(issues) >= issue_limit:
            truncation_reason = "issue_limit"
            return False
        issues.append(
            ArtifactIssue(
                artifact_type="replay",
                relative_path=relative_path,
                message=message,
            )
        )
        return True

    def walk(directory_descriptor: int, prefix: tuple[str, ...]) -> bool:
        nonlocal truncation_reason, visited_entries
        remaining_budget = MAX_REPLAY_TRAVERSAL_ENTRIES - visited_entries
        names: list[str] = []
        has_unvisited_entry = False
        try:
            with os.scandir(directory_descriptor) as entries:
                for _ in range(max(remaining_budget, 0)):
                    try:
                        entry = next(entries)
                    except StopIteration:
                        break
                    names.append(entry.name)
                    visited_entries += 1
                else:
                    has_unvisited_entry = next(entries, None) is not None
        except OSError:
            relative_path = "/".join(prefix) or "."
            return add_issue(relative_path, "cannot scan replay directory")
        for name in sorted(names):
            relative_parts = (*prefix, name)
            relative_path = "/".join(relative_parts)
            try:
                entry_stat = os.stat(
                    name,
                    dir_fd=directory_descriptor,
                    follow_symlinks=False,
                )
            except OSError:
                if not add_issue(relative_path, "cannot inspect replay traversal entry"):
                    return False
                continue
            if stat.S_ISLNK(entry_stat.st_mode):
                if not add_issue(relative_path, "replay traversal entry is a symlink"):
                    return False
                continue
            if stat.S_ISDIR(entry_stat.st_mode):
                try:
                    child_descriptor = os.open(
                        name,
                        directory_flags,
                        dir_fd=directory_descriptor,
                    )
                except OSError:
                    if not add_issue(
                        relative_path,
                        "replay traversal entry is a symlink or non-directory component",
                    ):
                        return False
                    continue
                try:
                    if not walk(child_descriptor, relative_parts):
                        return False
                finally:
                    os.close(child_descriptor)
                continue
            if Path(name).suffix.lower() not in {".zip", ".mcpr"}:
                continue
            if not stat.S_ISREG(entry_stat.st_mode):
                if not add_issue(relative_path, "replay candidate is not a regular file"):
                    return False
                continue
            if len(candidates) >= MAX_REPLAY_CANDIDATES:
                truncation_reason = "candidate_limit"
                return False
            candidates.append(relative_path)
        if has_unvisited_entry:
            truncation_reason = "traversal_limit"
            return False
        return truncation_reason is None

    walk(root_descriptor, ())
    return tuple(candidates), tuple(issues), truncation_reason


def _artifact_id(identity: _ReplayIdentity, sha256: str) -> str:
    canonical_identity = json.dumps(
        asdict(identity),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii")
    return hashlib.sha256(canonical_identity + b"\0" + sha256.encode("ascii")).hexdigest()[:32]


def _replay_artifact(relative_path: str, root_descriptor: int) -> ReplayArchiveArtifact:
    file_descriptor = _open_replay_candidate(root_descriptor, relative_path)
    try:
        before_identity = _regular_file_identity(file_descriptor)
        with os.fdopen(os.dup(file_descriptor), "rb") as archive_source:
            identity = _archive_identity(archive_source)
        sha256, size_bytes = _stable_digest(file_descriptor)
        after_identity = _regular_file_identity(file_descriptor)
        if before_identity != after_identity:
            raise RecorderError("replay archive changed while it was inspected")
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
    except OSError as exc:
        raise RecorderError("cannot inspect replay archive") from exc
    finally:
        os.close(file_descriptor)


class ArtifactCatalog:
    def __init__(self, captures_root: Path, replays_root: Path) -> None:
        self._captures_root = Path(captures_root).absolute()
        self._replays_root = Path(replays_root).absolute()

    def scan(self) -> ArtifactCatalogResult:
        captures, capture_issues = self._scan_captures()
        replay_archives, replay_issues, replay_truncated = self._scan_replays(MAX_ARTIFACT_ISSUES - len(capture_issues))
        return ArtifactCatalogResult(
            capture_sessions=captures,
            replay_archives=replay_archives,
            issues=(capture_issues + replay_issues)[:MAX_ARTIFACT_ISSUES],
            truncated=replay_truncated,
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
    ) -> tuple[
        tuple[ReplayArchiveArtifact, ...],
        tuple[ArtifactIssue, ...],
        bool,
    ]:
        root = self._replays_root
        root_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
        try:
            root_descriptor = os.open(root, root_flags)
        except FileNotFoundError:
            return (), (), False
        except OSError:
            return (
                (),
                (
                    ArtifactIssue(
                        artifact_type="replay",
                        relative_path=".",
                        message="replay root is not a regular directory",
                    ),
                )[:issue_limit],
                False,
            )

        try:
            candidates, traversal_issues, traversal_truncation = _iter_replay_candidates(
                root_descriptor,
                max(issue_limit - 1, 0),
            )
        except OSError:
            os.close(root_descriptor)
            return (
                (),
                (
                    ArtifactIssue(
                        artifact_type="replay",
                        relative_path=".",
                        message="cannot scan replay root",
                    ),
                )[:issue_limit],
                False,
            )

        artifacts: list[ReplayArchiveArtifact] = []
        issues = list(traversal_issues)
        inspection_truncated = False
        try:
            for index, candidate in enumerate(candidates):
                try:
                    artifacts.append(_replay_artifact(candidate, root_descriptor))
                except RecorderError as exc:
                    has_more = index + 1 < len(candidates) or traversal_truncation is not None
                    if has_more and len(issues) >= max(issue_limit - 1, 0):
                        inspection_truncated = True
                        break
                    if len(issues) < issue_limit:
                        issues.append(
                            ArtifactIssue(
                                artifact_type="replay",
                                relative_path=candidate,
                                message=str(exc),
                            )
                        )
                    if has_more and len(issues) >= max(issue_limit - 1, 0):
                        inspection_truncated = True
                        break
        finally:
            os.close(root_descriptor)

        if traversal_truncation is not None or inspection_truncated:
            if inspection_truncated or traversal_truncation == "issue_limit":
                message = "replay scan truncated at catalog issue limit"
            elif traversal_truncation == "candidate_limit":
                message = "replay scan truncated at candidate limit"
            else:
                message = "replay scan truncated at traversal entry limit"
            truncation_issue = ArtifactIssue(
                artifact_type="replay",
                relative_path=".",
                message=message,
            )
            if len(issues) < issue_limit:
                issues.append(truncation_issue)
            elif issues:
                issues[-1] = truncation_issue
        return (
            tuple(artifacts),
            tuple(issues),
            traversal_truncation is not None or inspection_truncated,
        )
