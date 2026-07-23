from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import struct
import tempfile
import uuid
import zlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

from minerec.errors import RecorderError
from minerec.processing.capture.episodes import EpochInfo, iter_epochs, iter_events, sha256_file, validate_episode
from minerec.processing.capture.storage import pin_sealed_epochs
from minerec.processing.render.hud import validate_hud_result_envelope
from minerec.render.control.contract import FULL_CLIENT_PRESENTATION_CONTRACT
from minerec.render_compat import validate_unsupported_packet_summary

EXPORT_SCHEMA_VERSION = 2
EXPORT_FORMAT = "mc-recorder-jsonl-v2"
CANONICAL_RENDER_RESULT_TYPE = "mc-recorder-render-result-v2"
OWNER = "mc-recorder"
TICK_RATE_HZ = 20
MAX_PNG_BYTES_PER_PIXEL = 8
MIN_PNG_SIZE_LIMIT = 16 * 1024 * 1024
MAX_ATTACHMENT_INDEX_BYTES = 64 * 1024 * 1024
MAX_ATTACHMENT_INDEX_LINE_CHARS = 64 * 1024
MAX_ATTACHMENT_TICKS = 100_000
MAX_JSON_OBJECT_BYTES = 16 * 1024 * 1024
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_ENVELOPE_FIELDS = {
    "schema_version",
    "record_type",
    "session_id",
    "epoch_index",
    "server_tick",
    "sequence",
    "recorded_at_ns",
    "recorded_at_unix_ms",
}

SubjectKey = tuple[str, str]
FrameKey = tuple[str, str, str, int]


@dataclass(frozen=True)
class ExportResult:
    output: Path
    sample_count: int
    state_count: int
    action_count: int
    modality_count: int
    rgb_count: int
    scene_count: int


@dataclass
class TickBundle:
    tick: int
    states: dict[SubjectKey, dict[str, Any]] = field(default_factory=dict)
    controls: dict[SubjectKey, dict[str, Any]] = field(default_factory=dict)
    packets: dict[SubjectKey, list[dict[str, Any]]] = field(default_factory=dict)
    barrier: int | None = None
    tick_end_seen: bool = False


@dataclass(frozen=True)
class VerifiedEpoch:
    info: EpochInfo
    manifest_sha256: str
    events_sha256: str
    events_bytes: int
    record_count: int

    def manifest_entry(self) -> dict[str, Any]:
        return {
            "epoch_index": self.info.index,
            "manifest_sha256": self.manifest_sha256,
            "events_sha256": self.events_sha256,
            "events_bytes": self.events_bytes,
            "record_count": self.record_count,
        }


@dataclass(frozen=True)
class AttachedFrame:
    reference: str
    path: Path
    row: dict[str, Any]
    index_sha256: str
    artifact_sha256: str
    artifact_bytes: int
    width: int
    height: int


@dataclass(frozen=True)
class AttachedScene:
    reference: str
    frame_id: str
    coverage_complete: bool
    dimension: str
    subject_position: tuple[float, float, float]


@dataclass(frozen=True)
class VerifiedSceneAttachment:
    path: Path
    sha256: str
    size_bytes: int
    frames: dict[FrameKey, AttachedScene]
    provenance: dict[str, Any]


def _json_line(value: dict[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"


def _plain_json(value: Any) -> Any:  # noqa: ANN401
    if isinstance(value, dict) or hasattr(value, "items"):
        return {str(key): _plain_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain_json(item) for item in value]
    return value


def _required_int(mapping: dict[str, Any], key: str, context: Path | str) -> int:
    value = mapping.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise RecorderError(f"{context}: required integer field {key!r} is missing or invalid")
    return value


def _read_object(path: Path, description: str) -> dict[str, Any]:
    try:
        if path.stat().st_size > MAX_JSON_OBJECT_BYTES:
            raise RecorderError(f"{description} exceeds the JSON size limit: {path}")
        value = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise RecorderError(f"cannot read {description}: {path}") from exc
    except (ValueError, RecursionError) as exc:
        raise RecorderError(f"invalid JSON in {description} {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise RecorderError(f"{description} must be a JSON object: {path}")
    return value


def _renderer_replay_integrity(result: dict[str, Any], result_path: Path) -> tuple[str, int, str]:
    if result.get("result_type") == CANONICAL_RENDER_RESULT_TYPE:
        if result.get("schema_version") != 2:
            raise RecorderError(f"canonical renderer result has an invalid schema: {result_path}")
        if result.get("artifact_root") != ".":
            raise RecorderError(f"canonical renderer result has an invalid artifact root: {result_path}")
        portable = result.get("portable_request")
        if not isinstance(portable, dict):
            raise RecorderError(f"canonical renderer result lacks portable_request provenance: {result_path}")
        try:
            request_id = str(uuid.UUID(portable.get("request_id")))
        except (ValueError, TypeError, AttributeError) as exc:
            raise RecorderError(f"canonical renderer result has an invalid request_id: {result_path}") from exc
        if portable.get("request_id") != request_id or re.fullmatch(r"[0-9a-f]{64}", str(portable.get("sha256", ""))) is None:
            raise RecorderError(f"canonical renderer result has invalid request provenance: {result_path}")
        source = result.get("source_replay")
        if not isinstance(source, dict):
            raise RecorderError(f"canonical renderer result lacks source_replay: {result_path}")
        segment_id = source.get("segment_id")
        segment_ordinal = source.get("segment_ordinal")
        replay_sha = source.get("sha256")
        replay_bytes = source.get("size_bytes")
        if not isinstance(segment_id, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,159}", segment_id) is None:
            raise RecorderError(f"canonical renderer result lacks segment_id: {result_path}")
        if not isinstance(segment_ordinal, int) or isinstance(segment_ordinal, bool) or not 0 <= segment_ordinal <= 2**31 - 1 or source.get("format") != "flashback":
            raise RecorderError(f"canonical renderer result has invalid segment provenance: {result_path}")
        if not isinstance(replay_sha, str) or re.fullmatch(r"[0-9a-f]{64}", replay_sha) is None:
            raise RecorderError(f"canonical renderer result lacks a valid replay SHA-256: {result_path}")
        if not isinstance(replay_bytes, int) or isinstance(replay_bytes, bool) or replay_bytes <= 0 or replay_bytes > 2**63 - 1:
            raise RecorderError(f"canonical renderer result lacks a positive replay size: {result_path}")
        return replay_sha, replay_bytes, f"segment:{segment_id}"
    replay = result.get("replay")
    replay_sha = result.get("replay_sha256")
    replay_bytes = result.get("replay_bytes")
    if not isinstance(replay, str) or not replay:
        raise RecorderError(f"renderer result lacks its replay path: {result_path}")
    if not isinstance(replay_sha, str) or re.fullmatch(r"[0-9a-fA-F]{64}", replay_sha) is None:
        raise RecorderError(f"renderer result lacks a valid replay_sha256: {result_path}")
    if not isinstance(replay_bytes, int) or isinstance(replay_bytes, bool) or replay_bytes <= 0 or replay_bytes > 2**63 - 1:
        raise RecorderError(f"renderer result lacks a positive replay_bytes: {result_path}")
    return replay_sha.lower(), replay_bytes, replay


def _canonical_result_reference(result: dict[str, Any], result_path: Path, key: str, description: str) -> Path | None:
    """Resolve a v2 result reference without accepting a browser/worker path."""
    if result.get("result_type") != CANONICAL_RENDER_RESULT_TYPE:
        return None
    # Validate the non-path provenance envelope before resolving any reference.
    _renderer_replay_integrity(result, result_path)
    value = result.get(key)
    if not isinstance(value, str) or not value:
        raise RecorderError(f"canonical renderer result lacks {description}: {result_path}")
    if "\\" in value:
        raise RecorderError(f"canonical renderer result {description} must use a contained POSIX path")
    relative = Path(value)
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        raise RecorderError(f"canonical renderer result {description} must be a contained relative path")
    root = result_path.parent.resolve()
    candidate = root / relative
    cursor = candidate
    while cursor != root:
        if cursor.is_symlink():
            raise RecorderError(f"canonical renderer result {description} contains a symlink")
        parent = cursor.parent
        if parent == cursor:
            break
        cursor = parent
    resolved = candidate.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise RecorderError(f"canonical renderer result {description} escapes its import directory") from exc
    return resolved


def _player_uuid(record: dict[str, Any]) -> str | None:
    for key in ("player_uuid", "uuid", "player_id"):
        value = record.get(key)
        if isinstance(value, str):
            return value
    player = record.get("player")
    if isinstance(player, dict):
        value = player.get("uuid")
        if isinstance(value, str):
            return value
    return None


def _subject_key(record: dict[str, Any]) -> SubjectKey | None:
    player = _player_uuid(record)
    connection = record.get("connection_id")
    if player is None or not isinstance(connection, str):
        return None
    return player, connection


def _selected(
    record: dict[str, Any],
    players: set[str],
    connections: set[str],
    first_tick: int | None,
    last_tick: int | None,
) -> bool:
    tick = record.get("server_tick")
    if not isinstance(tick, int) or isinstance(tick, bool):
        return False
    if first_tick is not None and tick < first_tick:
        return False
    if last_tick is not None and tick > last_tick:
        return False
    player = _player_uuid(record)
    if players and player not in players:
        return False
    connection = record.get("connection_id")
    return not connections or connection in connections


def _normalize_players(players: Iterable[str]) -> set[str]:
    normalized: set[str] = set()
    for player in players:
        try:
            normalized.add(str(uuid.UUID(player)))
        except (ValueError, AttributeError) as exc:
            raise RecorderError(f"invalid player UUID filter: {player!r}") from exc
    return normalized


def _normalize_connections(connections: Iterable[str]) -> set[str]:
    normalized: set[str] = set()
    for connection in connections:
        try:
            normalized.add(str(uuid.UUID(connection)))
        except (ValueError, AttributeError) as exc:
            raise RecorderError(f"invalid connection UUID filter: {connection!r}") from exc
    return normalized


def _payload(record: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in record.items() if key not in _ENVELOPE_FIELDS}


def _source_ref(record: dict[str, Any], epoch_hashes: dict[int, VerifiedEpoch]) -> dict[str, Any]:
    epoch_index = record.get("epoch_index")
    source = epoch_hashes.get(epoch_index) if isinstance(epoch_index, int) else None
    return {
        "epoch_index": epoch_index,
        "event_sequence": record.get("sequence"),
        "events_sha256": source.events_sha256 if source is not None else None,
        "epoch_manifest_sha256": source.manifest_sha256 if source is not None else None,
    }


def _stable_packet_action_type(packet_type: str) -> str | None:
    name = packet_type.rsplit(":", 1)[-1].lower()
    if name == "player_input":
        return "movement_controls"
    if name.startswith("move_player"):
        return "camera_or_position"
    if name == "player_action":
        return "player_action"
    if "interact" in name:
        return "interact"
    if name.startswith("use_item"):
        return "use"
    if name == "swing":
        return "swing"
    if name.startswith("container_") or name == "set_carried_item":
        return "inventory"
    if name == "player_command":
        return "stance"
    if "chat" in name or "command" in name:
        return "text_redacted"
    if "custom_payload" in name:
        return "custom_payload_redacted"
    if name == "client_tick_end":
        return "tick_boundary"
    if name in {"chunk_batch_received", "accept_teleportation", "player_loaded"}:
        return "protocol_ack"
    return None


def _action_row(record: dict[str, Any], epoch_hashes: dict[int, VerifiedEpoch]) -> dict[str, Any]:
    nested_action = record.get("action")
    nested_packet = record.get("packet")
    if isinstance(nested_action, dict):
        payload = nested_action
    elif isinstance(nested_packet, dict):
        payload = nested_packet
    else:
        payload = _payload(record)

    action_type: Any = record.get("action_type") or record.get("action_kind") or record.get("packet_type") or record.get("packet_class")
    if record.get("record_type") == "control_state":
        action_type = "control_state"
    if not isinstance(action_type, str):
        for key in ("action_kind", "packet_type", "packet_class", "type", "action_type"):
            value = payload.get(key)
            if isinstance(value, str):
                action_type = value
                break
    if not isinstance(action_type, str):
        action_type = "unknown_serverbound_packet"
    if action_type == "other":
        packet_type = payload.get("packet_type")
        if not isinstance(packet_type, str):
            packet_type = record.get("packet_type")
        if isinstance(packet_type, str):
            action_type = _stable_packet_action_type(packet_type) or action_type

    return {
        "schema_version": EXPORT_SCHEMA_VERSION,
        "session_id": record.get("session_id"),
        "epoch_index": record.get("epoch_index"),
        "server_tick": record.get("server_tick"),
        "sequence": record.get("sequence"),
        "apply_sequence": record.get("apply_sequence"),
        "player_uuid": _player_uuid(record),
        "connection_id": record.get("connection_id"),
        "action_type": action_type,
        "applied": record.get("record_type") != "packet_arrival",
        "payload": payload,
        "source": {
            **_source_ref(record, epoch_hashes),
            "record_type": record.get("record_type"),
            "recorded_at_ns": record.get("recorded_at_ns"),
            "arrival_sequence": record.get("arrival_sequence"),
        },
    }


def _state_row(record: dict[str, Any], epoch_hashes: dict[int, VerifiedEpoch]) -> dict[str, Any]:
    row = dict(record)
    row["source_schema_version"] = row.pop("schema_version", None)
    row["schema_version"] = EXPORT_SCHEMA_VERSION
    row.pop("record_type", None)
    row["source"] = _source_ref(record, epoch_hashes)
    return row


def _frame_key(record: dict[str, Any]) -> FrameKey | None:
    session = record.get("session_id")
    player = _player_uuid(record)
    connection = record.get("connection_id")
    tick = record.get("server_tick")
    if not isinstance(session, str) or player is None or not isinstance(connection, str) or not isinstance(tick, int) or isinstance(tick, bool):
        return None
    return session, player, connection, tick


def _validate_scene_pose_binding(record: dict[str, Any], attached_scene: AttachedScene, key: FrameKey) -> None:
    """Require one scene frame to carry the exact canonical player-state pose.

    Both values originate as persisted decimal JSON numbers and are decoded to
    IEEE-754 binary64 values before reaching this boundary (the scene copy is
    stored as SQLite REAL). Direct equality therefore checks the exact stored
    canonical value. An epsilon is intentionally not used: even an adjacent
    representable position belongs to a different pose and must not be
    published as training data for this state.
    """

    context = f"scene frame {attached_scene.frame_id!r} for {key[0]}/{key[1]}/{key[2]} at server tick {key[3]}"
    dimension = record.get("dimension")
    if not isinstance(dimension, str) or not dimension:
        raise RecorderError(f"{context}: canonical player_state dimension is invalid")
    if attached_scene.dimension != dimension:
        raise RecorderError(f"{context}: dimension {attached_scene.dimension!r} does not match canonical player_state dimension {dimension!r}")

    position = record.get("position")
    if not isinstance(position, dict):
        raise RecorderError(f"{context}: canonical player_state position is invalid")
    coordinates: list[float] = []
    for axis in ("x", "y", "z"):
        value = position.get(axis)
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise RecorderError(f"{context}: canonical player_state position.{axis} is invalid")
        try:
            coordinate = float(value)
        except OverflowError as exc:
            raise RecorderError(f"{context}: canonical player_state position.{axis} is outside finite bounds") from exc
        if not math.isfinite(coordinate):
            raise RecorderError(f"{context}: canonical player_state position.{axis} is not finite")
        coordinates.append(coordinate)
    canonical_position = tuple(coordinates)
    if attached_scene.subject_position != canonical_position:
        raise RecorderError(f"{context}: subject_position {attached_scene.subject_position!r} does not match canonical player_state position {canonical_position!r}")


def _modality_entry(
    record: dict[str, Any],
    frames: dict[FrameKey, AttachedFrame],
    scenes: dict[FrameKey, AttachedScene],
    epoch_hashes: dict[int, VerifiedEpoch],
) -> dict[str, Any]:
    attached_scene = scenes.get(_frame_key(record))
    attached = frames.get(_frame_key(record))
    rgb: dict[str, Any]
    if attached is None:
        rgb = {
            "available": False,
            "reference": None,
            "valid": False,
            "reason": "no completed renderer frame index is attached for this exact sample key",
        }
    else:
        rgb = {
            "available": True,
            "reference": attached.reference,
            "valid": True,
            "reason": None,
            "frame": attached.row.get("frame"),
            "replay_tick": attached.row.get("replay_tick"),
            "partial_tick": attached.row.get("partial_tick"),
            "frames_index_sha256": attached.index_sha256,
            "artifact_sha256": attached.artifact_sha256,
            "artifact_bytes": attached.artifact_bytes,
            "width": attached.width,
            "height": attached.height,
        }
    if attached_scene is not None:
        scene = {
            "available": True,
            "valid": True,
            "coverage_complete": attached_scene.coverage_complete,
            "reference": attached_scene.reference,
            "frame_id": attached_scene.frame_id,
            "reason": None,
        }
    else:
        scene = {
            "available": False,
            "valid": False,
            "coverage_complete": False,
            "reference": None,
            "frame_id": None,
            "reason": "scene_not_attached",
        }
    return {
        "schema_version": EXPORT_SCHEMA_VERSION,
        "session_id": record.get("session_id"),
        "epoch_index": record.get("epoch_index"),
        "server_tick": record.get("server_tick"),
        "player_uuid": _player_uuid(record),
        "connection_id": record.get("connection_id"),
        "scene": scene,
        "rgb": rgb,
        "source": _source_ref(record, epoch_hashes),
    }


def _peer_rows(
    states: dict[SubjectKey, dict[str, Any]],
    subject: SubjectKey,
    epoch_hashes: dict[int, VerifiedEpoch],
) -> list[dict[str, Any]]:
    return [
        {
            "player_uuid": key[0],
            "connection_id": key[1],
            "state": _payload(record),
            "source": _source_ref(record, epoch_hashes),
        }
        for key, record in sorted(states.items())
        if key != subject
    ]


def _transition_invalid_reasons(
    previous: TickBundle,
    current: TickBundle,
    previous_state: dict[str, Any],
    current_state: dict[str, Any],
    control: dict[str, Any] | None,
    packets: list[dict[str, Any]],
) -> list[str]:
    reasons: list[str] = []
    if current.tick != previous.tick + 1:
        reasons.append("non_consecutive_server_ticks")
    if not previous.tick_end_seen or not current.tick_end_seen:
        reasons.append("missing_tick_end_barrier")

    previous_state_barrier = previous_state.get("state_barrier_apply_sequence")
    current_state_barrier = current_state.get("state_barrier_apply_sequence")
    barriers = (
        previous_state_barrier,
        previous.barrier,
        current_state_barrier,
        current.barrier,
    )
    if any(not isinstance(value, int) or isinstance(value, bool) for value in barriers):
        reasons.append("missing_authoritative_apply_barrier")
    elif previous_state_barrier != previous.barrier or current_state_barrier != current.barrier:
        reasons.append("state_and_tick_barriers_disagree")
    elif current_state_barrier < previous_state_barrier:  # ty:ignore[unsupported-operator]
        reasons.append("apply_barrier_moved_backwards")

    if control is None:
        reasons.append("missing_reconstructed_control")

    apply_sequences = [packet.get("apply_sequence") for packet in packets]
    if any(not isinstance(value, int) or isinstance(value, bool) for value in apply_sequences):
        reasons.append("packet_missing_apply_sequence")
    elif any(left >= right for left, right in zip(apply_sequences, apply_sequences[1:])):  # ty:ignore[unsupported-operator]
        reasons.append("packet_apply_sequence_not_strictly_increasing")

    if all(isinstance(value, int) and not isinstance(value, bool) for value in barriers + tuple(apply_sequences)):
        previous_barrier = int(previous_state_barrier)  # ty:ignore[invalid-argument-type]
        current_barrier = int(current_state_barrier)  # ty:ignore[invalid-argument-type]
        if any(not previous_barrier < int(value) <= current_barrier for value in apply_sequences):  # ty:ignore[invalid-argument-type]
            reasons.append("packet_apply_sequence_outside_transition_barriers")
    return reasons


def _sample_row(
    previous: TickBundle,
    current: TickBundle,
    key: SubjectKey,
    session_manifest_sha: str,
    epoch_hashes: dict[int, VerifiedEpoch],
    frames: dict[FrameKey, AttachedFrame],
    scenes: dict[FrameKey, AttachedScene],
) -> dict[str, Any]:
    previous_state = previous.states[key]
    current_state = current.states[key]
    player_uuid, connection_id = key
    control = current.controls.get(key)
    assigned_packets = current.packets.get(key, [])
    previous_barrier = previous_state.get("state_barrier_apply_sequence")
    current_barrier = current_state.get("state_barrier_apply_sequence")
    if isinstance(previous_barrier, int) and not isinstance(previous_barrier, bool) and isinstance(current_barrier, int) and not isinstance(current_barrier, bool):
        packets = [packet for packet in assigned_packets if isinstance(packet.get("apply_sequence"), int) and not isinstance(packet.get("apply_sequence"), bool) and previous_barrier < packet["apply_sequence"] <= current_barrier]
    else:
        packets = []
    packets.sort(
        key=lambda item: (
            item.get("apply_sequence") if isinstance(item.get("apply_sequence"), int) and not isinstance(item.get("apply_sequence"), bool) else 2**63,
            item.get("sequence") if isinstance(item.get("sequence"), int) else 2**63,
        ),
    )
    invalid_reasons = _transition_invalid_reasons(previous, current, previous_state, current_state, control, assigned_packets)
    modality = _modality_entry(previous_state, frames, scenes, epoch_hashes)
    control_row = _action_row(control, epoch_hashes) if control is not None else None
    packet_rows = [_action_row(packet, epoch_hashes) for packet in packets]
    return {
        "schema_version": EXPORT_SCHEMA_VERSION,
        "sample_key": {
            "session_id": previous_state.get("session_id"),
            "server_tick": previous.tick,
            "player_uuid": player_uuid,
            "connection_id": connection_id,
        },
        "session_id": previous_state.get("session_id"),
        "epoch_index": previous_state.get("epoch_index"),
        "server_tick": previous.tick,
        "player_uuid": player_uuid,
        "connection_id": connection_id,
        "state": _payload(previous_state),
        "action": {
            "reconstructed_control": control_row,
            "ordered_packets": packet_rows,
            "after_apply_sequence": previous_barrier,
            "through_apply_sequence": current_barrier,
            "source_server_tick": current.tick,
            "assigned_packet_count": len(assigned_packets),
            "excluded_packet_count": len(assigned_packets) - len(packets),
        },
        "next_state": _payload(current_state),
        "next_server_tick": current.tick,
        "peers": {
            "state": _peer_rows(previous.states, key, epoch_hashes),
            "next_state": _peer_rows(current.states, key, epoch_hashes),
        },
        "modalities": {"scene": modality["scene"], "rgb": modality["rgb"]},
        "transition_valid": not invalid_reasons,
        "transition_invalid_reasons": invalid_reasons,
        "source_manifest_sha256": session_manifest_sha,
        "source": {
            "state": _source_ref(previous_state, epoch_hashes),
            "reconstructed_control": (_source_ref(control, epoch_hashes) if control is not None else None),
            "ordered_packets": [_source_ref(packet, epoch_hashes) for packet in packets],
            "next_state": _source_ref(current_state, epoch_hashes),
        },
    }


def _owned_export_directory(path: Path) -> bool:
    manifest = path / "manifest.json"
    try:
        value = json.loads(manifest.read_text(encoding="utf-8"))
    except OSError, json.JSONDecodeError:
        return False
    if not isinstance(value, dict) or value.get("owner") != OWNER or value.get("format") != EXPORT_FORMAT:
        return False
    required_files = {
        "samples.jsonl",
        "states.jsonl",
        "actions.jsonl",
        "modalities.jsonl",
    }
    try:
        if path.is_symlink() or manifest.is_symlink() or not manifest.is_file():
            return False
        actual_files: set[str] = set()
        for entry in path.rglob("*"):
            if entry.is_symlink():
                return False
            if entry.is_file():
                actual_files.add(entry.relative_to(path).as_posix())
            elif not entry.is_dir():
                return False
        files = value.get("files")
        if not isinstance(files, dict) or not required_files <= set(files) or set(files) != actual_files - {"manifest.json"} or not actual_files <= required_files | {"manifest.json", "scene/scene-v1.sqlite3"}:
            return False
        for name, metadata in files.items():
            if not isinstance(metadata, dict):
                return False
            artifact = path / name
            if artifact.is_symlink() or not artifact.is_file():
                return False
            if metadata.get("size_bytes") != artifact.stat().st_size:
                return False
            if metadata.get("sha256") != sha256_file(artifact):
                return False
    except OSError:
        return False
    return True


def _safe_replace_directory(staging: Path, output: Path, force: bool) -> None:
    if not output.exists() and not output.is_symlink():
        staging.rename(output)
        _fsync_directory(output.parent)
        return
    if not force:
        raise RecorderError(f"export output already exists: {output}; pass --force to replace it")
    if output.is_symlink() or not output.is_dir() or not _owned_export_directory(output):
        raise RecorderError(f"refusing to replace non-owned export directory: {output}; choose an empty output path")

    try:
        original = output.lstat()
    except OSError as exc:
        raise RecorderError(f"cannot inspect existing export output: {output}") from exc
    backup = output.parent / f".{output.name}.backup-{uuid.uuid4().hex}"
    output.rename(backup)
    promoted = False
    try:
        moved = backup.lstat()
        if backup.is_symlink() or not backup.is_dir() or (original.st_dev, original.st_ino) != (moved.st_dev, moved.st_ino) or not _owned_export_directory(backup):
            raise RecorderError("existing export changed while preparing its atomic replacement")
        staging.rename(output)
        promoted = True
        _fsync_directory(output.parent)
    except Exception:
        if not promoted and not output.exists() and not output.is_symlink() and (backup.exists() or backup.is_symlink()):
            backup.rename(output)
            _fsync_directory(output.parent)
        raise
    try:
        shutil.rmtree(backup)
    except OSError:
        # The new export is already durable and valid. Leaving the verified,
        # hidden backup is safer than reporting a failed publication that a
        # retry might try to repeat.
        return
    _fsync_directory(output.parent)


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def _verified_sealed_epochs(episode: Path, session_id: str) -> list[VerifiedEpoch]:
    verified: list[VerifiedEpoch] = []
    for epoch in iter_epochs(episode):
        if epoch.status != "sealed":
            continue
        manifest_path = epoch.path / "manifest.json"
        events_path = epoch.path / "events.jsonl"
        manifest = _read_object(manifest_path, "epoch manifest")
        if manifest.get("sealed") is not True:
            raise RecorderError(f"epoch {epoch.index} is not explicitly sealed in {manifest_path}")
        if manifest.get("session_id") != session_id:
            raise RecorderError(f"epoch {epoch.index} manifest has the wrong session_id")
        if manifest.get("epoch_index") != epoch.index:
            raise RecorderError(f"epoch {epoch.index} manifest has the wrong epoch_index")
        expected_count = _required_int(manifest, "record_count", manifest_path)
        expected_bytes = _required_int(manifest, "events_bytes", manifest_path)
        expected_sha = manifest.get("events_sha256")
        if not isinstance(expected_sha, str) or len(expected_sha) != 64:
            raise RecorderError(f"{manifest_path}: required events_sha256 is missing or invalid")
        try:
            actual_bytes = events_path.stat().st_size
            actual_sha = sha256_file(events_path)
            with events_path.open("r", encoding="utf-8") as handle:
                actual_count = sum(1 for line in handle if line.strip())
        except OSError as exc:
            raise RecorderError(f"cannot verify sealed epoch source: {events_path}") from exc
        if actual_count != expected_count:
            raise RecorderError(f"epoch {epoch.index} record count mismatch: manifest {expected_count}, file {actual_count}")
        if actual_bytes != expected_bytes:
            raise RecorderError(f"epoch {epoch.index} byte count mismatch: manifest {expected_bytes}, file {actual_bytes}")
        if actual_sha != expected_sha.lower():
            raise RecorderError(f"epoch {epoch.index} events SHA-256 does not match its manifest")
        verified.append(
            VerifiedEpoch(
                info=epoch,
                manifest_sha256=sha256_file(manifest_path),
                events_sha256=actual_sha,
                events_bytes=actual_bytes,
                record_count=actual_count,
            )
        )
    return verified


def _assert_sources_unchanged(epochs: list[VerifiedEpoch]) -> None:
    for epoch in epochs:
        manifest_path = epoch.info.path / "manifest.json"
        events_path = epoch.info.path / "events.jsonl"
        try:
            unchanged = sha256_file(manifest_path) == epoch.manifest_sha256 and events_path.stat().st_size == epoch.events_bytes and sha256_file(events_path) == epoch.events_sha256
        except OSError as exc:
            raise RecorderError(f"sealed source disappeared during export: {epoch.info.path}") from exc
        if not unchanged:
            raise RecorderError(f"sealed source changed during export: {epoch.info.path}")


def _validated_source_snapshot(
    supplied: dict[str, Any],
    *,
    session_id: str,
    session_manifest_sha: str,
    verified_epochs: list[VerifiedEpoch],
    selected_players: set[str],
    selected_connections: set[str],
) -> dict[str, Any]:
    if supplied.get("format") != "append_prefix_v1":
        raise RecorderError("capture snapshot format is unsupported")
    if supplied.get("session_id") != session_id:
        raise RecorderError("capture snapshot has the wrong session")
    player_uuid = supplied.get("player_uuid")
    connection_id = supplied.get("connection_id")
    if not isinstance(player_uuid, str) or selected_players != {player_uuid}:
        raise RecorderError("capture snapshot requires its exact player selection")
    if not isinstance(connection_id, str) or selected_connections != {connection_id}:
        raise RecorderError("capture snapshot requires its exact connection selection")
    source_episode_value = supplied.get("source_episode")
    if not isinstance(source_episode_value, str):
        raise RecorderError("capture snapshot source episode is missing")
    source_episode = Path(source_episode_value)
    if not source_episode.is_absolute() or source_episode.is_symlink() or not source_episode.is_dir():
        raise RecorderError("capture snapshot source episode is invalid")
    source_manifest = source_episode / "manifest.json"
    if source_manifest.is_symlink() or not source_manifest.is_file():
        raise RecorderError("capture snapshot source manifest is invalid")
    if sha256_file(source_manifest) != session_manifest_sha:
        raise RecorderError("capture snapshot source manifest changed")

    raw_segments = supplied.get("segments")
    if not isinstance(raw_segments, list) or len(raw_segments) != len(verified_epochs):
        raise RecorderError("capture snapshot segment count does not match its episode")
    epoch_by_index = {epoch.info.index: epoch for epoch in verified_epochs}
    validated_segments: list[dict[str, Any]] = []
    for ordinal, raw_segment in enumerate(raw_segments):
        if not isinstance(raw_segment, dict):
            raise RecorderError(f"capture snapshot segment {ordinal} is not an object")
        epoch_index = raw_segment.get("epoch_index")
        epoch = epoch_by_index.get(epoch_index) if isinstance(epoch_index, int) and not isinstance(epoch_index, bool) else None
        if epoch is None:
            raise RecorderError(f"capture snapshot segment {ordinal} has an unknown epoch")
        kind = raw_segment.get("kind")
        if kind not in {"finalized", "active_prefix"}:
            raise RecorderError(f"capture snapshot segment {ordinal} has an invalid kind")
        source_value = raw_segment.get("source")
        if not isinstance(source_value, str):
            raise RecorderError(f"capture snapshot segment {ordinal} has no source")
        relative = Path(source_value)
        if relative.is_absolute() or ".." in relative.parts:
            raise RecorderError(f"capture snapshot segment {ordinal} source is not contained")
        source = source_episode / relative
        if source.is_symlink() or not source.is_file():
            raise RecorderError(f"capture snapshot segment {ordinal} source is invalid")
        resolved_source = source.resolve()
        try:
            resolved_source.relative_to(source_episode)
        except ValueError as exc:
            raise RecorderError(f"capture snapshot segment {ordinal} source escapes its episode") from exc
        size_bytes = raw_segment.get("bytes")
        observed_bytes = raw_segment.get("observed_bytes")
        record_count = raw_segment.get("record_count")
        expected_sha = raw_segment.get("sha256")
        if not isinstance(size_bytes, int) or isinstance(size_bytes, bool) or size_bytes <= 0:
            raise RecorderError(f"capture snapshot segment {ordinal} byte count is invalid")
        if not isinstance(observed_bytes, int) or isinstance(observed_bytes, bool) or observed_bytes < size_bytes:
            raise RecorderError(f"capture snapshot segment {ordinal} observed size is invalid")
        if not isinstance(record_count, int) or isinstance(record_count, bool) or record_count <= 0:
            raise RecorderError(f"capture snapshot segment {ordinal} record count is invalid")
        if not isinstance(expected_sha, str) or re.fullmatch(r"[0-9a-f]{64}", expected_sha) is None:
            raise RecorderError(f"capture snapshot segment {ordinal} SHA-256 is invalid")
        if (
            epoch.events_bytes != size_bytes
            or epoch.record_count != record_count
            or epoch.events_sha256 != expected_sha
        ):
            raise RecorderError(f"capture snapshot segment {ordinal} does not match its private envelope")
        try:
            before = source.stat()
            with source.open("rb") as handle:
                prefix = handle.read(size_bytes)
            after = source.stat()
        except OSError as exc:
            raise RecorderError(f"cannot verify capture snapshot segment {ordinal}") from exc
        if (
            before.st_dev != after.st_dev
            or before.st_ino != after.st_ino
            or before.st_size < size_bytes
            or after.st_size < size_bytes
            or len(prefix) != size_bytes
            or hashlib.sha256(prefix).hexdigest() != expected_sha
        ):
            raise RecorderError(f"capture snapshot segment {ordinal} source prefix changed")
        validated_segments.append(_plain_json(raw_segment))

    try:
        return json.loads(
            json.dumps(
                {
                    **supplied,
                    "source_episode": str(source_episode),
                    "segments": validated_segments,
                },
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            )
        )
    except (TypeError, ValueError, RecursionError) as exc:
        raise RecorderError("capture snapshot provenance is not valid JSON") from exc


def _attachment_input(path: Path, description: str) -> Path:
    expanded = path.expanduser()
    if expanded.is_symlink():
        raise RecorderError(f"{description} may not be a symlink: {expanded}")
    return expanded.resolve()


def _artifact_inside(root: Path, relative: str, context: str, description: str) -> Path:
    relative_path = Path(relative)
    if relative_path.is_absolute():
        raise RecorderError(f"{context}: {description} path must be relative to its index")
    root = root.resolve()
    candidate = root / relative_path
    cursor = candidate
    while cursor != root:
        if cursor.is_symlink():
            raise RecorderError(f"{context}: {description} path contains a symlink")
        parent = cursor.parent
        if parent == cursor:
            break
        cursor = parent
    resolved = candidate.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise RecorderError(f"{context}: {description} path escapes its index directory") from exc
    if not resolved.is_file():
        raise RecorderError(f"{context}: {description} artifact is missing")
    return resolved


def _artifact_reference(exports_root: Path, dataset_directory: Path, image: Path, context: str) -> str:
    resolved = image.resolve()
    try:
        resolved.relative_to(exports_root.resolve())
    except ValueError as exc:
        raise RecorderError(f"{context}: frame artifact is outside the export root") from exc
    return os.path.relpath(resolved, dataset_directory.resolve()).replace(os.sep, "/")


def _validate_png(path: Path, expected_width: int, expected_height: int, context: str) -> None:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise RecorderError(f"{context}: cannot stat frame artifact") from exc
    size_limit = max(
        MIN_PNG_SIZE_LIMIT,
        expected_width * expected_height * MAX_PNG_BYTES_PER_PIXEL,
    )
    if size > size_limit:
        raise RecorderError(f"{context}: PNG is implausibly large for {expected_width}x{expected_height}")

    seen_ihdr = False
    seen_idat = False
    chunk_count = 0
    try:
        handle = path.open("rb")
    except OSError as exc:
        raise RecorderError(f"{context}: cannot read frame artifact") from exc
    with handle:
        if handle.read(len(_PNG_SIGNATURE)) != _PNG_SIGNATURE:
            raise RecorderError(f"{context}: invalid PNG signature")
        while True:
            header = handle.read(8)
            if len(header) != 8:
                raise RecorderError(f"{context}: truncated PNG chunk header")
            length, chunk_type = struct.unpack(">I4s", header)
            chunk_count += 1
            if chunk_count > 1_000_000:
                raise RecorderError(f"{context}: PNG has too many chunks")
            if any(not (65 <= byte <= 90 or 97 <= byte <= 122) for byte in chunk_type):
                raise RecorderError(f"{context}: invalid PNG chunk type")
            if chunk_count == 1 and chunk_type != b"IHDR":
                raise RecorderError(f"{context}: PNG IHDR is not the first chunk")
            if chunk_type == b"IHDR" and (seen_ihdr or length != 13):
                raise RecorderError(f"{context}: invalid or duplicate PNG IHDR")

            crc = zlib.crc32(chunk_type)
            remaining = length
            ihdr = bytearray()
            while remaining:
                data = handle.read(min(remaining, 1024 * 1024))
                if not data:
                    raise RecorderError(f"{context}: truncated PNG chunk data")
                if chunk_type == b"IHDR":
                    ihdr.extend(data)
                crc = zlib.crc32(data, crc)
                remaining -= len(data)
            stored_crc_bytes = handle.read(4)
            if len(stored_crc_bytes) != 4:
                raise RecorderError(f"{context}: truncated PNG chunk CRC")
            stored_crc = struct.unpack(">I", stored_crc_bytes)[0]
            if stored_crc != crc & 0xFFFFFFFF:
                raise RecorderError(f"{context}: PNG chunk CRC mismatch")

            if chunk_type == b"IHDR":
                seen_ihdr = True
                width, height, bit_depth, color_type, compression, filtering, interlace = struct.unpack(">IIBBBBB", ihdr)
                if (width, height) != (expected_width, expected_height):
                    raise RecorderError(f"{context}: PNG dimensions {width}x{height} do not match renderer result {expected_width}x{expected_height}")
                valid_depths = {
                    0: {1, 2, 4, 8, 16},
                    2: {8, 16},
                    3: {1, 2, 4, 8},
                    4: {8, 16},
                    6: {8, 16},
                }
                if bit_depth not in valid_depths.get(color_type, set()):
                    raise RecorderError(f"{context}: invalid PNG color type or bit depth")
                if compression != 0 or filtering != 0 or interlace not in {0, 1}:
                    raise RecorderError(f"{context}: unsupported PNG IHDR encoding fields")
            elif chunk_type == b"IDAT":
                seen_idat = True
            elif chunk_type == b"IEND":
                if length != 0 or not seen_ihdr or not seen_idat:
                    raise RecorderError(f"{context}: invalid PNG IEND or missing image data")
                if handle.read(1):
                    raise RecorderError(f"{context}: trailing bytes after PNG IEND")
                return


def _resolve_frame_artifacts(path: Path) -> tuple[Path, Path]:
    source = _attachment_input(path, "frame attachment")
    if source.is_dir():
        if (source / "result.json").is_file():
            result = source / "result.json"
        elif (source.parent / "result.json").is_file():
            result = source.parent / "result.json"
        else:
            raise RecorderError(f"frame directory has no associated result.json: {source}")
        index_candidates = (source / "frames.jsonl", source / "frames" / "frames.jsonl")
        index = next((candidate for candidate in index_candidates if candidate.is_file()), None)
        if index is None:
            result_data = _read_object(result, "renderer result")
            canonical_output = _canonical_result_reference(result_data, result.resolve(), "output", "frame output")
            if canonical_output is not None:
                index = canonical_output / "frames.jsonl"
            else:
                output = result_data.get("output")
                if not isinstance(output, str):
                    raise RecorderError(f"renderer result does not identify its frame output: {result}")
                index = Path(output).expanduser().resolve() / "frames.jsonl"
        return result, index
    if not source.is_file() or source.is_symlink():
        raise RecorderError(f"frame attachment does not exist or is symlinked: {source}")
    if source.name == "result.json":
        result_data = _read_object(source, "renderer result")
        canonical_output = _canonical_result_reference(result_data, source.resolve(), "output", "frame output")
        if canonical_output is not None:
            return source, canonical_output / "frames.jsonl"
        output = result_data.get("output")
        if not isinstance(output, str):
            raise RecorderError(f"renderer result does not identify its frame output: {source}")
        return source, Path(output).expanduser().resolve() / "frames.jsonl"
    if source.name == "frames.jsonl":
        candidates = (source.parent / "result.json", source.parent.parent / "result.json")
        result = next((candidate for candidate in candidates if candidate.is_file()), None)
        if result is None:
            raise RecorderError(f"frames.jsonl has no associated completed result.json: {source}")
        return result, source
    raise RecorderError(f"--frames expects a render job directory, result.json, or frames.jsonl: {source}")


def _load_frame_attachments(
    paths: Iterable[Path],
    expected_session: str,
    *,
    exports_root: Path,
    dataset_directory: Path,
) -> tuple[dict[FrameKey, AttachedFrame], list[dict[str, Any]]]:
    attachments: dict[FrameKey, AttachedFrame] = {}
    sources: list[dict[str, Any]] = []
    seen_indexes: set[Path] = set()
    for supplied in paths:
        result_path, index_path = _resolve_frame_artifacts(supplied)
        if result_path.is_symlink() or index_path.is_symlink():
            raise RecorderError("renderer result and frames.jsonl may not be symlinks")
        result_path = result_path.resolve()
        index_path = index_path.resolve()
        if index_path in seen_indexes:
            continue
        seen_indexes.add(index_path)
        result_sha = sha256_file(result_path)
        result = _read_object(result_path, "renderer result")
        if result.get("status") != "complete":
            raise RecorderError(f"renderer result is not complete: {result_path}")
        no_gui = result.get("no_gui", True)
        if not isinstance(no_gui, bool):
            raise RecorderError(f"renderer result no_gui must be a boolean: {result_path}")
        presentation_contract = result.get("presentation_contract")
        structured_hud: dict[str, Any] | None = None
        if presentation_contract is not None:
            if presentation_contract != FULL_CLIENT_PRESENTATION_CONTRACT:
                raise RecorderError(f"renderer result presentation_contract is unsupported: {result_path}")
            if no_gui:
                raise RecorderError(f"renderer result presentation_contract requires no_gui=false: {result_path}")
            structured_hud = validate_hud_result_envelope(result.get("structured_hud"), "renderer result structured_hud")
        elif result.get("structured_hud") is not None:
            raise RecorderError(f"renderer result structured_hud requires a presentation_contract: {result_path}")
        unsupported_packets = None
        if result.get("unsupported_packets") is not None:
            unsupported_packets = validate_unsupported_packet_summary(result["unsupported_packets"], "renderer result unsupported_packets")
        replay_sha, replay_bytes, replay_path = _renderer_replay_integrity(result, result_path)
        session = result.get("session_id")
        player = result.get("player_uuid")
        connection = result.get("connection_id")
        if session != expected_session:
            raise RecorderError(f"renderer result session {session!r} does not match episode {expected_session!r}")
        if not isinstance(player, str) or not isinstance(connection, str):
            raise RecorderError(f"renderer result lacks player_uuid or connection_id: {result_path}")
        if structured_hud is not None and (structured_hud["session_id"] != session or structured_hud["player_uuid"] != player or structured_hud["connection_id"] != connection):
            raise RecorderError(f"renderer result structured_hud identity does not match its subject: {result_path}")
        first_tick = _required_int(result, "global_start_tick", result_path)
        last_tick = _required_int(result, "global_end_tick", result_path)
        if first_tick > last_tick:
            raise RecorderError(f"renderer result has an invalid global tick range: {result_path}")
        if structured_hud is not None and (structured_hud["start_server_tick"] > first_tick or structured_hud["end_server_tick"] < last_tick):
            raise RecorderError(f"renderer result structured_hud range does not cover frame coverage: {result_path}")
        if last_tick - first_tick + 1 > MAX_ATTACHMENT_TICKS:
            raise RecorderError(f"renderer result exceeds the attachment tick limit: {result_path}")
        if _required_int(result, "fps", result_path) != TICK_RATE_HZ:
            raise RecorderError(f"renderer result must be exactly {TICK_RATE_HZ} FPS: {result_path}")
        width = _required_int(result, "width", result_path)
        height = _required_int(result, "height", result_path)
        if not 1 <= width <= 16_384 or not 1 <= height <= 16_384:
            raise RecorderError(f"renderer result dimensions are outside v1 bounds: {result_path}")
        replay_start = _required_int(result, "replay_start_tick", result_path)
        replay_end = _required_int(result, "replay_end_tick", result_path)
        global_offset = _required_int(result, "global_tick_offset", result_path)
        if first_tick < 0 or replay_start < 0 or replay_end < replay_start:
            raise RecorderError(f"renderer result contains a negative or reversed timeline: {result_path}")
        if replay_end - replay_start != last_tick - first_tick:
            raise RecorderError(f"renderer result replay/global ranges have different lengths: {result_path}")
        if global_offset + replay_start != first_tick or global_offset + replay_end != last_tick:
            raise RecorderError(f"renderer result global tick offset is inconsistent: {result_path}")
        canonical_frames = _canonical_result_reference(result, result_path, "output", "frame output")
        output_value = result.get("output")
        if canonical_frames is not None:
            frames_directory = canonical_frames
        else:
            if not isinstance(output_value, str):
                raise RecorderError(f"renderer result lacks output directory: {result_path}")
            unresolved_frames_directory = Path(output_value).expanduser()
            if unresolved_frames_directory.is_symlink():
                raise RecorderError(f"renderer frame output may not be a symlink: {output_value}")
            frames_directory = unresolved_frames_directory.resolve()
        expected_index = frames_directory / "frames.jsonl"
        if index_path != expected_index:
            raise RecorderError(f"frame index {index_path} does not match renderer result output {expected_index}")
        if not index_path.is_file() or index_path.is_symlink():
            raise RecorderError(f"frame index does not exist or is symlinked: {index_path}")
        if index_path.stat().st_size > MAX_ATTACHMENT_INDEX_BYTES:
            raise RecorderError(f"frame index exceeds the size limit: {index_path}")

        index_sha = sha256_file(index_path)
        ticks_seen: set[int] = set()
        row_count = 0
        try:
            handle = index_path.open("rb")
        except OSError as exc:
            raise RecorderError(f"cannot read frame index: {index_path}") from exc
        with handle:
            for line_number, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                if len(line) > MAX_ATTACHMENT_INDEX_LINE_CHARS:
                    raise RecorderError(f"{index_path}:{line_number}: frame row exceeds the size limit")
                try:
                    row = json.loads(line)
                except (ValueError, RecursionError) as exc:
                    raise RecorderError(f"{index_path}:{line_number}: invalid frame JSON: {exc}") from exc
                if not isinstance(row, dict):
                    raise RecorderError(f"{index_path}:{line_number}: frame row must be an object")
                tick = row.get("server_tick")
                if not isinstance(tick, int) or isinstance(tick, bool):
                    raise RecorderError(f"{index_path}:{line_number}: server_tick must be an integer")
                if row.get("session_id") != session:
                    raise RecorderError(f"{index_path}:{line_number}: session_id does not match result")
                if row.get("player_uuid") != player:
                    raise RecorderError(f"{index_path}:{line_number}: player_uuid does not match result")
                if row.get("connection_id") != connection:
                    raise RecorderError(f"{index_path}:{line_number}: connection_id does not match result")
                if tick < first_tick or tick > last_tick:
                    raise RecorderError(f"{index_path}:{line_number}: server_tick is outside result range")
                if tick in ticks_seen:
                    raise RecorderError(f"{index_path}:{line_number}: duplicate server_tick {tick}")
                ticks_seen.add(tick)
                frame_number = row.get("frame")
                if not isinstance(frame_number, int) or isinstance(frame_number, bool) or frame_number != tick - first_tick + 1:
                    raise RecorderError(f"{index_path}:{line_number}: frame number is inconsistent")
                replay_tick = row.get("replay_tick")
                if not isinstance(replay_tick, int) or isinstance(replay_tick, bool) or replay_tick != replay_start + tick - first_tick or global_offset + replay_tick != tick:
                    raise RecorderError(f"{index_path}:{line_number}: replay_tick is inconsistent")
                partial_tick = row.get("partial_tick")
                if not isinstance(partial_tick, (int, float)) or isinstance(partial_tick, bool) or partial_tick != 0.0:
                    raise RecorderError(f"{index_path}:{line_number}: partial_tick must be zero in v1")
                relative = row.get("path")
                if not isinstance(relative, str) or not relative:
                    raise RecorderError(f"{index_path}:{line_number}: frame path is missing")
                context = f"{index_path}:{line_number}"
                image = _artifact_inside(frames_directory, relative, context, "frame")
                artifact_bytes = image.stat().st_size
                artifact_sha = sha256_file(image)
                _validate_png(image, width, height, context)
                if image.stat().st_size != artifact_bytes or sha256_file(image) != artifact_sha:
                    raise RecorderError(f"{context}: frame artifact changed during validation")
                key = (session, player, connection, tick)
                attached = AttachedFrame(
                    reference=_artifact_reference(exports_root, dataset_directory, image, context),
                    path=image,
                    row=row,
                    index_sha256=index_sha,
                    artifact_sha256=artifact_sha,
                    artifact_bytes=artifact_bytes,
                    width=width,
                    height=height,
                )
                existing = attachments.get(key)
                if existing is not None:
                    raise RecorderError(f"multiple renderer attachments claim {session}/{player}/{connection}/tick-{tick}")
                attachments[key] = attached  # ty:ignore[invalid-assignment]
                row_count += 1

        expected_ticks = set(range(first_tick, last_tick + 1))
        if ticks_seen != expected_ticks:
            missing = sorted(expected_ticks - ticks_seen)
            preview = ", ".join(str(tick) for tick in missing[:5])
            raise RecorderError(f"completed frame index is not contiguous for {first_tick}..{last_tick}; missing {preview}")
        if sha256_file(index_path) != index_sha or sha256_file(result_path) != result_sha:
            raise RecorderError(f"renderer result or frame index changed while attaching: {index_path}")
        sources.append(
            {
                "result": str(result_path),
                "result_sha256": result_sha,
                "frames_index": str(index_path),
                "frames_index_sha256": index_sha,
                "session_id": session,
                "player_uuid": player,
                "connection_id": connection,
                "global_start_tick": first_tick,
                "global_end_tick": last_tick,
                "fps": TICK_RATE_HZ,
                "width": width,
                "height": height,
                "no_gui": no_gui,
                **({"presentation_contract": presentation_contract} if presentation_contract is not None else {}),
                **({"structured_hud": structured_hud} if structured_hud is not None else {}),
                **({"unsupported_packets": unsupported_packets} if unsupported_packets is not None else {}),
                "frame_count": row_count,
                "replay": replay_path,
                "replay_sha256": replay_sha,
                "replay_bytes": replay_bytes,
                **(
                    {
                        "portable_request": result.get("portable_request"),
                        "source_replay": result.get("source_replay"),
                        "range_policy": result.get("range_policy"),
                        "newer_cutoff": result.get("newer_cutoff"),
                    }
                    if result.get("result_type") == CANONICAL_RENDER_RESULT_TYPE
                    else {}
                ),
            }
        )
    return attachments, sources


def _load_scene_attachment(supplied_paths: Iterable[Path], expected_session: str) -> VerifiedSceneAttachment | None:
    supplied = list(supplied_paths)
    if not supplied:
        return None
    if len(supplied) != 1:
        raise RecorderError("Dataset V2 accepts exactly one scene store per dataset")

    source = _attachment_input(supplied[0], "scene attachment")
    if source.is_symlink() or not source.is_file():
        raise RecorderError(f"scene attachment must be a regular SQLite store: {source}")
    from minerec.processing.scene.store import (
        SceneStore,
        validate_scene_attachment_provenance,
        validate_scene_store,
    )

    info = validate_scene_store(source, expected_session_id=expected_session)
    extraction = validate_scene_attachment_provenance(info)
    identity = info.identity
    ticks = tuple(info.ticks)
    if not ticks:
        raise RecorderError("scene attachment contains no frames")
    if not info.coverage_complete:
        raise RecorderError("refusing to attach an incomplete scene store")
    if not info.sensitive:
        raise RecorderError("full-metadata scene stores must be marked sensitive")
    frames: dict[FrameKey, AttachedScene] = {}
    with SceneStore(source) as store:
        for tick in ticks:
            frame = store.frame(tick)
            key = (identity.session_id, identity.player_uuid, identity.connection_id, tick)
            frames[key] = AttachedScene(
                reference="scene/scene-v1.sqlite3",
                frame_id=frame.frame_id,
                coverage_complete=frame.coverage_complete,
                dimension=frame.dimension,
                subject_position=frame.subject_position,
            )

    size_bytes = source.stat().st_size
    digest = sha256_file(source)
    if source.stat().st_size != size_bytes or sha256_file(source) != digest:
        raise RecorderError("scene store changed while it was validated")
    provenance = {
        "format": "mc-recorder-scene-store-v1",
        "path": str(source),
        "sha256": digest,
        "size_bytes": size_bytes,
        "session_id": identity.session_id,
        "player_uuid": identity.player_uuid,
        "connection_id": identity.connection_id,
        "global_start_tick": ticks[0],
        "global_end_tick": ticks[-1],
        "frame_count": len(ticks),
        "scope": extraction.scope,
        "metadata_policy": extraction.metadata_policy,
        "result": _plain_json(extraction.result),
        "sensitive": info.sensitive,
        "source_replays": _plain_json(info.source_replays),
    }
    return VerifiedSceneAttachment(
        path=source,
        sha256=digest,
        size_bytes=size_bytes,
        frames=frames,
        provenance=provenance,
    )


def _assert_attachments_unchanged(frames: dict[FrameKey, AttachedFrame]) -> None:
    expected: dict[Path, tuple[int, str, str]] = {}
    for attached in frames.values():
        expected[attached.path] = (
            attached.artifact_bytes,
            attached.artifact_sha256,
            "frame",
        )
    for path, (size, digest, kind) in expected.items():
        try:
            unchanged = path.stat().st_size == size and sha256_file(path) == digest
        except OSError as exc:
            raise RecorderError(f"attached {kind} artifact disappeared during export: {path}") from exc
        if not unchanged:
            raise RecorderError(f"attached {kind} artifact changed during export: {path}")


def export_episode(
    episode: Path,
    output: Path,
    *,
    players: Iterable[str] = (),
    connections: Iterable[str] = (),
    first_tick: int | None = None,
    last_tick: int | None = None,
    frames: Iterable[Path] = (),
    scenes: Iterable[Path] = (),
    source_snapshot: dict[str, Any] | None = None,
    force: bool = False,
) -> ExportResult:
    """Validate, read, and atomically publish while source epochs are pinned."""

    with pin_sealed_epochs(episode) as pinned_epochs:
        return _export_episode_pinned(
            episode,
            output,
            players=players,
            connections=connections,
            first_tick=first_tick,
            last_tick=last_tick,
            frames=frames,
            scenes=scenes,
            source_snapshot=source_snapshot,
            force=force,
            pinned_epochs=pinned_epochs,
        )


def _export_episode_pinned(
    episode: Path,
    output: Path,
    *,
    players: Iterable[str] = (),
    connections: Iterable[str] = (),
    first_tick: int | None = None,
    last_tick: int | None = None,
    frames: Iterable[Path] = (),
    scenes: Iterable[Path] = (),
    source_snapshot: dict[str, Any] | None,
    force: bool = False,
    pinned_epochs: tuple[Path, ...],
) -> ExportResult:
    if first_tick is not None and last_tick is not None and first_tick > last_tick:
        raise RecorderError("--from-tick cannot be greater than --to-tick")
    validation = validate_episode(episode)
    if not validation.valid:
        raise RecorderError("episode validation failed; run 'minerec episodes validate' for details")
    if validation.sealed_epochs == 0:
        raise RecorderError("episode has no sealed epochs to export")

    verified_epochs = _verified_sealed_epochs(episode, validation.session_id)
    if not verified_epochs:
        raise RecorderError("episode has no explicitly sealed, hash-verified epochs to export")
    if len(verified_epochs) != validation.sealed_epochs:
        raise RecorderError("sealed epoch set changed during validation; retry the export")
    if {epoch.info.path.resolve() for epoch in verified_epochs} != set(pinned_epochs):
        raise RecorderError("sealed epoch set changed while it was being pinned; retry the export")
    epoch_hashes = {epoch.info.index: epoch for epoch in verified_epochs}
    requested_output = output.expanduser()
    if requested_output.is_symlink():
        raise RecorderError(f"refusing symlinked export output: {requested_output}")
    output = requested_output.resolve()
    exports_root = output.parent.resolve()
    frame_attachments, frame_sources = _load_frame_attachments(
        frames,
        validation.session_id,
        exports_root=exports_root,
        dataset_directory=output,
    )
    selected_players = _normalize_players(players)
    selected_connections = _normalize_connections(connections)
    validated_snapshot = (
        _validated_source_snapshot(
            source_snapshot,
            session_id=validation.session_id,
            session_manifest_sha=sha256_file(episode / "manifest.json"),
            verified_epochs=verified_epochs,
            selected_players=selected_players,
            selected_connections=selected_connections,
        )
        if source_snapshot is not None
        else None
    )
    scene_attachment = _load_scene_attachment(scenes, validation.session_id)
    scene_frames = scene_attachment.frames if scene_attachment is not None else {}
    if scene_attachment is not None and scene_frames:
        _session, scene_player, scene_connection, _tick = next(iter(scene_frames))
        if selected_players and scene_player not in selected_players:
            raise RecorderError("scene attachment player is outside the export selection")
        if selected_connections and scene_connection not in selected_connections:
            raise RecorderError("scene attachment connection is outside the export selection")
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}.tmp-", dir=output.parent))
    sample_count = state_count = action_count = modality_count = rgb_count = scene_count = 0
    session_manifest = episode / "manifest.json"
    session_manifest_sha = sha256_file(session_manifest)

    try:
        states_path = staging / "states.jsonl"
        actions_path = staging / "actions.jsonl"
        modalities_path = staging / "modalities.jsonl"
        samples_path = staging / "samples.jsonl"
        previous: TickBundle | None = None
        current: TickBundle | None = None
        observed_state_keys: set[FrameKey] = set()

        with (
            states_path.open("w", encoding="utf-8", newline="\n") as states_file,
            actions_path.open("w", encoding="utf-8", newline="\n") as actions_file,
            modalities_path.open("w", encoding="utf-8", newline="\n") as modalities_file,
            samples_path.open("w", encoding="utf-8", newline="\n") as samples_file,
        ):

            def finish_bundle(bundle: TickBundle) -> None:
                nonlocal previous, sample_count
                if previous is not None:
                    for subject in sorted(set(previous.states) & set(bundle.states)):
                        previous_state = previous.states[subject]
                        if not _selected(
                            previous_state,
                            selected_players,
                            selected_connections,
                            first_tick,
                            last_tick,
                        ):
                            continue
                        samples_file.write(
                            _json_line(
                                _sample_row(
                                    previous,
                                    bundle,
                                    subject,
                                    session_manifest_sha,
                                    epoch_hashes,
                                    frame_attachments,
                                    scene_frames,
                                )
                            )
                        )
                        sample_count += 1
                previous = bundle

            for verified in verified_epochs:
                for _line_number, record in iter_events(verified.info):
                    tick = record.get("server_tick")
                    if not isinstance(tick, int) or isinstance(tick, bool):
                        continue
                    if current is None:
                        current = TickBundle(tick=tick)
                    elif current.tick != tick:
                        finish_bundle(current)
                        current = TickBundle(tick=tick)

                    record_type = record.get("record_type")
                    key = _subject_key(record)
                    if record_type == "player_state" and key is not None:
                        if key in current.states:
                            raise RecorderError(f"duplicate player_state for {key[0]}/{key[1]} at server tick {tick}")
                        current.states[key] = record
                        state_key = _frame_key(record)
                        if state_key is not None:
                            observed_state_keys.add(state_key)
                            attached_scene = scene_frames.get(state_key)
                            if attached_scene is not None:
                                _validate_scene_pose_binding(record, attached_scene, state_key)
                        if _selected(
                            record,
                            selected_players,
                            selected_connections,
                            first_tick,
                            last_tick,
                        ):
                            modality = _modality_entry(record, frame_attachments, scene_frames, epoch_hashes)
                            states_file.write(_json_line(_state_row(record, epoch_hashes)))
                            modalities_file.write(_json_line(modality))
                            state_count += 1
                            modality_count += 1
                            if modality["rgb"]["available"]:
                                rgb_count += 1
                            if modality["scene"]["available"]:
                                scene_count += 1
                    elif record_type == "control_state" and key is not None:
                        if key in current.controls:
                            raise RecorderError(f"duplicate control_state for {key[0]}/{key[1]} at server tick {tick}")
                        current.controls[key] = record
                        if _selected(
                            record,
                            selected_players,
                            selected_connections,
                            first_tick,
                            last_tick,
                        ):
                            actions_file.write(_json_line(_action_row(record, epoch_hashes)))
                            action_count += 1
                    elif record_type == "packet_apply" and key is not None:
                        current.packets.setdefault(key, []).append(record)
                        if _selected(
                            record,
                            selected_players,
                            selected_connections,
                            first_tick,
                            last_tick,
                        ):
                            actions_file.write(_json_line(_action_row(record, epoch_hashes)))
                            action_count += 1
                    elif record_type == "action" and key is not None:
                        if _selected(
                            record,
                            selected_players,
                            selected_connections,
                            first_tick,
                            last_tick,
                        ):
                            actions_file.write(_json_line(_action_row(record, epoch_hashes)))
                            action_count += 1
                    elif record_type == "tick_end":
                        if current.tick_end_seen:
                            raise RecorderError(f"duplicate tick_end at server tick {tick}")
                        barrier = record.get("apply_sequence_at_barrier")
                        current.barrier = barrier if isinstance(barrier, int) and not isinstance(barrier, bool) else None
                        current.tick_end_seen = True

            if current is not None:
                finish_bundle(current)

        unmatched_frames = set(frame_attachments) - observed_state_keys
        unmatched_scenes = set(scene_frames) - observed_state_keys
        if unmatched_frames:
            raise RecorderError(f"frame attachment contains a key with no matching episode player_state: {next(iter(sorted(unmatched_frames)))}")
        if unmatched_scenes:
            raise RecorderError(f"scene attachment contains a frame with no matching episode player_state: {next(iter(sorted(unmatched_scenes)))}")

        _assert_attachments_unchanged(frame_attachments)
        if scene_attachment is not None:
            scene_directory = staging / "scene"
            scene_directory.mkdir()
            scene_destination = scene_directory / "scene-v1.sqlite3"
            shutil.copyfile(scene_attachment.path, scene_destination)
            if (
                scene_destination.stat().st_size != scene_attachment.size_bytes
                or sha256_file(scene_destination) != scene_attachment.sha256
                or scene_attachment.path.stat().st_size != scene_attachment.size_bytes
                or sha256_file(scene_attachment.path) != scene_attachment.sha256
            ):
                raise RecorderError("scene store changed while it was attached")
        _assert_sources_unchanged(verified_epochs)
        if sha256_file(session_manifest) != session_manifest_sha:
            raise RecorderError(f"session manifest changed during export: {session_manifest}")

        files: dict[str, dict[str, Any]] = {}
        for path in (samples_path, states_path, actions_path, modalities_path):
            files[path.name] = {
                "sha256": sha256_file(path),
                "size_bytes": path.stat().st_size,
            }
        if scene_attachment is not None:
            scene_path = staging / "scene" / "scene-v1.sqlite3"
            files["scene/scene-v1.sqlite3"] = {
                "sha256": sha256_file(scene_path),
                "size_bytes": scene_path.stat().st_size,
            }
        source_manifest = {
            "episode": (
                validated_snapshot["source_episode"]
                if validated_snapshot is not None
                else str(episode.resolve())
            ),
            "manifest_sha256": session_manifest_sha,
            "sealed_epochs": len(verified_epochs),
            "active_epochs_skipped": (
                0 if validated_snapshot is not None else validation.active_epochs
            ),
            "epochs": [epoch.manifest_entry() for epoch in verified_epochs],
        }
        if validated_snapshot is not None:
            source_manifest["snapshot"] = validated_snapshot
        manifest = {
            "schema_version": EXPORT_SCHEMA_VERSION,
            "owner": OWNER,
            "format": EXPORT_FORMAT,
            "created_at": datetime.now(UTC).isoformat(),
            "session_id": validation.session_id,
            "source_manifest_sha256": session_manifest_sha,
            "source": source_manifest,
            "selection": {
                "players": sorted(selected_players),
                "connections": sorted(selected_connections),
                "from_tick": first_tick,
                "to_tick": last_tick,
                "frame_attachments": frame_sources,
                "scene_attachment": (scene_attachment.provenance if scene_attachment is not None else None),
            },
            "timeline": {
                "tick_rate_hz": TICK_RATE_HZ,
                "sample_rate_hz": TICK_RATE_HZ,
                "transition": ("post_state[t] + reconstructed_control[t+1] + ordered packets with barrier[t] < apply_sequence <= barrier[t+1] -> post_state[t+1]"),
                "latency_compensation": False,
            },
            "modalities": {
                "samples": {"available": True, "records": sample_count, "file": "samples.jsonl"},
                "state": {"available": True, "records": state_count, "file": "states.jsonl"},
                "actions": {"available": True, "records": action_count, "file": "actions.jsonl"},
                "scene": {
                    "availability": "per-sample",
                    "records_attached": scene_count,
                    "index": "modalities.jsonl",
                    "store": "scene/scene-v1.sqlite3" if scene_attachment is not None else None,
                    "note": ("immutable random-access frames are joined only by exact session/player/connection/global-server-tick identity"),
                },
                "rgb": {
                    "availability": "per-sample",
                    "records_attached": rgb_count,
                    "index": "modalities.jsonl",
                    "note": ("relative frame references are joined only by exact session/player/connection/global-server-tick identity"),
                },
            },
            "files": files,
        }
        manifest_path = staging / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        durable_paths = [samples_path, states_path, actions_path, modalities_path, manifest_path]
        if scene_attachment is not None:
            durable_paths.append(staging / "scene" / "scene-v1.sqlite3")
        for path in durable_paths:
            with path.open("rb") as handle:
                os.fsync(handle.fileno())
        if scene_attachment is not None:
            _fsync_directory(staging / "scene")
        _fsync_directory(staging)
        _safe_replace_directory(staging, output, force)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        raise

    return ExportResult(
        output=output,
        sample_count=sample_count,
        state_count=state_count,
        action_count=action_count,
        modality_count=modality_count,
        rgb_count=rgb_count,
        scene_count=scene_count,
    )
