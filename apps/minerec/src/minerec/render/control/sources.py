from __future__ import annotations

import json
import os
import uuid
import zipfile
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from minerec.errors import RecorderError
from minerec.processing.artifacts import catalog as artifact_catalog

FLASHBACK_CAPTURE_CONTRACT = artifact_catalog.FLASHBACK_CAPTURE_CONTRACT


class ReplayNotReadyError(RecorderError):
    """A replay source is temporarily unavailable to the render RPC."""


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
    flashback_capture_contract: str | None = None

    def as_json(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "segment_id": self.segment_id,
            "segment_ordinal": self.segment_ordinal,
            "player_uuid": self.player_uuid,
            "connection_id": self.connection_id,
            "replay_format": self.replay_format,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
        }
        if self.flashback_capture_contract is not None:
            value["flashback_capture_contract"] = self.flashback_capture_contract
        return value


def _canonical_uuid(value: object, label: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError) as exc:
        raise RecorderError(f"invalid {label}: {value!r}") from exc


def _session_id(value: object) -> str:
    if not isinstance(value, str) or not value or len(value) > 128 or value.startswith(".") or "/" in value or "\\" in value or any(ord(character) < 32 for character in value):
        raise RecorderError(f"invalid episode id: {value!r}")
    return value


def _disabled_captures_root(replays_root: Path) -> Path:
    return replays_root.absolute() / ".minerec-render-source-catalog" / f"disabled-captures-{uuid.uuid4()}"


def _verified_archive_path(
    replays_root: Path,
    artifact: artifact_catalog.ReplayArchiveArtifact,
) -> Path:
    root = replays_root.absolute()
    root_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    try:
        root_descriptor = os.open(root, root_flags)
    except OSError as exc:
        raise RecorderError("replay root is not a safe directory") from exc
    try:
        file_descriptor = artifact_catalog._open_replay_candidate(
            root_descriptor,
            artifact.relative_path,
        )
    finally:
        os.close(root_descriptor)
    try:
        sha256, size_bytes = artifact_catalog._stable_digest(file_descriptor)
    finally:
        os.close(file_descriptor)
    if sha256 != artifact.sha256 or size_bytes != artifact.size_bytes:
        raise RecorderError(f"replay archive changed after catalog validation: {artifact.relative_path}")
    return root.joinpath(*artifact_catalog._path_parts(artifact.relative_path))


def _issue_claim(
    replays_root: Path,
    relative_path: str,
    *,
    session_id: str,
    player_uuid: str,
    connection_id: str,
) -> str | None:
    root_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    try:
        root_descriptor = os.open(replays_root.absolute(), root_flags)
        try:
            file_descriptor = artifact_catalog._open_replay_candidate(
                root_descriptor,
                relative_path,
            )
        finally:
            os.close(root_descriptor)
        try:
            with os.fdopen(os.dup(file_descriptor), "rb") as source:
                artifact_catalog._preflight_zip_entry_count(source)
                with zipfile.ZipFile(source) as archive:
                    entries = [entry for entry in archive.infolist() if entry.filename == "arcade_replay_meta.json"]
                    if len(entries) != 1:
                        return None
                    entry = entries[0]
                    if entry.file_size > artifact_catalog.MAX_ARCHIVE_METADATA_BYTES:
                        return None
                    with archive.open(entry) as handle:
                        raw = handle.read(artifact_catalog.MAX_ARCHIVE_METADATA_BYTES + 1)
        finally:
            os.close(file_descriptor)
    except (
        EOFError,
        NotImplementedError,
        OSError,
        RecorderError,
        RuntimeError,
        zipfile.BadZipFile,
        zipfile.LargeZipFile,
        zlib.error,
    ):
        return None
    if len(raw) > artifact_catalog.MAX_ARCHIVE_METADATA_BYTES:
        return None
    try:
        metadata = json.loads(raw)
    except json.JSONDecodeError, UnicodeDecodeError, RecursionError:
        return None
    if not isinstance(metadata, dict):
        return None
    identity = metadata.get("mc_recorder")
    if not isinstance(identity, dict):
        return None
    if identity.get("session_id") != session_id:
        return None
    try:
        claimed_player = _canonical_uuid(identity.get("player_uuid"), "player UUID")
    except RecorderError:
        return None
    if claimed_player != player_uuid:
        return None
    raw_connection = identity.get("connection_id")
    if raw_connection is None:
        return "missing_connection"
    try:
        claimed_connection = _canonical_uuid(raw_connection, "connection UUID")
    except RecorderError:
        return "missing_connection"
    return "exact" if claimed_connection == connection_id else None


def resolve_replay_segments(
    *,
    replays_root: Path,
    session_id: str,
    player_uuid: str,
    connection_id: str,
) -> list[ReplaySegmentSource]:
    """Resolve every catalog-verified archive for one exact connection."""

    root = Path(replays_root).absolute()
    session = _session_id(session_id)
    player = _canonical_uuid(player_uuid, "player UUID")
    connection = _canonical_uuid(connection_id, "connection UUID")
    catalog = artifact_catalog.ArtifactCatalog(
        captures_root=_disabled_captures_root(root),
        replays_root=root,
    ).scan()

    for artifact in catalog.replay_archives:
        if artifact.session_id == session and artifact.player_uuid == player and artifact.connection_id is None:
            raise RecorderError("saved replay archive does not have a supported connection identity")

    for issue in catalog.issues:
        if issue.artifact_type != "replay" or issue.relative_path == ".":
            continue
        claim = _issue_claim(
            root,
            issue.relative_path,
            session_id=session,
            player_uuid=player,
            connection_id=connection,
        )
        if claim == "missing_connection":
            raise RecorderError("saved replay archive does not have a supported connection identity")
        if claim == "exact":
            raise RecorderError(f"requested replay archive was rejected by the artifact catalog: {issue.message}")

    matching = [artifact for artifact in catalog.replay_archives if artifact.session_id == session and artifact.player_uuid == player and artifact.connection_id == connection]
    if not matching:
        raise RecorderError("no exact saved replay segments exist for this connection")

    seen_ids: set[str] = set()
    seen_ordinals: set[int] = set()
    sources: list[ReplaySegmentSource] = []
    for artifact in matching:
        if artifact.segment_id in seen_ids:
            raise RecorderError("saved replay archives have a duplicate segment ID")
        if artifact.segment_ordinal in seen_ordinals:
            raise RecorderError("saved replay archives have a duplicate segment ordinal")
        seen_ids.add(artifact.segment_id)
        seen_ordinals.add(artifact.segment_ordinal)
        sources.append(
            ReplaySegmentSource(
                segment_id=artifact.segment_id,
                segment_ordinal=artifact.segment_ordinal,
                player_uuid=artifact.player_uuid,
                connection_id=connection,
                path=_verified_archive_path(root, artifact),
                replay_format="flashback",
                sha256=artifact.sha256,
                size_bytes=artifact.size_bytes,
                flashback_capture_contract=artifact.flashback_capture_contract,
            )
        )
    return sorted(sources, key=lambda source: source.segment_ordinal)
