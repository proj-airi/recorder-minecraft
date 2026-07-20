from __future__ import annotations

import array
import base64
import binascii
import gzip
import json
import os
import re
import shutil
import struct
import sys
import tempfile
import uuid
import zlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

from .episodes import EpochInfo, iter_epochs, iter_events, sha256_file, validate_episode
from .errors import RecorderError


EXPORT_SCHEMA_VERSION = 1
EXPORT_FORMAT = "mc-recorder-jsonl-v1"
CANONICAL_RENDER_RESULT_TYPE = "mc-recorder-render-result-v2"
OWNER = "mc-recorder"
TICK_RATE_HZ = 20
MAX_PNG_BYTES_PER_PIXEL = 8
MIN_PNG_SIZE_LIMIT = 16 * 1024 * 1024
MAX_VOXEL_COMPRESSED_BYTES = 64 * 1024 * 1024
MAX_VOXEL_JSON_BYTES = 64 * 1024 * 1024
MAX_VOXEL_CELLS = 2_000_000
MAX_VOXEL_AXIS = 129
MAX_ATTACHMENT_INDEX_BYTES = 64 * 1024 * 1024
MAX_ATTACHMENT_INDEX_LINE_CHARS = 64 * 1024
MAX_ATTACHMENT_TICKS = 100_000
MAX_JSON_OBJECT_BYTES = 16 * 1024 * 1024
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_RESOURCE_LOCATION = re.compile(r"^[a-z0-9_.-]+:[a-z0-9/._-]+$")
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
    voxel_count: int


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
    row: dict[str, Any]
    index_sha256: str
    artifact_sha256: str
    artifact_bytes: int
    width: int
    height: int


@dataclass(frozen=True)
class AttachedVoxel:
    reference: str
    row: dict[str, Any]
    index_sha256: str
    artifact_sha256: str
    artifact_bytes: int
    dimension: str


def _json_line(value: dict[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"


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


def _renderer_replay_integrity(
    result: dict[str, Any], result_path: Path
) -> tuple[str, int, str]:
    if result.get("result_type") == CANONICAL_RENDER_RESULT_TYPE:
        if result.get("schema_version") != 2:
            raise RecorderError(f"canonical renderer result has an invalid schema: {result_path}")
        if result.get("artifact_root") != ".":
            raise RecorderError(
                f"canonical renderer result has an invalid artifact root: {result_path}"
            )
        portable = result.get("portable_request")
        if not isinstance(portable, dict):
            raise RecorderError(
                f"canonical renderer result lacks portable_request provenance: {result_path}"
            )
        try:
            request_id = str(uuid.UUID(portable.get("request_id")))
        except (ValueError, TypeError, AttributeError) as exc:
            raise RecorderError(
                f"canonical renderer result has an invalid request_id: {result_path}"
            ) from exc
        if portable.get("request_id") != request_id or re.fullmatch(
            r"[0-9a-f]{64}", str(portable.get("sha256", ""))
        ) is None:
            raise RecorderError(
                f"canonical renderer result has invalid request provenance: {result_path}"
            )
        source = result.get("source_replay")
        if not isinstance(source, dict):
            raise RecorderError(f"canonical renderer result lacks source_replay: {result_path}")
        segment_id = source.get("segment_id")
        segment_ordinal = source.get("segment_ordinal")
        replay_sha = source.get("sha256")
        replay_bytes = source.get("size_bytes")
        if (
            not isinstance(segment_id, str)
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,159}", segment_id) is None
        ):
            raise RecorderError(f"canonical renderer result lacks segment_id: {result_path}")
        if (
            not isinstance(segment_ordinal, int)
            or isinstance(segment_ordinal, bool)
            or not 0 <= segment_ordinal <= 2**31 - 1
            or source.get("format") != "flashback"
        ):
            raise RecorderError(
                f"canonical renderer result has invalid segment provenance: {result_path}"
            )
        if not isinstance(replay_sha, str) or re.fullmatch(r"[0-9a-f]{64}", replay_sha) is None:
            raise RecorderError(
                f"canonical renderer result lacks a valid replay SHA-256: {result_path}"
            )
        if (
            not isinstance(replay_bytes, int)
            or isinstance(replay_bytes, bool)
            or replay_bytes <= 0
            or replay_bytes > 2**63 - 1
        ):
            raise RecorderError(
                f"canonical renderer result lacks a positive replay size: {result_path}"
            )
        return replay_sha, replay_bytes, f"segment:{segment_id}"
    replay = result.get("replay")
    replay_sha = result.get("replay_sha256")
    replay_bytes = result.get("replay_bytes")
    if not isinstance(replay, str) or not replay:
        raise RecorderError(f"renderer result lacks its replay path: {result_path}")
    if (
        not isinstance(replay_sha, str)
        or re.fullmatch(r"[0-9a-fA-F]{64}", replay_sha) is None
    ):
        raise RecorderError(f"renderer result lacks a valid replay_sha256: {result_path}")
    if (
        not isinstance(replay_bytes, int)
        or isinstance(replay_bytes, bool)
        or replay_bytes <= 0
        or replay_bytes > 2**63 - 1
    ):
        raise RecorderError(f"renderer result lacks a positive replay_bytes: {result_path}")
    return replay_sha.lower(), replay_bytes, replay


def _canonical_result_reference(
    result: dict[str, Any], result_path: Path, key: str, description: str
) -> Path | None:
    """Resolve a v2 result reference without accepting a browser/worker path."""
    if result.get("result_type") != CANONICAL_RENDER_RESULT_TYPE:
        return None
    # Validate the non-path provenance envelope before resolving any reference.
    _renderer_replay_integrity(result, result_path)
    value = result.get(key)
    if not isinstance(value, str) or not value:
        raise RecorderError(f"canonical renderer result lacks {description}: {result_path}")
    if "\\" in value:
        raise RecorderError(
            f"canonical renderer result {description} must use a contained POSIX path"
        )
    relative = Path(value)
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        raise RecorderError(
            f"canonical renderer result {description} must be a contained relative path"
        )
    root = result_path.parent.resolve()
    candidate = root / relative
    cursor = candidate
    while cursor != root:
        if cursor.is_symlink():
            raise RecorderError(
                f"canonical renderer result {description} contains a symlink"
            )
        parent = cursor.parent
        if parent == cursor:
            break
        cursor = parent
    resolved = candidate.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise RecorderError(
            f"canonical renderer result {description} escapes its import directory"
        ) from exc
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


def _action_row(
    record: dict[str, Any], epoch_hashes: dict[int, VerifiedEpoch]
) -> dict[str, Any]:
    nested_action = record.get("action")
    nested_packet = record.get("packet")
    if isinstance(nested_action, dict):
        payload = nested_action
    elif isinstance(nested_packet, dict):
        payload = nested_packet
    else:
        payload = _payload(record)

    action_type: Any = (
        record.get("action_type")
        or record.get("action_kind")
        or record.get("packet_type")
        or record.get("packet_class")
    )
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


def _state_row(
    record: dict[str, Any], epoch_hashes: dict[int, VerifiedEpoch]
) -> dict[str, Any]:
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
    if (
        not isinstance(session, str)
        or player is None
        or not isinstance(connection, str)
        or not isinstance(tick, int)
        or isinstance(tick, bool)
    ):
        return None
    return session, player, connection, tick


def _modality_entry(
    record: dict[str, Any],
    frames: dict[FrameKey, AttachedFrame],
    voxels: dict[FrameKey, AttachedVoxel],
    epoch_hashes: dict[int, VerifiedEpoch],
) -> dict[str, Any]:
    voxel_ref = record.get("voxel_ref")
    voxel_mask = record.get("voxel_coverage_mask_ref") or record.get("coverage_mask_ref")
    attached_voxel = voxels.get(_frame_key(record))
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
    if attached_voxel is not None:
        voxel = {
            "available": True,
            "reference": attached_voxel.reference,
            "coverage_mask_reference": attached_voxel.reference,
            "valid": True,
            "reason": None,
            "replay_tick": attached_voxel.row.get("replay_tick"),
            "origin": attached_voxel.row.get("origin"),
            "shape": attached_voxel.row.get("shape"),
            "covered_cells": attached_voxel.row.get("covered_cells"),
            "total_cells": attached_voxel.row.get("total_cells"),
            "coverage_complete": attached_voxel.row.get("coverage_complete"),
            "voxels_index_sha256": attached_voxel.index_sha256,
            "artifact_sha256": attached_voxel.artifact_sha256,
            "artifact_bytes": attached_voxel.artifact_bytes,
            "dimension": attached_voxel.dimension,
        }
    else:
        voxel = {
            "available": False,
            "reference": None,
            "coverage_mask_reference": None,
            "valid": False,
            "reason": (
                "source-provided voxel references are unvalidated; attach voxels.jsonl explicitly"
                if voxel_ref is not None
                else "no voxel converter artifact is attached"
            ),
            "unvalidated_source_reference": voxel_ref,
            "unvalidated_source_coverage_mask_reference": voxel_mask,
        }
    return {
        "schema_version": EXPORT_SCHEMA_VERSION,
        "session_id": record.get("session_id"),
        "epoch_index": record.get("epoch_index"),
        "server_tick": record.get("server_tick"),
        "player_uuid": _player_uuid(record),
        "connection_id": record.get("connection_id"),
        "voxels": voxel,
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
    elif current_state_barrier < previous_state_barrier:
        reasons.append("apply_barrier_moved_backwards")

    if control is None:
        reasons.append("missing_reconstructed_control")

    apply_sequences = [packet.get("apply_sequence") for packet in packets]
    if any(not isinstance(value, int) or isinstance(value, bool) for value in apply_sequences):
        reasons.append("packet_missing_apply_sequence")
    elif any(left >= right for left, right in zip(apply_sequences, apply_sequences[1:])):
        reasons.append("packet_apply_sequence_not_strictly_increasing")

    if all(isinstance(value, int) and not isinstance(value, bool) for value in barriers + tuple(apply_sequences)):
        previous_barrier = int(previous_state_barrier)
        current_barrier = int(current_state_barrier)
        if any(not previous_barrier < int(value) <= current_barrier for value in apply_sequences):
            reasons.append("packet_apply_sequence_outside_transition_barriers")
    return reasons


def _sample_row(
    previous: TickBundle,
    current: TickBundle,
    key: SubjectKey,
    session_manifest_sha: str,
    epoch_hashes: dict[int, VerifiedEpoch],
    frames: dict[FrameKey, AttachedFrame],
    voxels: dict[FrameKey, AttachedVoxel],
) -> dict[str, Any]:
    previous_state = previous.states[key]
    current_state = current.states[key]
    player_uuid, connection_id = key
    control = current.controls.get(key)
    assigned_packets = current.packets.get(key, [])
    previous_barrier = previous_state.get("state_barrier_apply_sequence")
    current_barrier = current_state.get("state_barrier_apply_sequence")
    if (
        isinstance(previous_barrier, int)
        and not isinstance(previous_barrier, bool)
        and isinstance(current_barrier, int)
        and not isinstance(current_barrier, bool)
    ):
        packets = [
            packet
            for packet in assigned_packets
            if isinstance(packet.get("apply_sequence"), int)
            and not isinstance(packet.get("apply_sequence"), bool)
            and previous_barrier < packet["apply_sequence"] <= current_barrier
        ]
    else:
        packets = []
    packets.sort(
        key=lambda item: (
            item.get("apply_sequence")
            if isinstance(item.get("apply_sequence"), int)
            and not isinstance(item.get("apply_sequence"), bool)
            else 2**63,
            item.get("sequence") if isinstance(item.get("sequence"), int) else 2**63,
        ),
    )
    invalid_reasons = _transition_invalid_reasons(
        previous, current, previous_state, current_state, control, assigned_packets
    )
    modality = _modality_entry(previous_state, frames, voxels, epoch_hashes)
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
        "modalities": {"voxels": modality["voxels"], "rgb": modality["rgb"]},
        "transition_valid": not invalid_reasons,
        "transition_invalid_reasons": invalid_reasons,
        "source_manifest_sha256": session_manifest_sha,
        "source": {
            "state": _source_ref(previous_state, epoch_hashes),
            "reconstructed_control": (
                _source_ref(control, epoch_hashes) if control is not None else None
            ),
            "ordered_packets": [_source_ref(packet, epoch_hashes) for packet in packets],
            "next_state": _source_ref(current_state, epoch_hashes),
        },
    }


def _owned_export_directory(path: Path) -> bool:
    manifest = path / "manifest.json"
    try:
        value = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if (
        not isinstance(value, dict)
        or value.get("owner") != OWNER
        or value.get("format") != EXPORT_FORMAT
    ):
        return False
    expected_names = {
        "manifest.json",
        "samples.jsonl",
        "states.jsonl",
        "actions.jsonl",
        "modalities.jsonl",
    }
    try:
        entries = list(path.iterdir())
        if {entry.name for entry in entries} != expected_names:
            return False
        if any(entry.is_symlink() or not entry.is_file() for entry in entries):
            return False
        files = value.get("files")
        if not isinstance(files, dict) or set(files) != expected_names - {"manifest.json"}:
            return False
        for name, metadata in files.items():
            if not isinstance(metadata, dict):
                return False
            artifact = path / name
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
        raise RecorderError(
            f"refusing to replace non-owned export directory: {output}; choose an empty output path"
        )

    try:
        original = output.lstat()
    except OSError as exc:
        raise RecorderError(f"cannot inspect existing export output: {output}") from exc
    backup = output.parent / f".{output.name}.backup-{uuid.uuid4().hex}"
    output.rename(backup)
    promoted = False
    try:
        moved = backup.lstat()
        if (
            backup.is_symlink()
            or not backup.is_dir()
            or (original.st_dev, original.st_ino) != (moved.st_dev, moved.st_ino)
            or not _owned_export_directory(backup)
        ):
            raise RecorderError("existing export changed while preparing its atomic replacement")
        staging.rename(output)
        promoted = True
        _fsync_directory(output.parent)
    except Exception:
        if (
            not promoted
            and not output.exists()
            and not output.is_symlink()
            and (backup.exists() or backup.is_symlink())
        ):
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
            raise RecorderError(
                f"epoch {epoch.index} record count mismatch: manifest {expected_count}, file {actual_count}"
            )
        if actual_bytes != expected_bytes:
            raise RecorderError(
                f"epoch {epoch.index} byte count mismatch: manifest {expected_bytes}, file {actual_bytes}"
            )
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
            unchanged = (
                sha256_file(manifest_path) == epoch.manifest_sha256
                and events_path.stat().st_size == epoch.events_bytes
                and sha256_file(events_path) == epoch.events_sha256
            )
        except OSError as exc:
            raise RecorderError(f"sealed source disappeared during export: {epoch.info.path}") from exc
        if not unchanged:
            raise RecorderError(f"sealed source changed during export: {epoch.info.path}")


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
        raise RecorderError(
            f"{context}: PNG is implausibly large for {expected_width}x{expected_height}"
        )

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
                width, height, bit_depth, color_type, compression, filtering, interlace = (
                    struct.unpack(">IIBBBBB", ihdr)
                )
                if (width, height) != (expected_width, expected_height):
                    raise RecorderError(
                        f"{context}: PNG dimensions {width}x{height} do not match "
                        f"renderer result {expected_width}x{expected_height}"
                    )
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


def _decode_base64(value: Any, maximum_encoded: int, context: str, field: str) -> bytes:
    if not isinstance(value, str):
        raise RecorderError(f"{context}: voxel {field} must be a base64 string")
    if len(value) > maximum_encoded:
        raise RecorderError(f"{context}: voxel {field} exceeds its size bound")
    try:
        return base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise RecorderError(f"{context}: voxel {field} is not valid base64") from exc


def _validate_voxel_artifact(
    path: Path,
    index_row: dict[str, Any],
    expected_shape: tuple[int, int, int] | None,
    context: str,
) -> str:
    try:
        compressed_bytes = path.stat().st_size
    except OSError as exc:
        raise RecorderError(f"{context}: cannot stat voxel artifact") from exc
    if compressed_bytes > MAX_VOXEL_COMPRESSED_BYTES:
        raise RecorderError(f"{context}: compressed voxel artifact exceeds the size limit")
    try:
        with gzip.open(path, "rb") as handle:
            raw = handle.read(MAX_VOXEL_JSON_BYTES + 1)
    except (gzip.BadGzipFile, EOFError, OSError) as exc:
        raise RecorderError(f"{context}: voxel artifact is not a valid complete gzip stream") from exc
    if len(raw) > MAX_VOXEL_JSON_BYTES:
        raise RecorderError(f"{context}: decompressed voxel JSON exceeds the size limit")
    try:
        snapshot = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise RecorderError(f"{context}: voxel artifact is not valid UTF-8 JSON") from exc
    if not isinstance(snapshot, dict):
        raise RecorderError(f"{context}: voxel snapshot must be a JSON object")
    if snapshot.get("schema_version") != 1 or isinstance(snapshot.get("schema_version"), bool):
        raise RecorderError(f"{context}: unsupported voxel schema_version")
    if snapshot.get("format") != "mc-recorder-voxel-palette-v1":
        raise RecorderError(f"{context}: unsupported voxel format")
    for field_name in ("session_id", "connection_id", "player_uuid"):
        if not isinstance(snapshot.get(field_name), str):
            raise RecorderError(f"{context}: voxel snapshot {field_name} must be a string")
        if snapshot.get(field_name) != index_row.get(field_name):
            raise RecorderError(
                f"{context}: voxel snapshot {field_name} does not match its index row"
            )
    for field_name in ("server_tick", "replay_tick"):
        if _required_int(snapshot, field_name, context) != index_row.get(field_name):
            raise RecorderError(
                f"{context}: voxel snapshot {field_name} does not match its index row"
            )

    dimension = snapshot.get("dimension")
    if not isinstance(dimension, str) or _RESOURCE_LOCATION.fullmatch(dimension) is None:
        raise RecorderError(f"{context}: voxel dimension is not a valid resource location")
    origin = _voxel_vector(snapshot, "origin", context)
    shape = _voxel_vector(snapshot, "shape", context)
    center = _voxel_vector(snapshot, "center", context)
    if origin != _voxel_vector(index_row, "origin", context):
        raise RecorderError(f"{context}: voxel origin does not match its index row")
    if shape != _voxel_vector(index_row, "shape", context):
        raise RecorderError(f"{context}: voxel shape does not match its index row")
    if expected_shape is not None and shape != expected_shape:
        raise RecorderError(
            f"{context}: voxel shape {shape} does not match renderer result {expected_shape}"
        )
    if any(size <= 0 or size > MAX_VOXEL_AXIS or size % 2 == 0 for size in shape):
        raise RecorderError(f"{context}: voxel shape must contain bounded odd positive axes")
    if any(value < -(2**31) or value > 2**31 - 1 for value in origin + center):
        raise RecorderError(f"{context}: voxel origin or center is outside signed 32-bit bounds")
    expected_center = tuple(origin[axis] + (shape[axis] - 1) // 2 for axis in range(3))
    if center != expected_center:
        raise RecorderError(f"{context}: voxel center is inconsistent with origin and shape")

    total = shape[0] * shape[1] * shape[2]
    if total > MAX_VOXEL_CELLS:
        raise RecorderError(f"{context}: voxel crop exceeds the v1 cell limit")
    snapshot_total = _required_int(snapshot, "total_cells", context)
    index_total = _required_int(index_row, "total_cells", context)
    if snapshot_total != total or index_total != total:
        raise RecorderError(f"{context}: voxel total_cells does not match shape")
    covered = _required_int(snapshot, "covered_cells", context)
    if covered != _required_int(index_row, "covered_cells", context) or not 0 <= covered <= total:
        raise RecorderError(f"{context}: voxel covered_cells does not match its index row")
    coverage_complete = snapshot.get("coverage_complete")
    if (
        not isinstance(coverage_complete, bool)
        or coverage_complete != index_row.get("coverage_complete")
        or coverage_complete != (covered == total)
    ):
        raise RecorderError(f"{context}: voxel coverage_complete is inconsistent")

    if snapshot.get("linear_order") != "x_fastest_then_z_then_y":
        raise RecorderError(f"{context}: unsupported voxel linear_order")
    if snapshot.get("index_byte_order") != "little_endian":
        raise RecorderError(f"{context}: unsupported voxel index byte order")
    dtype = snapshot.get("index_dtype")
    if dtype not in {"uint16", "uint32"}:
        raise RecorderError(f"{context}: unsupported voxel index dtype")
    palette = snapshot.get("palette")
    if not isinstance(palette, list) or not palette or len(palette) > total + 1:
        raise RecorderError(f"{context}: voxel palette is missing or implausibly large")
    if palette[0] != "minecraft:air":
        raise RecorderError(f"{context}: voxel palette entry zero must be minecraft:air")
    seen_palette: set[str] = set()
    for entry in palette:
        if not isinstance(entry, str) or not entry or len(entry) > 4096:
            raise RecorderError(f"{context}: voxel palette contains an invalid entry")
        block_id = entry.split("[", 1)[0]
        if _RESOURCE_LOCATION.fullmatch(block_id) is None:
            raise RecorderError(f"{context}: voxel palette contains an invalid block resource")
        if "[" in entry and not entry.endswith("]"):
            raise RecorderError(f"{context}: voxel palette contains a malformed block state")
        if entry in seen_palette:
            raise RecorderError(f"{context}: voxel palette contains duplicate entries")
        seen_palette.add(entry)
    if (len(palette) <= 65_536) != (dtype == "uint16"):
        raise RecorderError(f"{context}: voxel index dtype does not match palette size")

    index_width = 2 if dtype == "uint16" else 4
    expected_index_bytes = total * index_width
    expected_index_encoded = 4 * ((expected_index_bytes + 2) // 3)
    indices = _decode_base64(
        snapshot.get("indices_base64"), expected_index_encoded, context, "indices_base64"
    )
    if len(indices) != expected_index_bytes:
        raise RecorderError(f"{context}: voxel index buffer length does not match shape and dtype")

    maximum_coverage_bytes = (total + 7) // 8
    maximum_coverage_encoded = 4 * ((maximum_coverage_bytes + 2) // 3)
    coverage = _decode_base64(
        snapshot.get("coverage_bitset_base64"),
        maximum_coverage_encoded,
        context,
        "coverage_bitset_base64",
    )
    if len(coverage) > maximum_coverage_bytes or (coverage and coverage[-1] == 0):
        raise RecorderError(f"{context}: voxel coverage bitset is not minimally encoded")
    if (
        len(coverage) == maximum_coverage_bytes
        and coverage
        and total % 8
        and coverage[-1] >> (total % 8)
    ):
        raise RecorderError(f"{context}: voxel coverage bitset has bits beyond the crop")
    if snapshot.get("coverage_bit_order") != "lsb0":
        raise RecorderError(f"{context}: unsupported voxel coverage bit order")
    actual_covered = sum(byte.bit_count() for byte in coverage)
    if actual_covered != covered:
        raise RecorderError(f"{context}: voxel coverage bitset count does not match metadata")

    palette_indices = array.array("H" if index_width == 2 else "I")
    palette_indices.frombytes(indices)
    if sys.byteorder != "little":
        palette_indices.byteswap()
    if palette_indices and max(palette_indices) >= len(palette):
        raise RecorderError(f"{context}: voxel index references outside the palette")

    if snapshot.get("block_entities_included") is not False:
        raise RecorderError(f"{context}: voxel v1 must explicitly exclude block entities")
    note = snapshot.get("block_entities_note")
    if not isinstance(note, str) or not note:
        raise RecorderError(f"{context}: voxel block entity limitation note is missing")
    return dimension


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
            canonical_output = _canonical_result_reference(
                result_data, result.resolve(), "output", "frame output"
            )
            if canonical_output is not None:
                index = canonical_output / "frames.jsonl"
            else:
                output = result_data.get("output")
                if not isinstance(output, str):
                    raise RecorderError(
                        f"renderer result does not identify its frame output: {result}"
                    )
                index = Path(output).expanduser().resolve() / "frames.jsonl"
        return result, index
    if not source.is_file() or source.is_symlink():
        raise RecorderError(f"frame attachment does not exist or is symlinked: {source}")
    if source.name == "result.json":
        result_data = _read_object(source, "renderer result")
        canonical_output = _canonical_result_reference(
            result_data, source.resolve(), "output", "frame output"
        )
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
    paths: Iterable[Path], expected_session: str
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
        replay_sha, replay_bytes, replay_path = _renderer_replay_integrity(result, result_path)
        session = result.get("session_id")
        player = result.get("player_uuid")
        connection = result.get("connection_id")
        if session != expected_session:
            raise RecorderError(
                f"renderer result session {session!r} does not match episode {expected_session!r}"
            )
        if not isinstance(player, str) or not isinstance(connection, str):
            raise RecorderError(f"renderer result lacks player_uuid or connection_id: {result_path}")
        first_tick = _required_int(result, "global_start_tick", result_path)
        last_tick = _required_int(result, "global_end_tick", result_path)
        if first_tick > last_tick:
            raise RecorderError(f"renderer result has an invalid global tick range: {result_path}")
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
        canonical_frames = _canonical_result_reference(
            result, result_path, "output", "frame output"
        )
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
            raise RecorderError(
                f"frame index {index_path} does not match renderer result output {expected_index}"
            )
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
                    raise RecorderError(
                        f"{index_path}:{line_number}: invalid frame JSON: {exc}"
                    ) from exc
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
                if (
                    not isinstance(frame_number, int)
                    or isinstance(frame_number, bool)
                    or frame_number != tick - first_tick + 1
                ):
                    raise RecorderError(f"{index_path}:{line_number}: frame number is inconsistent")
                replay_tick = row.get("replay_tick")
                if (
                    not isinstance(replay_tick, int)
                    or isinstance(replay_tick, bool)
                    or replay_tick != replay_start + tick - first_tick
                    or global_offset + replay_tick != tick
                ):
                    raise RecorderError(f"{index_path}:{line_number}: replay_tick is inconsistent")
                partial_tick = row.get("partial_tick")
                if (
                    not isinstance(partial_tick, (int, float))
                    or isinstance(partial_tick, bool)
                    or partial_tick != 0.0
                ):
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
                    reference=str(image),
                    row=row,
                    index_sha256=index_sha,
                    artifact_sha256=artifact_sha,
                    artifact_bytes=artifact_bytes,
                    width=width,
                    height=height,
                )
                existing = attachments.get(key)
                if existing is not None:
                    raise RecorderError(
                        f"multiple renderer attachments claim {session}/{player}/{connection}/tick-{tick}"
                    )
                attachments[key] = attached
                row_count += 1

        expected_ticks = set(range(first_tick, last_tick + 1))
        if ticks_seen != expected_ticks:
            missing = sorted(expected_ticks - ticks_seen)
            preview = ", ".join(str(tick) for tick in missing[:5])
            raise RecorderError(
                f"completed frame index is not contiguous for {first_tick}..{last_tick}; missing {preview}"
            )
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


def _resolve_voxel_artifacts(path: Path) -> tuple[Path | None, Path]:
    source = _attachment_input(path, "voxel attachment")
    if source.is_dir():
        index_candidates = (source / "voxels.jsonl", source / "frames" / "voxels.jsonl")
        index = next((candidate for candidate in index_candidates if candidate.is_file()), None)
        if index is None:
            raise RecorderError(f"render directory has no voxels.jsonl: {source}")
        result_candidates = (source / "result.json", source.parent / "result.json")
        result = next((candidate for candidate in result_candidates if candidate.is_file()), None)
        if result is None:
            raise RecorderError(f"render directory has no associated result.json: {source}")
        return result, index
    if not source.is_file() or source.is_symlink() or source.name != "voxels.jsonl":
        raise RecorderError(f"--voxels expects a render job directory or voxels.jsonl: {source}")
    result_candidates = (source.parent / "result.json", source.parent.parent / "result.json")
    result = next((candidate for candidate in result_candidates if candidate.is_file()), None)
    return result, source


def _voxel_vector(row: dict[str, Any], key: str, context: str) -> tuple[int, int, int]:
    value = row.get(key)
    if not isinstance(value, dict):
        raise RecorderError(f"{context}: voxel {key} must be an object")
    coordinates: list[int] = []
    for axis in ("x", "y", "z"):
        coordinate = value.get(axis)
        if not isinstance(coordinate, int) or isinstance(coordinate, bool):
            raise RecorderError(f"{context}: voxel {key}.{axis} must be an integer")
        coordinates.append(coordinate)
    return coordinates[0], coordinates[1], coordinates[2]


def _load_voxel_attachments(
    paths: Iterable[Path], expected_session: str
) -> tuple[dict[FrameKey, AttachedVoxel], list[dict[str, Any]]]:
    attachments: dict[FrameKey, AttachedVoxel] = {}
    sources: list[dict[str, Any]] = []
    seen_indexes: set[Path] = set()
    for supplied in paths:
        result_path, index_path = _resolve_voxel_artifacts(supplied)
        if index_path.is_symlink() or (result_path is not None and result_path.is_symlink()):
            raise RecorderError("renderer result and voxels.jsonl may not be symlinks")
        index_path = index_path.resolve()
        if index_path in seen_indexes:
            continue
        seen_indexes.add(index_path)

        result: dict[str, Any] | None = None
        result_sha: str | None = None
        result_identity: tuple[str, str, str] | None = None
        expected_ticks: set[int] | None = None
        expected_shape: tuple[int, int, int] | None = None
        replay_start: int | None = None
        global_offset: int | None = None
        expected_voxel_count: int | None = None
        replay_sha: str | None = None
        replay_bytes: int | None = None
        replay_path: str | None = None
        if result_path is not None:
            result_path = result_path.resolve()
            result_sha = sha256_file(result_path)
            result = _read_object(result_path, "renderer result")
            if result.get("status") != "complete":
                raise RecorderError(f"renderer result is not complete: {result_path}")
            replay_sha, replay_bytes, replay_path = _renderer_replay_integrity(
                result, result_path
            )
            session = result.get("session_id")
            player = result.get("player_uuid")
            connection = result.get("connection_id")
            if session != expected_session:
                raise RecorderError(
                    f"renderer result session {session!r} does not match episode {expected_session!r}"
                )
            if not isinstance(player, str) or not isinstance(connection, str):
                raise RecorderError(f"renderer result lacks player_uuid or connection_id: {result_path}")
            if _required_int(result, "fps", result_path) != TICK_RATE_HZ:
                raise RecorderError(f"renderer result must be exactly {TICK_RATE_HZ} FPS: {result_path}")
            first_tick = _required_int(result, "global_start_tick", result_path)
            last_tick = _required_int(result, "global_end_tick", result_path)
            if first_tick > last_tick:
                raise RecorderError(f"renderer result has an invalid global tick range: {result_path}")
            if last_tick - first_tick + 1 > MAX_ATTACHMENT_TICKS:
                raise RecorderError(f"renderer result exceeds the attachment tick limit: {result_path}")
            replay_start = _required_int(result, "replay_start_tick", result_path)
            replay_end = _required_int(result, "replay_end_tick", result_path)
            global_offset = _required_int(result, "global_tick_offset", result_path)
            if first_tick < 0 or replay_start < 0 or replay_end < replay_start:
                raise RecorderError(
                    f"renderer result contains a negative or reversed timeline: {result_path}"
                )
            if replay_end - replay_start != last_tick - first_tick:
                raise RecorderError(
                    f"renderer result replay/global ranges have different lengths: {result_path}"
                )
            if global_offset + replay_start != first_tick or global_offset + replay_end != last_tick:
                raise RecorderError(f"renderer result global tick offset is inconsistent: {result_path}")
            horizontal_radius = _required_int(result, "voxel_horizontal_radius", result_path)
            vertical_radius = _required_int(result, "voxel_vertical_radius", result_path)
            if not 1 <= horizontal_radius <= 64 or not 1 <= vertical_radius <= 64:
                raise RecorderError(f"renderer result voxel radii are outside v1 bounds: {result_path}")
            expected_shape = (
                horizontal_radius * 2 + 1,
                vertical_radius * 2 + 1,
                horizontal_radius * 2 + 1,
            )
            expected_voxel_count = _required_int(result, "voxel_snapshots", result_path)
            expected_ticks = set(range(first_tick, last_tick + 1))
            result_identity = (session, player, connection)
            result_index = result.get("voxel_index")
            canonical_index = _canonical_result_reference(
                result, result_path, "voxel_index", "voxel index"
            )
            if canonical_index is not None:
                resolved_result_index = canonical_index
            else:
                if not isinstance(result_index, str):
                    raise RecorderError(f"renderer result lacks voxel_index: {result_path}")
                resolved_result_index = Path(result_index).expanduser().resolve()
            if resolved_result_index != index_path:
                raise RecorderError(
                    f"voxel index {index_path} does not match renderer result {result_index}"
                )

        if not index_path.is_file() or index_path.is_symlink():
            raise RecorderError(f"voxel index does not exist or is symlinked: {index_path}")
        if index_path.stat().st_size > MAX_ATTACHMENT_INDEX_BYTES:
            raise RecorderError(f"voxel index exceeds the size limit: {index_path}")
        index_sha = sha256_file(index_path)
        ticks_by_identity: dict[tuple[str, str, str], set[int]] = {}
        offsets_by_identity: dict[tuple[str, str, str], set[int]] = {}
        row_count = 0
        try:
            handle = index_path.open("rb")
        except OSError as exc:
            raise RecorderError(f"cannot read voxel index: {index_path}") from exc
        with handle:
            for line_number, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                if len(line) > MAX_ATTACHMENT_INDEX_LINE_CHARS:
                    raise RecorderError(f"{index_path}:{line_number}: voxel row exceeds the size limit")
                context = f"{index_path}:{line_number}"
                try:
                    row = json.loads(line)
                except (ValueError, RecursionError) as exc:
                    raise RecorderError(f"{context}: invalid voxel JSON: {exc}") from exc
                if not isinstance(row, dict):
                    raise RecorderError(f"{context}: voxel row must be an object")
                session = row.get("session_id")
                player = row.get("player_uuid")
                connection = row.get("connection_id")
                tick = row.get("server_tick")
                if row.get("schema_version") != 1 or isinstance(row.get("schema_version"), bool):
                    raise RecorderError(f"{context}: unsupported voxel index schema_version")
                if session != expected_session:
                    raise RecorderError(f"{context}: session_id does not match episode")
                if not isinstance(player, str) or not isinstance(connection, str):
                    raise RecorderError(f"{context}: player_uuid or connection_id is missing")
                if not isinstance(tick, int) or isinstance(tick, bool):
                    raise RecorderError(f"{context}: server_tick must be an integer")
                replay_tick = row.get("replay_tick")
                if not isinstance(replay_tick, int) or isinstance(replay_tick, bool):
                    raise RecorderError(f"{context}: replay_tick must be an integer")
                identity = (session, player, connection)
                if result_identity is not None and identity != result_identity:
                    raise RecorderError(f"{context}: voxel identity does not match renderer result")
                ticks = ticks_by_identity.setdefault(identity, set())
                if tick in ticks:
                    raise RecorderError(f"{context}: duplicate server_tick {tick}")
                ticks.add(tick)
                offsets_by_identity.setdefault(identity, set()).add(tick - replay_tick)
                if replay_start is not None and global_offset is not None:
                    if replay_tick != replay_start + tick - first_tick:
                        raise RecorderError(f"{context}: replay_tick is inconsistent with renderer result")
                    if global_offset + replay_tick != tick:
                        raise RecorderError(f"{context}: global tick offset is inconsistent")

                _voxel_vector(row, "origin", context)
                shape = _voxel_vector(row, "shape", context)
                if any(size <= 0 for size in shape):
                    raise RecorderError(f"{context}: voxel shape dimensions must be positive")
                covered = row.get("covered_cells")
                total = row.get("total_cells")
                complete = row.get("coverage_complete")
                if (
                    not isinstance(covered, int)
                    or isinstance(covered, bool)
                    or not isinstance(total, int)
                    or isinstance(total, bool)
                    or covered < 0
                    or total <= 0
                    or covered > total
                ):
                    raise RecorderError(f"{context}: voxel coverage counts are invalid")
                if total != shape[0] * shape[1] * shape[2]:
                    raise RecorderError(f"{context}: total_cells does not match voxel shape")
                if not isinstance(complete, bool) or complete != (covered == total):
                    raise RecorderError(f"{context}: coverage_complete disagrees with coverage counts")

                relative = row.get("reference")
                if not isinstance(relative, str) or not relative:
                    raise RecorderError(f"{context}: voxel reference is missing")
                artifact = _artifact_inside(index_path.parent, relative, context, "voxel")
                artifact_bytes = artifact.stat().st_size
                artifact_sha = sha256_file(artifact)
                dimension = _validate_voxel_artifact(artifact, row, expected_shape, context)
                if artifact.stat().st_size != artifact_bytes or sha256_file(artifact) != artifact_sha:
                    raise RecorderError(f"{context}: voxel artifact changed during validation")

                key = (session, player, connection, tick)
                attached = AttachedVoxel(
                    reference=str(artifact),
                    row=row,
                    index_sha256=index_sha,
                    artifact_sha256=artifact_sha,
                    artifact_bytes=artifact_bytes,
                    dimension=dimension,
                )
                existing = attachments.get(key)
                if existing is not None:
                    raise RecorderError(
                        f"multiple voxel attachments claim {session}/{player}/{connection}/tick-{tick}"
                    )
                attachments[key] = attached
                row_count += 1

        if not ticks_by_identity:
            raise RecorderError(f"voxel index is empty: {index_path}")
        if result_identity is not None:
            actual_ticks = ticks_by_identity.get(result_identity, set())
            assert expected_ticks is not None
            if actual_ticks != expected_ticks:
                missing = sorted(expected_ticks - actual_ticks)
                preview = ", ".join(str(tick) for tick in missing[:5])
                raise RecorderError(
                    f"completed voxel index is not contiguous; missing global ticks {preview}"
                )
            if expected_voxel_count != row_count:
                raise RecorderError(
                    f"voxel index row count {row_count} does not match renderer result "
                    f"{expected_voxel_count}"
                )
        else:
            if len(ticks_by_identity) != 1:
                raise RecorderError(
                    f"standalone voxel index must contain exactly one player connection: {index_path}"
                )
            actual_ticks = next(iter(ticks_by_identity.values()))
            if max(actual_ticks) - min(actual_ticks) + 1 > MAX_ATTACHMENT_TICKS:
                raise RecorderError(f"standalone voxel index exceeds the tick limit: {index_path}")
            contiguous = set(range(min(actual_ticks), max(actual_ticks) + 1))
            if actual_ticks != contiguous:
                raise RecorderError(f"standalone voxel index has a non-contiguous global timeline: {index_path}")
            identity = next(iter(ticks_by_identity))
            if len(offsets_by_identity[identity]) != 1:
                raise RecorderError(f"standalone voxel index has an inconsistent replay timeline: {index_path}")

        if sha256_file(index_path) != index_sha:
            raise RecorderError(f"voxel index changed while attaching: {index_path}")
        if result_path is not None and result_sha is not None:
            if sha256_file(result_path) != result_sha:
                raise RecorderError(f"renderer result changed while attaching voxels: {result_path}")

        identity = result_identity or next(iter(ticks_by_identity))
        actual_ticks = ticks_by_identity[identity]
        source: dict[str, Any] = {
            "voxels_index": str(index_path),
            "voxels_index_sha256": index_sha,
            "session_id": identity[0],
            "player_uuid": identity[1],
            "connection_id": identity[2],
            "global_start_tick": min(actual_ticks),
            "global_end_tick": max(actual_ticks),
            "voxel_count": row_count,
        }
        if result_path is not None:
            source["result"] = str(result_path)
            source["result_sha256"] = result_sha
            source["replay"] = replay_path
            source["replay_sha256"] = replay_sha
            source["replay_bytes"] = replay_bytes
            if result is not None and result.get("result_type") == CANONICAL_RENDER_RESULT_TYPE:
                source["portable_request"] = result.get("portable_request")
                source["source_replay"] = result.get("source_replay")
                source["range_policy"] = result.get("range_policy")
                source["newer_cutoff"] = result.get("newer_cutoff")
        sources.append(source)
    return attachments, sources


def _assert_attachments_unchanged(
    frames: dict[FrameKey, AttachedFrame], voxels: dict[FrameKey, AttachedVoxel]
) -> None:
    expected: dict[Path, tuple[int, str, str]] = {}
    for attached in frames.values():
        expected[Path(attached.reference)] = (
            attached.artifact_bytes,
            attached.artifact_sha256,
            "frame",
        )
    for attached in voxels.values():
        expected[Path(attached.reference)] = (
            attached.artifact_bytes,
            attached.artifact_sha256,
            "voxel",
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
    voxels: Iterable[Path] = (),
    force: bool = False,
) -> ExportResult:
    if first_tick is not None and last_tick is not None and first_tick > last_tick:
        raise RecorderError("--from-tick cannot be greater than --to-tick")
    validation = validate_episode(episode)
    if not validation.valid:
        raise RecorderError("episode validation failed; run 'mc-recorder episodes validate' for details")
    if validation.sealed_epochs == 0:
        raise RecorderError("episode has no sealed epochs to export")

    verified_epochs = _verified_sealed_epochs(episode, validation.session_id)
    if not verified_epochs:
        raise RecorderError("episode has no explicitly sealed, hash-verified epochs to export")
    if len(verified_epochs) != validation.sealed_epochs:
        raise RecorderError("sealed epoch set changed during validation; retry the export")
    epoch_hashes = {epoch.info.index: epoch for epoch in verified_epochs}
    frame_attachments, frame_sources = _load_frame_attachments(frames, validation.session_id)
    voxel_attachments, voxel_sources = _load_voxel_attachments(voxels, validation.session_id)
    selected_players = _normalize_players(players)
    selected_connections = _normalize_connections(connections)
    requested_output = output.expanduser()
    if requested_output.is_symlink():
        raise RecorderError(f"refusing symlinked export output: {requested_output}")
    output = requested_output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}.tmp-", dir=output.parent))
    sample_count = state_count = action_count = modality_count = rgb_count = voxel_count = 0
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
        observed_states: dict[FrameKey, dict[str, Any]] = {}

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
                                    voxel_attachments,
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
                            raise RecorderError(
                                f"duplicate player_state for {key[0]}/{key[1]} at server tick {tick}"
                            )
                        current.states[key] = record
                        state_key = _frame_key(record)
                        if state_key is not None:
                            observed_state_keys.add(state_key)
                            observed_states[state_key] = record
                        if _selected(
                            record,
                            selected_players,
                            selected_connections,
                            first_tick,
                            last_tick,
                        ):
                            modality = _modality_entry(
                                record, frame_attachments, voxel_attachments, epoch_hashes
                            )
                            states_file.write(_json_line(_state_row(record, epoch_hashes)))
                            modalities_file.write(_json_line(modality))
                            state_count += 1
                            modality_count += 1
                            if modality["rgb"]["available"]:
                                rgb_count += 1
                            if modality["voxels"]["available"]:
                                voxel_count += 1
                    elif record_type == "control_state" and key is not None:
                        if key in current.controls:
                            raise RecorderError(
                                f"duplicate control_state for {key[0]}/{key[1]} at server tick {tick}"
                            )
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
                        current.barrier = (
                            barrier
                            if isinstance(barrier, int) and not isinstance(barrier, bool)
                            else None
                        )
                        current.tick_end_seen = True

            if current is not None:
                finish_bundle(current)

        unmatched_frames = set(frame_attachments) - observed_state_keys
        unmatched_voxels = set(voxel_attachments) - observed_state_keys
        if unmatched_frames:
            raise RecorderError(
                "frame attachment contains a key with no matching episode player_state: "
                f"{next(iter(sorted(unmatched_frames)))}"
            )
        if unmatched_voxels:
            raise RecorderError(
                "voxel attachment contains a key with no matching episode player_state: "
                f"{next(iter(sorted(unmatched_voxels)))}"
            )
        for key, attached in voxel_attachments.items():
            state_dimension = observed_states[key].get("dimension")
            if state_dimension != attached.dimension:
                raise RecorderError(
                    "voxel attachment dimension does not match episode player_state for "
                    f"{key}: {attached.dimension!r} != {state_dimension!r}"
                )

        _assert_attachments_unchanged(frame_attachments, voxel_attachments)
        _assert_sources_unchanged(verified_epochs)
        if sha256_file(session_manifest) != session_manifest_sha:
            raise RecorderError(f"session manifest changed during export: {session_manifest}")

        files: dict[str, dict[str, Any]] = {}
        for path in (samples_path, states_path, actions_path, modalities_path):
            files[path.name] = {
                "sha256": sha256_file(path),
                "size_bytes": path.stat().st_size,
            }
        manifest = {
            "schema_version": EXPORT_SCHEMA_VERSION,
            "owner": OWNER,
            "format": EXPORT_FORMAT,
            "created_at": datetime.now(UTC).isoformat(),
            "session_id": validation.session_id,
            "source_manifest_sha256": session_manifest_sha,
            "source": {
                "episode": str(episode.resolve()),
                "manifest_sha256": session_manifest_sha,
                "sealed_epochs": len(verified_epochs),
                "active_epochs_skipped": validation.active_epochs,
                "epochs": [epoch.manifest_entry() for epoch in verified_epochs],
            },
            "selection": {
                "players": sorted(selected_players),
                "connections": sorted(selected_connections),
                "from_tick": first_tick,
                "to_tick": last_tick,
                "frame_attachments": frame_sources,
                "voxel_attachments": voxel_sources,
            },
            "timeline": {
                "tick_rate_hz": TICK_RATE_HZ,
                "sample_rate_hz": TICK_RATE_HZ,
                "transition": (
                    "post_state[t] + reconstructed_control[t+1] + ordered packets with "
                    "barrier[t] < apply_sequence <= barrier[t+1] -> post_state[t+1]"
                ),
                "latency_compensation": False,
            },
            "modalities": {
                "samples": {"available": True, "records": sample_count, "file": "samples.jsonl"},
                "state": {"available": True, "records": state_count, "file": "states.jsonl"},
                "actions": {"available": True, "records": action_count, "file": "actions.jsonl"},
                "voxels": {
                    "availability": "per-sample",
                    "records_attached": voxel_count,
                    "index": "modalities.jsonl",
                    "note": (
                        "references include an embedded coverage bitset and are joined only by exact "
                        "session/player/connection/global-server-tick identity"
                    ),
                },
                "rgb": {
                    "availability": "per-sample",
                    "records_attached": rgb_count,
                    "index": "modalities.jsonl",
                    "note": (
                        "absolute frame references are joined only by exact "
                        "session/player/connection/global-server-tick identity"
                    ),
                },
            },
            "files": files,
        }
        manifest_path = staging / "manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        for path in (samples_path, states_path, actions_path, modalities_path, manifest_path):
            with path.open("rb") as handle:
                os.fsync(handle.fileno())
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
        voxel_count=voxel_count,
    )
