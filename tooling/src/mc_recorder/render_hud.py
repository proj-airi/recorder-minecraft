from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, BinaryIO, Mapping, Protocol, cast

from .errors import RecorderError

if TYPE_CHECKING:
    from .dataset_viewer import DatasetViewer


HUD_SIDECAR_TYPE = "mc-recorder-structured-hud-v1"
HUD_SIDECAR_FORMAT = "jsonl"
MAX_HUD_SIDECAR_BYTES = 512 * 1024 * 1024
MAX_HUD_SIDECAR_LINE_BYTES = 1024 * 1024
MAX_STACK_SNBT_BYTES = 256 * 1024
_SHA256_LENGTH = 64
_ITEM_RE = re.compile(r"^[a-z0-9_.-]+:[a-z0-9_./-]+$")


class _Digest(Protocol):
    def update(self, data: bytes, /) -> object: ...


def _dataset_error(message: str) -> RecorderError:
    # Imported lazily because DatasetViewer also uses the result-envelope
    # validator when classifying attached RGB provenance.
    from .dataset_viewer import DatasetValidationError

    return DatasetValidationError(message)


@dataclass(frozen=True)
class StructuredHudSidecar:
    path: Path
    sha256: str
    size_bytes: int
    record_count: int
    first_tick: int
    last_tick: int
    dataset_id: str
    dataset_manifest_sha256: str
    samples_sha256: str
    session_id: str
    player_uuid: str
    connection_id: str

    def envelope(self) -> dict[str, Any]:
        """Return the path-free integrity envelope carried by portable requests."""

        return {
            "schema_version": 1,
            "sidecar_type": HUD_SIDECAR_TYPE,
            "format": HUD_SIDECAR_FORMAT,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "record_count": self.record_count,
            "first_tick": self.first_tick,
            "last_tick": self.last_tick,
            "dataset_id": self.dataset_id,
            "dataset_manifest_sha256": self.dataset_manifest_sha256,
            "samples_sha256": self.samples_sha256,
            "session_id": self.session_id,
            "player_uuid": self.player_uuid,
            "connection_id": self.connection_id,
        }


def _canonical_uuid(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise RecorderError(f"{label} must be a UUID")
    try:
        canonical = str(uuid.UUID(value))
    except (ValueError, TypeError, AttributeError) as exc:
        raise RecorderError(f"{label} must be a UUID") from exc
    if value != canonical:
        raise RecorderError(f"{label} must use canonical UUID spelling")
    return canonical


def _integer(value: object, label: str, minimum: int, maximum: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum or value > maximum:
        raise RecorderError(f"{label} must be an integer in {minimum}..{maximum}")
    return value


def _number(
    value: object,
    label: str,
    minimum: float,
    maximum: float,
) -> int | float:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value < minimum or value > maximum:
        raise RecorderError(f"{label} must be a finite number in {minimum}..{maximum}")
    return value


def _string(value: object, label: str, maximum_bytes: int) -> str:
    if not isinstance(value, str) or not value or len(value.encode("utf-8")) > maximum_bytes or any(ord(character) < 0x20 for character in value):
        raise RecorderError(f"{label} must be a bounded non-empty string")
    return value


def _inventory(value: object, tick: int) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise RecorderError(f"dataset HUD state at tick {tick} has no inventory list")
    rows: list[dict[str, Any]] = []
    seen: set[int] = set()
    for ordinal, raw in enumerate(value):
        if not isinstance(raw, dict):
            raise RecorderError(f"dataset HUD inventory entry {ordinal} at tick {tick} is not an object")
        slot = _integer(raw.get("slot"), f"inventory slot at tick {tick}", 0, 42)
        if slot in seen:
            raise RecorderError(f"dataset HUD inventory repeats slot {slot} at tick {tick}")
        seen.add(slot)
        stack_snbt = _string(
            raw.get("stack_snbt"),
            f"inventory stack_snbt for slot {slot} at tick {tick}",
            MAX_STACK_SNBT_BYTES,
        )
        item = _string(raw.get("item"), f"inventory item for slot {slot} at tick {tick}", 512)
        if _ITEM_RE.fullmatch(item) is None:
            raise RecorderError(f"inventory item for slot {slot} at tick {tick} is invalid")
        count = _integer(raw.get("count"), f"inventory count for slot {slot} at tick {tick}", 1, 999)
        damage = _integer(raw.get("damage"), f"inventory damage for slot {slot} at tick {tick}", 0, 2**31 - 1)
        max_damage = _integer(
            raw.get("max_damage"),
            f"inventory max_damage for slot {slot} at tick {tick}",
            0,
            2**31 - 1,
        )
        rows.append(
            {
                "slot": slot,
                "stack_snbt": stack_snbt,
                "item": item,
                "count": count,
                "damage": damage,
                "max_damage": max_damage,
            }
        )
    return sorted(rows, key=lambda row: row["slot"])


def _hud_row(
    state: Mapping[str, Any],
    *,
    tick: int,
    session_id: str,
    player_uuid: str,
    connection_id: str,
) -> dict[str, Any]:
    if state.get("session_id") != session_id:
        raise RecorderError(f"dataset state at tick {tick} has the wrong session")
    if state.get("server_tick") != tick:
        raise RecorderError(f"dataset state tick is inconsistent at tick {tick}")
    if state.get("player_uuid") != player_uuid or state.get("connection_id") != connection_id:
        raise RecorderError(f"dataset state identity is inconsistent at tick {tick}")
    hud = {
        "health": _number(state.get("health"), f"health at tick {tick}", 0.0, 2048.0),
        "max_health": _number(state.get("max_health"), f"max_health at tick {tick}", 0.01, 2048.0),
        "absorption": _number(state.get("absorption"), f"absorption at tick {tick}", 0.0, 2048.0),
        "air": _integer(state.get("air"), f"air at tick {tick}", -1_000_000, 1_000_000),
        "max_air": _integer(state.get("max_air"), f"max_air at tick {tick}", 1, 1_000_000),
        "food_level": _integer(state.get("food_level"), f"food_level at tick {tick}", 0, 20),
        "saturation": _number(state.get("saturation"), f"saturation at tick {tick}", 0.0, 20.0),
        "experience_progress": _number(
            state.get("experience_progress"),
            f"experience_progress at tick {tick}",
            0.0,
            1.0,
        ),
        "experience_level": _integer(
            state.get("experience_level"),
            f"experience_level at tick {tick}",
            0,
            2**31 - 1,
        ),
        "total_experience": _integer(
            state.get("total_experience"),
            f"total_experience at tick {tick}",
            0,
            2**31 - 1,
        ),
        "selected_slot": _integer(state.get("selected_slot"), f"selected_slot at tick {tick}", 0, 8),
        "inventory": _inventory(state.get("inventory"), tick),
    }
    return {
        "schema_version": 1,
        "sidecar_type": HUD_SIDECAR_TYPE,
        "session_id": session_id,
        "server_tick": tick,
        "player_uuid": player_uuid,
        "connection_id": connection_id,
        "state": hud,
    }


def _write_line(handle: BinaryIO, digest: _Digest, row: Mapping[str, Any], total: int) -> int:
    try:
        encoded = (
            json.dumps(
                row,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError, RecursionError) as exc:
        raise RecorderError("dataset HUD state cannot be encoded as finite JSON") from exc
    if len(encoded) > MAX_HUD_SIDECAR_LINE_BYTES:
        raise RecorderError("structured HUD sidecar row exceeds the size limit")
    total += len(encoded)
    if total > MAX_HUD_SIDECAR_BYTES:
        raise RecorderError("structured HUD sidecar exceeds the size limit")
    handle.write(encoded)
    digest.update(encoded)
    return total


def create_structured_hud_sidecar(
    viewer: DatasetViewer,
    dataset_id: str,
    output: Path,
    *,
    session_id: str,
    player_uuid: str,
    connection_id: str,
    first_tick: int,
    last_tick: int,
    selection_first_tick: int,
    selection_last_tick: int,
) -> StructuredHudSidecar:
    """Create an immutable, dataset-bound HUD timeline for one render interval."""

    player_uuid = _canonical_uuid(player_uuid, "HUD player UUID")
    connection_id = _canonical_uuid(connection_id, "HUD connection UUID")
    first_tick = _integer(first_tick, "HUD first tick", 0, 2**63 - 1)
    last_tick = _integer(last_tick, "HUD last tick", first_tick, 2**63 - 1)
    selection_first_tick = _integer(selection_first_tick, "HUD selection first tick", 0, first_tick)
    selection_last_tick = _integer(selection_last_tick, "HUD selection last tick", last_tick, 2**63 - 1)
    dataset = viewer._dataset_by_id(dataset_id)
    manifest = dataset.manifest
    if manifest.get("session_id") != session_id:
        raise RecorderError("render dataset session does not match its queue job")
    selection = manifest.get("selection")
    if not isinstance(selection, dict):
        raise RecorderError("render dataset has no selection contract")
    players = selection.get("players")
    connections = selection.get("connections")
    if not isinstance(players, list) or player_uuid not in players:
        raise RecorderError("render dataset does not select the requested player")
    if not isinstance(connections, list) or connection_id not in connections:
        raise RecorderError("render dataset does not select the requested connection")
    if selection.get("from_tick") != selection_first_tick or selection.get("to_tick") != selection_last_tick:
        raise RecorderError("render dataset selection range does not match its queue job")

    verified_states = dataset.files["states.jsonl"]
    states_path = verified_states.path
    verified_samples = dataset.files["samples.jsonl"]
    requested = output.expanduser()
    if requested.exists() or requested.is_symlink():
        raise RecorderError(f"structured HUD sidecar output already exists: {requested}")
    target = requested.resolve()
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.tmp-", dir=target.parent)
    temporary = Path(temporary_name)
    output_digest = hashlib.sha256()
    total = 0
    count = 0
    expected_tick = first_tick
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as destination:
            try:
                before = states_path.stat()
                if (
                    states_path.is_symlink()
                    or before.st_dev != verified_states.device
                    or before.st_ino != verified_states.inode
                    or before.st_size != verified_states.size_bytes
                    or before.st_mtime_ns != verified_states.modified_ns
                    or before.st_ctime_ns != verified_states.changed_ns
                ):
                    raise _dataset_error("states.jsonl changed after dataset verification")
                source_digest = hashlib.sha256()
                with states_path.open("rb") as source:
                    for line_number, raw in enumerate(source, 1):
                        source_digest.update(raw)
                        if len(raw) > viewer.max_sample_line_bytes:
                            raise _dataset_error(f"states.jsonl line {line_number} exceeds the size limit")
                        try:
                            state = json.loads(raw)
                        except (ValueError, RecursionError) as exc:
                            raise _dataset_error(f"states.jsonl line {line_number} is invalid JSON") from exc
                        if not isinstance(state, dict):
                            raise _dataset_error(f"states.jsonl line {line_number} is not an object")
                        if state.get("player_uuid") != player_uuid or state.get("connection_id") != connection_id:
                            continue
                        tick = state.get("server_tick")
                        if not isinstance(tick, int) or isinstance(tick, bool):
                            raise RecorderError("selected dataset state has an invalid server tick")
                        if tick < first_tick or tick > last_tick:
                            continue
                        if tick != expected_tick:
                            detail = "duplicate" if tick < expected_tick else "missing"
                            raise RecorderError(f"structured HUD dataset has a {detail} tick at {expected_tick}")
                        total = _write_line(
                            destination,
                            output_digest,
                            _hud_row(
                                state,
                                tick=tick,
                                session_id=session_id,
                                player_uuid=player_uuid,
                                connection_id=connection_id,
                            ),
                            total,
                        )
                        count += 1
                        expected_tick += 1
                after = states_path.stat()
                if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                    after.st_dev,
                    after.st_ino,
                    after.st_size,
                    after.st_mtime_ns,
                    after.st_ctime_ns,
                ) or source_digest.hexdigest() != verified_states.sha256:
                    raise _dataset_error("states.jsonl changed while the HUD sidecar was generated")
            except OSError as exc:
                raise _dataset_error("cannot read verified states.jsonl") from exc
            if expected_tick != last_tick + 1:
                raise RecorderError(f"structured HUD dataset is missing tick {expected_tick}")
            destination.flush()
            os.fsync(destination.fileno())
        if count != last_tick - first_tick + 1 or total <= 0:
            raise RecorderError("structured HUD sidecar does not cover its exact tick range")
        try:
            os.link(temporary, target, follow_symlinks=False)
        except FileExistsError as exc:
            raise RecorderError(f"structured HUD sidecar was concurrently published: {target}") from exc
        parent_descriptor = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(parent_descriptor)
        finally:
            os.close(parent_descriptor)
    finally:
        temporary.unlink(missing_ok=True)

    return StructuredHudSidecar(
        path=target,
        sha256=output_digest.hexdigest(),
        size_bytes=total,
        record_count=count,
        first_tick=first_tick,
        last_tick=last_tick,
        dataset_id=dataset.dataset_id,
        dataset_manifest_sha256=dataset.manifest_sha256,
        # The wire contract retains the samples digest for compatibility. The
        # manifest digest binds the verified states stream used above.
        samples_sha256=verified_samples.sha256,
        session_id=session_id,
        player_uuid=player_uuid,
        connection_id=connection_id,
    )


def validate_hud_sidecar_envelope(value: object, label: str = "structured HUD sidecar") -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RecorderError(f"{label} must be an object")
    required = {
        "schema_version",
        "sidecar_type",
        "format",
        "sha256",
        "size_bytes",
        "record_count",
        "first_tick",
        "last_tick",
        "dataset_id",
        "dataset_manifest_sha256",
        "samples_sha256",
        "session_id",
        "player_uuid",
        "connection_id",
    }
    if set(value) != required:
        raise RecorderError(f"{label} has unsupported or missing fields")
    if value.get("schema_version") != 1 or value.get("sidecar_type") != HUD_SIDECAR_TYPE or value.get("format") != HUD_SIDECAR_FORMAT:
        raise RecorderError(f"{label} has an unsupported contract")
    for key in ("sha256", "dataset_manifest_sha256", "samples_sha256"):
        digest = value.get(key)
        if not isinstance(digest, str) or len(digest) != _SHA256_LENGTH or any(character not in "0123456789abcdef" for character in digest):
            raise RecorderError(f"{label} {key} must be lowercase SHA-256")
    dataset_id = value.get("dataset_id")
    if not isinstance(dataset_id, str) or len(dataset_id) != 32 or any(character not in "0123456789abcdef" for character in dataset_id):
        raise RecorderError(f"{label} dataset_id is invalid")
    _string(value.get("session_id"), f"{label} session_id", 512)
    _canonical_uuid(value.get("player_uuid"), f"{label} player UUID")
    _canonical_uuid(value.get("connection_id"), f"{label} connection UUID")
    first = _integer(value.get("first_tick"), f"{label} first_tick", 0, 2**63 - 1)
    last = _integer(value.get("last_tick"), f"{label} last_tick", first, 2**63 - 1)
    records = _integer(value.get("record_count"), f"{label} record_count", 1, 2**63 - 1)
    if records != last - first + 1:
        raise RecorderError(f"{label} record count does not match its exact tick range")
    _integer(
        value.get("size_bytes"),
        f"{label} size_bytes",
        1,
        MAX_HUD_SIDECAR_BYTES,
    )
    return cast(dict[str, Any], value)


def hud_result_envelope(value: Mapping[str, Any]) -> dict[str, Any]:
    """Derive the exact path-free result provenance from a request or local job."""

    if "record_count" in value:
        records = value.get("record_count")
        first = value.get("first_tick")
        last = value.get("last_tick")
        sidecar_type = value.get("sidecar_type")
    else:
        records = value.get("records")
        first = value.get("start_server_tick")
        last = value.get("end_server_tick")
        sidecar_type = value.get("type")
    return validate_hud_result_envelope(
        {
            "schema_version": value.get("schema_version"),
            "type": sidecar_type,
            "format": value.get("format"),
            "sha256": value.get("sha256"),
            "size_bytes": value.get("size_bytes"),
            "records": records,
            "start_server_tick": first,
            "end_server_tick": last,
            "dataset_id": value.get("dataset_id"),
            "dataset_manifest_sha256": value.get("dataset_manifest_sha256"),
            "samples_sha256": value.get("samples_sha256"),
            "session_id": value.get("session_id"),
            "player_uuid": value.get("player_uuid"),
            "connection_id": value.get("connection_id"),
        }
    )


def validate_hud_result_envelope(value: object, label: str = "renderer structured_hud") -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {
        "schema_version",
        "type",
        "format",
        "sha256",
        "size_bytes",
        "records",
        "start_server_tick",
        "end_server_tick",
        "dataset_id",
        "dataset_manifest_sha256",
        "samples_sha256",
        "session_id",
        "player_uuid",
        "connection_id",
    }:
        raise RecorderError(f"{label} has unsupported or missing fields")
    portable = validate_hud_sidecar_envelope(
        {
            "schema_version": value.get("schema_version"),
            "sidecar_type": value.get("type"),
            "format": value.get("format"),
            "sha256": value.get("sha256"),
            "size_bytes": value.get("size_bytes"),
            "record_count": value.get("records"),
            "first_tick": value.get("start_server_tick"),
            "last_tick": value.get("end_server_tick"),
            "dataset_id": value.get("dataset_id"),
            "dataset_manifest_sha256": value.get("dataset_manifest_sha256"),
            "samples_sha256": value.get("samples_sha256"),
            "session_id": value.get("session_id"),
            "player_uuid": value.get("player_uuid"),
            "connection_id": value.get("connection_id"),
        },
        label,
    )
    return {
        "schema_version": portable["schema_version"],
        "type": HUD_SIDECAR_TYPE,
        "format": portable["format"],
        "sha256": portable["sha256"],
        "size_bytes": portable["size_bytes"],
        "records": portable["record_count"],
        "start_server_tick": portable["first_tick"],
        "end_server_tick": portable["last_tick"],
        "dataset_id": portable["dataset_id"],
        "dataset_manifest_sha256": portable["dataset_manifest_sha256"],
        "samples_sha256": portable["samples_sha256"],
        "session_id": portable["session_id"],
        "player_uuid": portable["player_uuid"],
        "connection_id": portable["connection_id"],
    }


__all__ = [
    "HUD_SIDECAR_FORMAT",
    "HUD_SIDECAR_TYPE",
    "MAX_HUD_SIDECAR_BYTES",
    "MAX_HUD_SIDECAR_LINE_BYTES",
    "MAX_STACK_SNBT_BYTES",
    "StructuredHudSidecar",
    "create_structured_hud_sidecar",
    "hud_result_envelope",
    "validate_hud_sidecar_envelope",
    "validate_hud_result_envelope",
]
