from __future__ import annotations

import uuid
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
    if catalog.truncated:
        raise RecorderError(
            "replay artifact catalog is incomplete; reduce the replay archive count or resolve catalog issues"
        )

    for artifact in catalog.replay_archives:
        if artifact.session_id == session and artifact.player_uuid == player and artifact.connection_id is None:
            raise RecorderError("saved replay archive does not have a supported connection identity")

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
                path=root.joinpath(*artifact_catalog._path_parts(artifact.relative_path)),
                replay_format="flashback",
                sha256=artifact.sha256,
                size_bytes=artifact.size_bytes,
                flashback_capture_contract=artifact.flashback_capture_contract,
            )
        )
    return sorted(sources, key=lambda source: source.segment_ordinal)
