from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from minerec.errors import RecorderError
from minerec.processing.capture import load_capture_metadata, scan_capture_events

ACTION_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class ActionExtractionResult:
    output: Path
    record_count: int
    first_tick: int
    last_tick: int


def _payload(record: dict[str, Any]) -> dict[str, Any]:
    value = record.get("packet")
    if isinstance(value, dict):
        return value
    envelope = {
        "schema_version",
        "record_type",
        "session_id",
        "server_tick",
        "sequence",
        "recorded_at_ns",
        "recorded_at_unix_ms",
        "player_uuid",
        "player_name",
        "entity_id",
        "connection_id",
        "connection_start_server_tick",
        "apply_sequence",
        "arrival_sequence",
    }
    return {key: value for key, value in record.items() if key not in envelope}


def _action_type(record: dict[str, Any], payload: dict[str, Any]) -> str:
    if record.get("record_type") == "control_state":
        return "control_state"
    for key in ("action_type", "action_kind", "packet_type", "packet_class", "type"):
        value = record.get(key)
        if isinstance(value, str) and value:
            return value
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return "unknown_serverbound_packet"


def _action_row(record: dict[str, Any]) -> dict[str, Any]:
    payload = _payload(record)
    return {
        "schema_version": ACTION_SCHEMA_VERSION,
        "server_tick": record["server_tick"],
        "sequence": record["sequence"],
        "apply_sequence": record.get("apply_sequence"),
        "player_uuid": record["player_uuid"],
        "connection_id": record["connection_id"],
        "action_type": _action_type(record, payload),
        "payload": payload,
        "source_record_type": record["record_type"],
    }


def extract_actions(
    metadata: Path,
    events: Path,
    output: Path,
    *,
    first_tick: int | None = None,
    last_tick: int | None = None,
    force: bool = False,
) -> ActionExtractionResult:
    """Reconstruct one connection action stream from recorder events into one file."""

    capture = load_capture_metadata(metadata)
    if first_tick is not None and last_tick is not None and first_tick > last_tick:
        raise RecorderError("first tick cannot be greater than last tick")
    requested = output.expanduser()
    if requested.is_symlink():
        raise RecorderError(f"action output may not be a symlink: {requested}")
    destination = requested.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() or destination.is_symlink():
        if not force:
            raise RecorderError(f"action output exists: {destination}; pass --force to replace it")
        if destination.is_symlink() or not destination.is_file():
            raise RecorderError(f"action output is not a replaceable regular file: {destination}")

    descriptor, staging_name = tempfile.mkstemp(prefix=f".{destination.name}.", suffix=".inprogress", dir=destination.parent)
    os.close(descriptor)
    staging = Path(staging_name)
    count = 0
    observed_ticks: list[int] = []
    try:
        with staging.open("wb") as handle:

            def visit(record: dict[str, Any]) -> None:
                nonlocal count
                if record.get("record_type") not in {"control_state", "packet_apply"}:
                    return
                tick = record["server_tick"]
                if (first_tick is not None and tick < first_tick) or (last_tick is not None and tick > last_tick):
                    return
                handle.write(
                    json.dumps(
                        _action_row(record),
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=False,
                        allow_nan=False,
                    ).encode("utf-8")
                    + b"\n"
                )
                count += 1
                observed_ticks.append(tick)

            scan_capture_events(events, capture, visit)
            handle.flush()
            os.fsync(handle.fileno())
        if not observed_ticks:
            raise RecorderError("no reconstructed actions matched the requested connection and tick range")
        os.replace(staging, destination)
    except Exception:
        staging.unlink(missing_ok=True)
        raise
    return ActionExtractionResult(destination, count, min(observed_ticks), max(observed_ticks))


__all__ = ["ACTION_SCHEMA_VERSION", "ActionExtractionResult", "extract_actions"]
