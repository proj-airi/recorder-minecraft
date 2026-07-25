"""Scene Store V2 writer, validator, and read-only SQLite adapter."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
import tempfile
import uuid
import zlib
from collections import OrderedDict
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType, TracebackType
from typing import Any, Callable, Iterable, Iterator, Mapping, Sequence, cast
from urllib.parse import quote

from sqlalchemy import and_, bindparam, create_engine, func, insert, or_, select
from sqlalchemy.dialects import sqlite as sqlite_dialect
from sqlalchemy.engine import URL, Connection
from sqlalchemy.sql.elements import ClauseElement

from minerec.processing.scene.schema_v2 import (
    FRAME_ALIGNMENT_SELECT,
    SCENE_STORE_V2_SCHEMA,
    SCENE_STORE_V2_SCHEMA_VERSION,
    SCENE_STORE_V2_USER_VERSION,
    blobs,
    block_entity_versions,
    entity_versions,
    frames,
    player_states,
    scene_meta,
    schema_info,
    section_versions,
)
from minerec.processing.scene.schema_v2 import (
    metadata as schema_metadata,
)
from minerec.processing.scene.store import (
    DEFAULT_MAX_CROP_CELLS,
    MAX_BLOB_UNCOMPRESSED_BYTES,
    MAX_CROP_BLOCK_ENTITIES,
    MAX_CROP_ENTITIES,
    MAX_CROP_OBJECT_PAYLOAD_BYTES,
    MAX_CROP_OBJECT_PAYLOAD_TOTAL_BYTES,
    MAX_SLICE_RADIUS,
    SECTION_EDGE,
    SQLITE_INTEGER_MAX,
    SQLITE_INTEGER_MIN,
    UNKNOWN_PALETTE_INDEX,
    SceneBlockEntity,
    SceneCrop,
    SceneEntity,
    SceneFrame,
    SceneIdentity,
    SceneSlice,
    SceneSliceCell,
    SceneStoreError,
    SceneStoreValidationError,
    _block_entity_type,
    _decode_section_blob,
    _entity_fields,
    _validated_extraction_provenance,
    canonical_json_blob,
    validate_scene_store,
)

MAX_STATE_JSONL_LINE_BYTES = 64 * 1024 * 1024
MAX_STATE_READ_ROWS = 100_000
MAX_PRIVATE_TEXT_BYTES = 8 * 1024 * 1024
MAX_VALIDATION_BLOB_CACHE_BYTES = 64 * 1024 * 1024
MAX_VALIDATION_BLOB_CACHE_ENTRIES = 65_536

_ABILITY_FIELDS = frozenset({"invulnerable", "flying", "may_fly", "instant_build", "may_build"})
_EFFECT_FIELDS = frozenset({"effect", "duration", "amplifier", "ambient", "visible", "show_icon"})
_ENTITY_REFERENCE_FIELDS = frozenset({"entity_id", "uuid", "type"})
_INVENTORY_REQUIRED_FIELDS = frozenset({"slot", "item", "count", "damage", "max_damage"})
_INVENTORY_OPTIONAL_FIELDS = frozenset({"components_debug", "stack_snbt"})
_SOURCE_REPLAY_FIELDS = frozenset({"segment_id", "segment_ordinal", "path", "sha256", "size_bytes", "format"})
_RESOURCE_LOCATION_RE = re.compile(r"^[a-z0-9_.-]+:[a-z0-9_./-]+$")
_SAFE_SEGMENT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_WINDOWS_ABSOLUTE_PATH_RE = re.compile(r"^[A-Za-z]:[\\\\/]")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_REDACTED_HOST_PATH = "processor-runtime://host-path-removed"

_SQLITE_CORE_DIALECT = sqlite_dialect.dialect(paramstyle="named")


class SceneStoreV2Error(SceneStoreError):
    """A Scene Store V2 contract or persistence failure."""


class SceneStoreV2ValidationError(SceneStoreValidationError, SceneStoreV2Error):
    """A persisted Scene Store V2 failed validation."""


@dataclass(frozen=True)
class SceneStoreV2Info:
    identity: SceneIdentity
    start_tick: int
    end_tick: int
    frame_count: int
    ticks: tuple[int, ...]
    source_replays: tuple[Mapping[str, Any], ...]
    provenance: Mapping[str, Any]
    sensitive: bool
    sha256: str
    size_bytes: int


@dataclass(frozen=True)
class PlayerStateV2:
    tick: int
    entity_version_id: int
    entity_instance_id: str
    entity_id: int
    dimension: str
    position: tuple[float, float, float]
    velocity: tuple[float, float, float]
    rotation: tuple[float, float, float]
    alive: bool
    on_ground: bool
    pose: str
    sprinting: bool
    sneaking: bool
    swimming: bool
    fall_flying: bool
    using_item: bool
    use_item_remaining_ticks: int
    game_mode: str
    health: float
    max_health: float
    absorption: float
    armor: int
    air: int
    max_air: int
    food_level: int
    saturation: float
    experience_level: int
    experience_progress: float
    total_experience: int
    selected_slot: int
    state_barrier_apply_sequence: int
    payload: Mapping[str, Any]


@dataclass(frozen=True)
class SceneFrameAlignment:
    server_tick: int
    frame_id: str
    segment_id: str | None
    replay_tick: int | None


@dataclass(frozen=True)
class SceneTrajectoryPoint:
    tick: int
    dimension: str
    position: tuple[float, float, float]
    entity_instance_id: str


_SQLITE_RTREE_DDL = (
    """
    CREATE VIRTUAL TABLE entity_versions_rtree USING rtree(
        version_id,
        min_tick, max_tick,
        min_x, max_x,
        min_y, max_y,
        min_z, max_z
    )
    """,
    """
    CREATE TRIGGER entity_versions_rtree_insert AFTER INSERT ON entity_versions BEGIN
        INSERT INTO entity_versions_rtree VALUES (
            new.version_id,
            new.start_tick, new.end_tick,
            new.min_x, new.max_x,
            new.min_y, new.max_y,
            new.min_z, new.max_z
        );
    END
    """,
    """
    CREATE TRIGGER entity_versions_rtree_delete AFTER DELETE ON entity_versions BEGIN
        DELETE FROM entity_versions_rtree WHERE version_id = old.version_id;
    END
    """,
    """
    CREATE TRIGGER entity_versions_rtree_update
    AFTER UPDATE OF version_id, start_tick, end_tick, min_x, max_x, min_y, max_y, min_z, max_z
    ON entity_versions BEGIN
        DELETE FROM entity_versions_rtree WHERE version_id = old.version_id;
        INSERT INTO entity_versions_rtree VALUES (
            new.version_id,
            new.start_tick, new.end_tick,
            new.min_x, new.max_x,
            new.min_y, new.max_y,
            new.min_z, new.max_z
        );
    END
    """,
    """
    CREATE VIRTUAL TABLE block_entity_versions_rtree USING rtree(
        version_id,
        min_tick, max_tick,
        min_x, max_x,
        min_y, max_y,
        min_z, max_z
    )
    """,
    """
    CREATE TRIGGER block_entity_versions_rtree_insert
    AFTER INSERT ON block_entity_versions BEGIN
        INSERT INTO block_entity_versions_rtree VALUES (
            new.version_id,
            new.start_tick, new.end_tick,
            new.block_x, new.block_x + 1,
            new.block_y, new.block_y + 1,
            new.block_z, new.block_z + 1
        );
    END
    """,
    """
    CREATE TRIGGER block_entity_versions_rtree_delete
    AFTER DELETE ON block_entity_versions BEGIN
        DELETE FROM block_entity_versions_rtree WHERE version_id = old.version_id;
    END
    """,
    """
    CREATE TRIGGER block_entity_versions_rtree_update
    AFTER UPDATE OF version_id, start_tick, end_tick, block_x, block_y, block_z
    ON block_entity_versions BEGIN
        DELETE FROM block_entity_versions_rtree WHERE version_id = old.version_id;
        INSERT INTO block_entity_versions_rtree VALUES (
            new.version_id,
            new.start_tick, new.end_tick,
            new.block_x, new.block_x + 1,
            new.block_y, new.block_y + 1,
            new.block_z, new.block_z + 1
        );
    END
    """,
)


_ENTITY_BOX_SQL = """
SELECT source.*
FROM entity_versions_rtree AS search
CROSS JOIN entity_versions AS source
WHERE search.min_tick <= ? AND search.max_tick > ?
  AND search.max_x > ? AND search.min_x < ?
  AND search.max_y > ? AND search.min_y < ?
  AND search.max_z > ? AND search.min_z < ?
  AND source.version_id = search.version_id
  AND source.dimension = ?
  AND source.start_tick <= ? AND source.end_tick > ?
  AND source.max_x > ? AND source.min_x < ?
  AND source.max_y > ? AND source.min_y < ?
  AND source.max_z > ? AND source.min_z < ?
ORDER BY source.instance_id
"""


_BLOCK_ENTITY_BOX_SQL = """
SELECT source.*
FROM block_entity_versions_rtree AS search
CROSS JOIN block_entity_versions AS source
WHERE search.min_tick <= ? AND search.max_tick > ?
  AND search.max_x > ? AND search.min_x < ?
  AND search.max_y > ? AND search.min_y < ?
  AND search.max_z > ? AND search.min_z < ?
  AND source.version_id = search.version_id
  AND source.dimension = ?
  AND source.start_tick <= ? AND source.end_tick > ?
  AND source.block_x >= ? AND source.block_x < ?
  AND source.block_y >= ? AND source.block_y < ?
  AND source.block_z >= ? AND source.block_z < ?
ORDER BY source.block_y, source.block_z, source.block_x
"""


def create_sqlite_scene_schema(connection: Connection) -> None:
    """Create the vendor-neutral base schema plus SQLite-only R-tree adapters."""

    if connection.dialect.name != "sqlite":
        raise SceneStoreV2Error("the SQLite scene adapter requires a SQLite connection")
    connection.exec_driver_sql("PRAGMA foreign_keys = ON")
    schema_metadata.create_all(connection)
    for statement in _SQLITE_RTREE_DDL:
        connection.exec_driver_sql(statement)
    connection.exec_driver_sql(f"PRAGMA user_version = {SCENE_STORE_V2_USER_VERSION}")


def _canonical_json(value: Any, description: str) -> bytes:  # noqa: ANN401
    try:
        return canonical_json_blob(value)
    except SceneStoreError as exc:
        raise SceneStoreV2Error(f"{description} is not canonical JSON: {exc}") from exc


def _parse_canonical_json(data: bytes, description: str) -> Any:  # noqa: ANN401
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SceneStoreV2ValidationError(f"{description} is not valid UTF-8 JSON") from exc
    try:
        encoded = canonical_json_blob(value)
    except SceneStoreError as exc:
        raise SceneStoreV2ValidationError(f"{description} is not canonical JSON: {exc}") from exc
    if encoded != data:
        raise SceneStoreV2ValidationError(f"{description} is not canonically encoded")
    return value


def _freeze_json(value: Any) -> Any:  # noqa: ANN401
    if isinstance(value, dict):
        return MappingProxyType({str(key): _freeze_json(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    return value


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _path_identity(path: Path) -> tuple[int, int, int, int]:
    stat = path.stat()
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns


def _check_regular_file(path: Path, description: str) -> None:
    try:
        stat = path.lstat()
    except FileNotFoundError as exc:
        raise SceneStoreV2Error(f"{description} does not exist: {path}") from exc
    if path.is_symlink() or not path.is_file() or stat.st_nlink != 1:
        raise SceneStoreV2Error(f"{description} must be a regular non-linked file: {path}")


def _connect_read_only(path: Path) -> sqlite3.Connection:
    uri = f"file:{quote(str(path), safe='/')}?mode=ro&immutable=1"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _execute_core(
    connection: sqlite3.Connection,
    statement: ClauseElement,
    parameters: Mapping[str, Any] | None = None,
) -> sqlite3.Cursor:
    """Execute one portable Core statement through the native SQLite shell."""

    compiled = statement.compile(dialect=_SQLITE_CORE_DIALECT)
    values = compiled.construct_params(dict(parameters or {})) or {}
    return connection.execute(str(compiled), values)


def _decompress(data: bytes, expected_size: int, description: str) -> bytes:
    inflater = zlib.decompressobj()
    try:
        value = inflater.decompress(data, MAX_BLOB_UNCOMPRESSED_BYTES + 1)
        if len(value) > MAX_BLOB_UNCOMPRESSED_BYTES or inflater.unconsumed_tail:
            raise SceneStoreV2ValidationError(f"{description} exceeds the uncompressed size limit")
        value += inflater.flush()
    except zlib.error as exc:
        raise SceneStoreV2ValidationError(f"{description} is not valid zlib data") from exc
    if len(value) != expected_size or not inflater.eof or inflater.unused_data:
        raise SceneStoreV2ValidationError(f"{description} has an invalid zlib boundary or size")
    return value


def _validated_blob_row(row: Mapping[str, Any], digest: str, expected_kind: str) -> bytes:
    if row["kind"] != expected_kind or row["encoding"] != "zlib":
        raise SceneStoreV2ValidationError(f"blob {digest} has the wrong kind or encoding")
    data = bytes(row["data"])
    if len(data) != row["compressed_size"]:
        raise SceneStoreV2ValidationError(f"blob {digest} compressed size is invalid")
    value = _decompress(data, row["uncompressed_size"], f"{expected_kind} blob {digest}")
    if hashlib.sha256(value).hexdigest() != digest:
        raise SceneStoreV2ValidationError(f"blob {digest} content hash is invalid")
    if zlib.compress(value, level=9) != data:
        raise SceneStoreV2ValidationError(f"blob {digest} does not use the canonical zlib encoding")
    return value


def _blob_value(connection: sqlite3.Connection, digest: str, expected_kind: str) -> bytes:
    row = _execute_core(
        connection,
        select(
            blobs.c.kind,
            blobs.c.encoding,
            blobs.c.uncompressed_size,
            blobs.c.compressed_size,
            blobs.c.data,
        ).where(blobs.c.sha256 == bindparam("digest")),
        {"digest": digest},
    ).fetchone()
    if row is None:
        raise SceneStoreV2ValidationError(f"missing {expected_kind} blob {digest}")
    return _validated_blob_row(row, digest, expected_kind)


def _put_blob(connection: Connection, kind: str, value: bytes) -> str:
    if len(value) > MAX_BLOB_UNCOMPRESSED_BYTES:
        raise SceneStoreV2Error(f"{kind} blob exceeds the uncompressed size limit")
    digest = hashlib.sha256(value).hexdigest()
    existing = connection.execute(blobs.select().with_only_columns(blobs.c.kind, blobs.c.uncompressed_size).where(blobs.c.sha256 == digest)).first()
    if existing is not None:
        if existing.kind != kind or existing.uncompressed_size != len(value):
            raise SceneStoreV2Error(f"scene blob hash collision for {digest}")
        return digest
    compressed = zlib.compress(value, level=9)
    connection.execute(
        insert(blobs).values(
            sha256=digest,
            kind=kind,
            encoding="zlib",
            uncompressed_size=len(value),
            compressed_size=len(compressed),
            data=compressed,
        )
    )
    return digest


def _required_text(value: object, description: str) -> str:
    if not isinstance(value, str) or not value:
        raise SceneStoreV2Error(f"{description} must be non-empty text")
    return value


def _required_int(value: object, description: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise SceneStoreV2Error(f"{description} must be an integer")
    return value


def _required_bool(value: object, description: str) -> bool:
    if not isinstance(value, bool):
        raise SceneStoreV2Error(f"{description} must be boolean")
    return value


def _bounded_int(
    value: object,
    description: str,
    minimum: int,
    maximum: int,
) -> int:
    result = _required_int(value, description)
    if not minimum <= result <= maximum:
        raise SceneStoreV2Error(f"{description} must be an integer from {minimum} to {maximum}")
    return result


def _bounded_text(value: object, description: str, maximum_bytes: int) -> str:
    result = _required_text(value, description)
    if len(result.encode("utf-8")) > maximum_bytes or any(ord(character) < 0x20 for character in result):
        raise SceneStoreV2Error(f"{description} must be bounded printable text")
    return result


def _resource_location(value: object, description: str) -> str:
    result = _bounded_text(value, description, 512)
    if _RESOURCE_LOCATION_RE.fullmatch(result) is None:
        raise SceneStoreV2Error(f"{description} must be a resource location")
    return result


def _canonical_uuid(value: object, description: str) -> str:
    result = _bounded_text(value, description, 36)
    try:
        canonical = str(uuid.UUID(result))
    except ValueError as exc:
        raise SceneStoreV2Error(f"{description} must be a canonical UUID") from exc
    if result != canonical:
        raise SceneStoreV2Error(f"{description} must use canonical UUID spelling")
    return result


def _validate_entity_reference(value: object, description: str) -> tuple[int, str]:
    if not isinstance(value, Mapping) or set(value) != _ENTITY_REFERENCE_FIELDS:
        raise SceneStoreV2Error(f"{description} must contain exactly entity_id, uuid, and type")
    entity_id = _bounded_int(
        value.get("entity_id"),
        f"{description} entity_id",
        0,
        2**31 - 1,
    )
    entity_uuid = _canonical_uuid(value.get("uuid"), f"{description} uuid")
    _resource_location(value.get("type"), f"{description} type")
    return entity_id, entity_uuid


def _validate_private_state_payload(record: Mapping[str, Any]) -> None:
    """Validate the complete private fields emitted by PlayerSnapshot.

    The canonical JSON blob remains byte-for-byte source data.  This helper
    validates its semantic shape without projecting it into a second model.
    """

    abilities = record.get("abilities")
    if not isinstance(abilities, Mapping) or set(abilities) != _ABILITY_FIELDS:
        raise SceneStoreV2Error("player_state abilities must contain the complete v1 ability fields")
    for name in sorted(_ABILITY_FIELDS):
        _required_bool(abilities.get(name), f"player_state abilities.{name}")

    effects = record.get("effects")
    if not isinstance(effects, list):
        raise SceneStoreV2Error("player_state effects must be a list")
    seen_effects: set[str] = set()
    for index, effect in enumerate(effects):
        description = f"player_state effects[{index}]"
        if not isinstance(effect, Mapping) or set(effect) != _EFFECT_FIELDS:
            raise SceneStoreV2Error(f"{description} must contain the complete v1 effect fields")
        effect_id = _resource_location(effect.get("effect"), f"{description}.effect")
        if effect_id in seen_effects:
            raise SceneStoreV2Error("player_state effects must have unique effect IDs")
        seen_effects.add(effect_id)
        _bounded_int(effect.get("duration"), f"{description}.duration", -1, 2**31 - 1)
        _bounded_int(effect.get("amplifier"), f"{description}.amplifier", 0, 255)
        for name in ("ambient", "visible", "show_icon"):
            _required_bool(effect.get(name), f"{description}.{name}")

    vehicle = record.get("vehicle")
    if vehicle is not None:
        _validate_entity_reference(vehicle, "player_state vehicle")

    passengers = record.get("passengers")
    if not isinstance(passengers, list):
        raise SceneStoreV2Error("player_state passengers must be a list")
    passenger_ids: set[int] = set()
    passenger_uuids: set[str] = set()
    for index, passenger in enumerate(passengers):
        entity_id, entity_uuid = _validate_entity_reference(
            passenger,
            f"player_state passengers[{index}]",
        )
        if entity_id in passenger_ids or entity_uuid in passenger_uuids:
            raise SceneStoreV2Error("player_state passengers must have unique entity IDs and UUIDs")
        passenger_ids.add(entity_id)
        passenger_uuids.add(entity_uuid)

    inventory = record.get("inventory")
    if not isinstance(inventory, list):
        raise SceneStoreV2Error("player_state inventory must be a list")
    slots: set[int] = set()
    for index, stack in enumerate(inventory):
        description = f"player_state inventory[{index}]"
        if not isinstance(stack, Mapping):
            raise SceneStoreV2Error(f"{description} must be an object")
        fields = set(stack)
        if not _INVENTORY_REQUIRED_FIELDS <= fields or not fields <= (_INVENTORY_REQUIRED_FIELDS | _INVENTORY_OPTIONAL_FIELDS):
            raise SceneStoreV2Error(f"{description} fields do not match the v1 inventory contract")
        slot = _bounded_int(stack.get("slot"), f"{description}.slot", 0, 42)
        if slot in slots:
            raise SceneStoreV2Error("player_state inventory must have unique slots")
        slots.add(slot)
        _resource_location(stack.get("item"), f"{description}.item")
        _bounded_int(stack.get("count"), f"{description}.count", 1, 999)
        _bounded_int(stack.get("damage"), f"{description}.damage", 0, 2**31 - 1)
        _bounded_int(
            stack.get("max_damage"),
            f"{description}.max_damage",
            0,
            2**31 - 1,
        )
        for name in sorted(_INVENTORY_OPTIONAL_FIELDS & fields):
            _bounded_text(
                stack.get(name),
                f"{description}.{name}",
                MAX_PRIVATE_TEXT_BYTES,
            )


def _finite(value: object, description: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SceneStoreV2Error(f"{description} must be a finite number")
    try:
        result = float(value)
    except (OverflowError, ValueError) as exc:
        raise SceneStoreV2Error(f"{description} must be a finite number") from exc
    if not math.isfinite(result):
        raise SceneStoreV2Error(f"{description} must be a finite number")
    return result


def _vector(value: object, description: str) -> tuple[float, float, float]:
    if not isinstance(value, dict):
        raise SceneStoreV2Error(f"{description} must be an object")
    return tuple(_finite(value.get(axis), f"{description}.{axis}") for axis in ("x", "y", "z"))  # ty:ignore[invalid-return-type]


def _rotation(value: object) -> tuple[float, float, float]:
    if not isinstance(value, dict):
        raise SceneStoreV2Error("player_state rotation must be an object")
    return (
        _finite(value.get("yaw"), "player_state rotation.yaw"),
        _finite(value.get("pitch"), "player_state rotation.pitch"),
        _finite(value.get("head_yaw"), "player_state rotation.head_yaw"),
    )


def _state_columns(record: Mapping[str, Any]) -> dict[str, Any]:
    _validate_private_state_payload(record)
    position = _vector(record.get("position"), "player_state position")
    velocity = _vector(record.get("velocity"), "player_state velocity")
    rotation = _rotation(record.get("rotation"))
    selected_slot = _required_int(record.get("selected_slot"), "player_state selected_slot")
    if not 0 <= selected_slot <= 8:
        raise SceneStoreV2Error("player_state selected_slot must be from 0 to 8")
    return {
        "server_tick": _required_int(record.get("server_tick"), "player_state server_tick"),
        "entity_id": _required_int(record.get("entity_id"), "player_state entity_id"),
        "dimension": _required_text(record.get("dimension"), "player_state dimension"),
        "position_x": position[0],
        "position_y": position[1],
        "position_z": position[2],
        "velocity_x": velocity[0],
        "velocity_y": velocity[1],
        "velocity_z": velocity[2],
        "yaw": rotation[0],
        "pitch": rotation[1],
        "head_yaw": rotation[2],
        "alive": _required_bool(record.get("alive"), "player_state alive"),
        "on_ground": _required_bool(record.get("on_ground"), "player_state on_ground"),
        "pose": _required_text(record.get("pose"), "player_state pose"),
        "sprinting": _required_bool(record.get("sprinting"), "player_state sprinting"),
        "sneaking": _required_bool(record.get("sneaking"), "player_state sneaking"),
        "swimming": _required_bool(record.get("swimming"), "player_state swimming"),
        "fall_flying": _required_bool(record.get("fall_flying"), "player_state fall_flying"),
        "using_item": _required_bool(record.get("using_item"), "player_state using_item"),
        "use_item_remaining_ticks": _required_int(
            record.get("use_item_remaining_ticks"),
            "player_state use_item_remaining_ticks",
        ),
        "game_mode": _required_text(record.get("game_mode"), "player_state game_mode"),
        "health": _finite(record.get("health"), "player_state health"),
        "max_health": _finite(record.get("max_health"), "player_state max_health"),
        "absorption": _finite(record.get("absorption"), "player_state absorption"),
        "armor": _required_int(record.get("armor"), "player_state armor"),
        "air": _required_int(record.get("air"), "player_state air"),
        "max_air": _required_int(record.get("max_air"), "player_state max_air"),
        "food_level": _required_int(record.get("food_level"), "player_state food_level"),
        "saturation": _finite(record.get("saturation"), "player_state saturation"),
        "experience_level": _required_int(
            record.get("experience_level"),
            "player_state experience_level",
        ),
        "experience_progress": _finite(
            record.get("experience_progress"),
            "player_state experience_progress",
        ),
        "total_experience": _required_int(
            record.get("total_experience"),
            "player_state total_experience",
        ),
        "selected_slot": selected_slot,
        "state_barrier_apply_sequence": _required_int(
            record.get("state_barrier_apply_sequence"),
            "player_state state_barrier_apply_sequence",
        ),
    }


def _read_authoritative_states(
    path: Path,
    identity: SceneIdentity,
) -> tuple[dict[int, dict[str, Any]], tuple[int, int, int, int], str]:
    _check_regular_file(path, "authoritative player states JSONL")
    before = _path_identity(path)
    digest = hashlib.sha256()
    result: dict[int, dict[str, Any]] = {}
    try:
        with path.open("rb") as handle:
            for line_number, raw in enumerate(handle, 1):
                digest.update(raw)
                if len(raw) > MAX_STATE_JSONL_LINE_BYTES:
                    raise SceneStoreV2Error(f"{path}:{line_number}: player state exceeds the size limit")
                if not raw.strip():
                    raise SceneStoreV2Error(f"{path}:{line_number}: empty player-state record")
                try:
                    value = json.loads(raw)
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise SceneStoreV2Error(f"{path}:{line_number}: invalid player-state JSON") from exc
                if not isinstance(value, dict):
                    raise SceneStoreV2Error(f"{path}:{line_number}: player state must be an object")
                record_type = value.get("record_type")
                if record_type is not None and record_type != "player_state":
                    continue
                identifiers = (value.get("session_id"), value.get("player_uuid"), value.get("connection_id"))
                expected = (identity.session_id, identity.player_uuid, identity.connection_id)
                if identifiers != expected:
                    if record_type == "player_state":
                        continue
                    raise SceneStoreV2Error(f"{path}:{line_number}: player state identity does not match the scene")
                columns = _state_columns(value)
                tick = columns["server_tick"]
                if tick in result:
                    raise SceneStoreV2Error(f"{path}:{line_number}: duplicate player_state at tick {tick}")
                result[tick] = value
    except OSError as exc:
        raise SceneStoreV2Error(f"cannot read authoritative player states: {path}") from exc
    after = _path_identity(path)
    if before != after:
        raise SceneStoreV2Error("authoritative player states changed while being read")
    return result, after, digest.hexdigest()


def _copy_v1_blob(source: sqlite3.Connection, destination: Connection, digest: str, kind: str) -> None:
    row = source.execute(
        "SELECT kind, uncompressed_size, compressed_size, zlib_data FROM blobs WHERE sha256 = ?",
        (digest,),
    ).fetchone()
    if row is None or row["kind"] != kind:
        raise SceneStoreV2Error(f"Scene V1 is missing {kind} blob {digest}")
    compressed = bytes(row["zlib_data"])
    if len(compressed) != row["compressed_size"]:
        raise SceneStoreV2Error(f"Scene V1 blob {digest} has an invalid compressed size")
    value = _decompress(compressed, row["uncompressed_size"], f"Scene V1 {kind} blob {digest}")
    if hashlib.sha256(value).hexdigest() != digest:
        raise SceneStoreV2Error(f"Scene V1 blob {digest} has an invalid digest")
    copied = _put_blob(destination, kind, value)
    if copied != digest:
        raise SceneStoreV2Error(f"Scene V1 blob {digest} changed while canonicalizing")


def _v1_blob_json(source: sqlite3.Connection, digest: str, kind: str) -> dict[str, Any]:
    row = source.execute(
        "SELECT kind, uncompressed_size, compressed_size, zlib_data FROM blobs WHERE sha256 = ?",
        (digest,),
    ).fetchone()
    if row is None or row["kind"] != kind:
        raise SceneStoreV2Error(f"Scene V1 is missing {kind} blob {digest}")
    compressed = bytes(row["zlib_data"])
    if len(compressed) != row["compressed_size"]:
        raise SceneStoreV2Error(f"Scene V1 blob {digest} has an invalid compressed size")
    value = _decompress(compressed, row["uncompressed_size"], f"Scene V1 {kind} blob {digest}")
    parsed = _parse_canonical_json(value, f"Scene V1 {kind} blob {digest}")
    if not isinstance(parsed, dict):
        raise SceneStoreV2Error(f"Scene V1 {kind} blob {digest} must contain an object")
    return parsed


def _subject_entity_problem(
    type_id: object,
    entity_uuid: object,
    identity: SceneIdentity,
) -> str | None:
    if type_id != "minecraft:player":
        return f"linked entity type is {type_id!r}, not 'minecraft:player'"
    try:
        observed_uuid = _canonical_uuid(entity_uuid, "linked player entity UUID")
    except SceneStoreV2Error as exc:
        return str(exc)
    if observed_uuid != identity.player_uuid:
        return f"linked player entity UUID {observed_uuid!r} does not match subject {identity.player_uuid!r}"
    return None


def _meta_json_value(
    value: object,
    description: str,
    expected_type: type[Any],
) -> Any:  # noqa: ANN401
    if isinstance(value, bytes):
        raw = value
    elif isinstance(value, str):
        raw = value.encode("utf-8")
    else:
        raise SceneStoreV2Error(f"{description} is not encoded JSON")
    try:
        parsed = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SceneStoreV2Error(f"{description} is invalid JSON") from exc
    if not isinstance(parsed, expected_type):
        raise SceneStoreV2Error(f"{description} must contain a {expected_type.__name__}")
    return parsed


def _play_replay_entry_name(segment_ordinal: int, segment_id: str) -> str:
    if _SAFE_SEGMENT_ID_RE.fullmatch(segment_id) is None:
        raise SceneStoreV2Error(f"scene source replay segment_id is not play-safe: {segment_id!r}")
    if segment_ordinal != 0:
        raise SceneStoreV2Error("Scene V2 accepts one unrotated capture replay with ordinal zero")
    return "capture/replay.zip"


def _validated_play_source_replays(
    value: object,
    description: str = "Scene V2 source replays",
) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, (list, tuple)):
        raise SceneStoreV2Error(f"{description} must be an array")
    if len(value) > 1:
        raise SceneStoreV2Error(f"{description} may contain at most one capture replay")
    result: list[dict[str, Any]] = []
    segment_ids: set[str] = set()
    previous_source_ordinal = -1
    for index, raw in enumerate(value):
        context = f"{description}[{index}]"
        if not isinstance(raw, Mapping) or set(raw) != _SOURCE_REPLAY_FIELDS:
            raise SceneStoreV2Error(f"{context} fields do not match the source replay contract")
        segment_id = _required_text(raw.get("segment_id"), f"{context} segment_id")
        source_ordinal = _bounded_int(
            raw.get("segment_ordinal"),
            f"{context} segment_ordinal",
            0,
            2**31 - 1,
        )
        expected_path = _play_replay_entry_name(source_ordinal, segment_id)
        if source_ordinal <= previous_source_ordinal or segment_id in segment_ids:
            raise SceneStoreV2Error(f"{description} must have unique IDs and increasing source ordinals")
        path = _required_text(raw.get("path"), f"{context} path")
        if path != expected_path:
            raise SceneStoreV2Error(f"{context} path must be the deterministic play-relative replay path")
        digest = _required_text(raw.get("sha256"), f"{context} sha256")
        if _SHA256_RE.fullmatch(digest) is None:
            raise SceneStoreV2Error(f"{context} sha256 must be lowercase SHA-256 text")
        size_bytes = _bounded_int(
            raw.get("size_bytes"),
            f"{context} size_bytes",
            1,
            2**63 - 1,
        )
        if raw.get("format") != "flashback":
            raise SceneStoreV2Error(f"{context} format must be flashback")
        result.append(
            {
                "segment_id": segment_id,
                "segment_ordinal": source_ordinal,
                "path": path,
                "sha256": digest,
                "size_bytes": size_bytes,
                "format": "flashback",
            }
        )
        segment_ids.add(segment_id)
        previous_source_ordinal = source_ordinal
    return tuple(result)


def _play_source_replays(value: object) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, list):
        raise SceneStoreV2Error("Scene V1 source_replays_json must contain a list")
    play_replays: list[dict[str, Any]] = []
    for index, raw in enumerate(value):
        if not isinstance(raw, Mapping):
            raise SceneStoreV2Error(f"Scene V1 source replay {index} must be an object")
        normalized = dict(raw)
        segment_id = _required_text(
            normalized.get("segment_id"),
            f"Scene V1 source replay {index} segment_id",
        )
        segment_ordinal = _bounded_int(
            normalized.get("segment_ordinal"),
            f"Scene V1 source replay {index} segment_ordinal",
            0,
            2**31 - 1,
        )
        normalized["path"] = _play_replay_entry_name(segment_ordinal, segment_id)
        play_replays.append(normalized)
    return _validated_play_source_replays(
        play_replays,
        "normalized Scene V1 source replays",
    )


def _is_absolute_host_path(value: str) -> bool:
    lowered = value.casefold()
    return value.startswith(("/", "\\")) or _WINDOWS_ABSOLUTE_PATH_RE.match(value) is not None or lowered.startswith("file:")


def _play_provenance_value(
    value: Any,  # noqa: ANN401
    source_replays: tuple[dict[str, Any], ...],
) -> Any:  # noqa: ANN401
    if isinstance(value, Mapping):
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            if key == "source_replays":
                nested = _play_source_replays(item)
                if nested != source_replays:
                    raise SceneStoreV2Error("Scene V1 provenance source replays do not match scene metadata")
                normalized[str(key)] = list(nested)
            else:
                normalized[str(key)] = _play_provenance_value(
                    item,
                    source_replays,
                )
        return normalized
    if isinstance(value, list):
        return [_play_provenance_value(item, source_replays) for item in value]
    if isinstance(value, str) and _is_absolute_host_path(value):
        return _REDACTED_HOST_PATH
    return value


def _reject_host_paths(value: Any, description: str) -> None:  # noqa: ANN401
    if isinstance(value, Mapping):
        for key, item in value.items():
            _reject_host_paths(item, f"{description}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _reject_host_paths(item, f"{description}[{index}]")
    elif isinstance(value, str) and _is_absolute_host_path(value):
        raise SceneStoreV2Error(f"{description} contains a host-local absolute path")


def _validated_play_provenance(
    value: object,
    source_replays: tuple[dict[str, Any], ...],
    *,
    identity: SceneIdentity,
    start_tick: int,
    end_tick: int,
    frame_count: int,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SceneStoreV2Error("Scene V2 provenance must be an object")
    _reject_host_paths(value, "Scene V2 provenance")
    if not source_replays and not value:
        return cast(dict[str, Any], value)
    if not source_replays or not value:
        raise SceneStoreV2Error("Scene V2 source replays and extraction provenance must appear together")
    validation_value = json.loads(_canonical_json(value, "Scene V2 provenance validation copy"))
    result = validation_value.get("result")
    subject_poses = result.get("subject_poses") if isinstance(result, dict) else None
    stream = result.get("stream") if isinstance(result, dict) else None
    if not isinstance(subject_poses, dict) or subject_poses.get("path") != _REDACTED_HOST_PATH or not isinstance(stream, dict) or stream.get("path") != _REDACTED_HOST_PATH:
        raise SceneStoreV2Error("Scene V2 provenance must redact non-portable subject-pose and stream paths")
    # The V1 provenance validator intentionally requires the original private
    # subject-pose path.  Validate the play-local value through that mature
    # semantic contract with an ephemeral sentinel; never persist the sentinel.
    subject_poses["path"] = "/portable/subject-poses.jsonl"
    try:
        _validated_extraction_provenance(
            source_replays,
            validation_value,
            identity=identity,
            start_tick=start_tick,
            end_tick=end_tick,
            frame_count=frame_count,
        )
    except SceneStoreValidationError as exc:
        raise SceneStoreV2Error(f"Scene V2 provenance is invalid: {exc}") from exc
    return cast(dict[str, Any], value)


def _entity_for_state(
    state: Mapping[str, Any],
    columns: Mapping[str, Any],
    versions: Sequence[dict[str, Any]],
    source: sqlite3.Connection,
    identity: SceneIdentity,
) -> dict[str, Any]:
    tick = columns["server_tick"]
    candidates = [version for version in versions if version["network_id"] == columns["entity_id"] and version["dimension"] == columns["dimension"] and version["start_tick"] <= tick < version["end_tick"]]
    if len(candidates) != 1:
        raise SceneStoreV2Error(f"player_state at tick {tick} must link to exactly one active scene entity version; found {len(candidates)}")
    selected = candidates[0]
    payload = _v1_blob_json(source, selected["blob_sha256"], "entity")
    problem = _subject_entity_problem(
        selected["type_id"],
        payload.get("uuid"),
        identity,
    )
    if problem is not None:
        raise SceneStoreV2Error(f"player_state at tick {tick} {problem}")
    return selected


def _copy_v1_into_v2(
    source: sqlite3.Connection,
    destination: Connection,
    states_by_tick: Mapping[int, dict[str, Any]],
    identity: SceneIdentity,
) -> tuple[int, ...]:
    meta = source.execute("SELECT * FROM scene_meta WHERE singleton = 1").fetchone()
    if meta is None:
        raise SceneStoreV2Error("Scene V1 metadata is missing")
    source_replays_value = _meta_json_value(
        meta["source_replays_json"],
        "Scene V1 source_replays_json",
        list,
    )
    provenance_value = _meta_json_value(
        meta["provenance_json"],
        "Scene V1 provenance_json",
        dict,
    )
    play_sources = _play_source_replays(source_replays_value)
    play_provenance = _play_provenance_value(
        provenance_value,
        play_sources,
    )
    _validated_play_provenance(
        play_provenance,
        play_sources,
        identity=identity,
        start_tick=meta["start_tick"],
        end_tick=meta["end_tick"],
        frame_count=len(states_by_tick),
    )
    destination.execute(
        insert(schema_info).values(
            singleton=1,
            schema_name=SCENE_STORE_V2_SCHEMA,
            schema_version=SCENE_STORE_V2_SCHEMA_VERSION,
        )
    )
    destination.execute(
        insert(scene_meta).values(
            singleton=1,
            session_id=identity.session_id,
            player_uuid=identity.player_uuid,
            connection_id=identity.connection_id,
            start_tick=meta["start_tick"],
            end_tick=meta["end_tick"],
            source_replays_json=_canonical_json(
                list(play_sources),
                "Scene V2 source replays",
            ),
            sensitive=bool(meta["sensitive"]),
            provenance_json=_canonical_json(
                play_provenance,
                "Scene V2 provenance",
            ),
        )
    )

    for row in source.execute("SELECT sha256, kind FROM blobs ORDER BY sha256"):
        _copy_v1_blob(source, destination, row["sha256"], row["kind"])

    frame_rows = [dict(row) for row in source.execute("SELECT * FROM frames ORDER BY server_tick")]
    ticks = tuple(int(row["server_tick"]) for row in frame_rows)
    if set(states_by_tick) != set(ticks):
        missing = sorted(set(ticks) - set(states_by_tick))
        extra = sorted(set(states_by_tick) - set(ticks))
        detail = []
        if missing:
            detail.append(f"missing {missing[:5]}")
        if extra:
            detail.append(f"unexpected {extra[:5]}")
        raise SceneStoreV2Error("authoritative player-state ticks must exactly match Scene V1 frames: " + ", ".join(detail))
    if frame_rows:
        destination.execute(insert(frames), frame_rows)

    section_rows = []
    for version_id, row in enumerate(
        source.execute("SELECT * FROM section_versions ORDER BY dimension, section_x, section_y, section_z, start_tick"),
        1,
    ):
        section_rows.append({"version_id": version_id, **dict(row)})
    if section_rows:
        destination.execute(insert(section_versions), section_rows)

    entity_rows: list[dict[str, Any]] = []
    for version_id, row in enumerate(
        source.execute("SELECT * FROM entity_versions ORDER BY instance_id, start_tick"),
        1,
    ):
        entity_rows.append({"version_id": version_id, **dict(row)})
    if entity_rows:
        destination.execute(insert(entity_versions), entity_rows)

    block_rows = []
    for version_id, row in enumerate(
        source.execute("SELECT * FROM block_entity_versions ORDER BY dimension, block_x, block_y, block_z, start_tick"),
        1,
    ):
        block_rows.append({"version_id": version_id, **dict(row)})
    if block_rows:
        destination.execute(insert(block_entity_versions), block_rows)

    state_rows: list[dict[str, Any]] = []
    frames_by_tick = {int(row["server_tick"]): row for row in frame_rows}
    for tick in ticks:
        record = states_by_tick[tick]
        columns = _state_columns(record)
        frame = frames_by_tick[tick]
        if columns["dimension"] != frame["dimension"]:
            raise SceneStoreV2Error(f"player_state dimension at tick {tick} does not match the Scene V1 frame")
        state_position = (
            columns["position_x"],
            columns["position_y"],
            columns["position_z"],
        )
        frame_position = (frame["subject_x"], frame["subject_y"], frame["subject_z"])
        if state_position != frame_position:
            raise SceneStoreV2Error(f"player_state position at tick {tick} does not exactly match the Scene V1 frame")
        linked = _entity_for_state(record, columns, entity_rows, source, identity)
        payload_sha256 = _put_blob(destination, "player_state", _canonical_json(dict(record), "player_state"))
        state_rows.append(
            {
                **columns,
                "entity_version_id": linked["version_id"],
                "entity_instance_id": linked["instance_id"],
                "payload_sha256": payload_sha256,
            }
        )
    if state_rows:
        destination.execute(insert(player_states), state_rows)
    return ticks


def finalize_scene_store_v2(
    scene_v1_path: Path | str,
    states_jsonl_path: Path | str,
    output_path: Path | str,
    *,
    force: bool = False,
) -> SceneStoreV2Info:
    """Build one V2 store from private V1 staging and authoritative states."""

    source_path = Path(scene_v1_path)
    states_path = Path(states_jsonl_path)
    output = Path(output_path)
    _check_regular_file(source_path, "Scene V1 store")
    source_before = _path_identity(source_path)
    source_info = validate_scene_store(source_path)
    if not source_info.coverage_complete:
        raise SceneStoreV2Error("Scene V1 coverage is incomplete; refusing to write Scene Store V2")
    states_by_tick, states_identity, states_sha256 = _read_authoritative_states(
        states_path,
        source_info.identity,
    )
    if not output.parent.is_dir() or output.parent.is_symlink():
        raise SceneStoreV2Error(f"Scene V2 parent is not a safe directory: {output.parent}")
    output_before: tuple[int, int, int, int] | None = None
    try:
        output.lstat()
    except FileNotFoundError:
        pass
    else:
        if not force:
            raise SceneStoreV2Error(f"Scene V2 output exists: {output}; pass force=True to replace it")
        _check_regular_file(output, "existing Scene V2 output")
        validate_scene_store_v2(output)
        output_before = _path_identity(output)

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output.name}.tmp-",
        suffix=".sqlite3",
        dir=output.parent,
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    engine = create_engine(URL.create("sqlite+pysqlite", database=str(temporary)))
    try:
        with closing(_connect_read_only(source_path)) as source:
            with engine.begin() as destination:
                destination.exec_driver_sql("PRAGMA journal_mode = DELETE")
                destination.exec_driver_sql("PRAGMA synchronous = FULL")
                create_sqlite_scene_schema(destination)
                ticks = _copy_v1_into_v2(
                    source,
                    destination,
                    states_by_tick,
                    source_info.identity,
                )
        engine.dispose()
        validate_scene_store_v2(
            temporary,
            expected_session_id=source_info.identity.session_id,
            expected_player_uuid=source_info.identity.player_uuid,
            expected_connection_id=source_info.identity.connection_id,
            expected_start_tick=source_info.start_tick,
            expected_end_tick=source_info.end_tick,
            expected_ticks=ticks,
        )
        if _path_identity(source_path) != source_before:
            raise SceneStoreV2Error("Scene V1 store changed during V2 finalization")
        if _sha256_file(source_path) != source_info.sha256:
            raise SceneStoreV2Error("Scene V1 store content changed during V2 finalization")
        if _path_identity(states_path) != states_identity:
            raise SceneStoreV2Error("authoritative player states changed during V2 finalization")
        if _sha256_file(states_path) != states_sha256:
            raise SceneStoreV2Error("authoritative player-state content changed during V2 finalization")
        with temporary.open("rb") as handle:
            os.fsync(handle.fileno())
        if output_before is None:
            try:
                os.link(temporary, output)
            except FileExistsError as exc:
                raise SceneStoreV2Error(f"Scene V2 output appeared during extraction: {output}") from exc
            temporary.unlink()
        else:
            if _path_identity(output) != output_before:
                raise SceneStoreV2Error("existing Scene V2 output changed during replacement")
            os.replace(temporary, output)
        directory_fd = os.open(output.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except Exception:
        engine.dispose()
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise
    return validate_scene_store_v2(
        output,
        expected_session_id=source_info.identity.session_id,
        expected_player_uuid=source_info.identity.player_uuid,
        expected_connection_id=source_info.identity.connection_id,
        expected_start_tick=source_info.start_tick,
        expected_end_tick=source_info.end_tick,
        expected_ticks=ticks,
    )


def _json_meta_value(data: object, description: str, expected_type: type[Any]) -> Any:  # noqa: ANN401
    if not isinstance(data, bytes):
        raise SceneStoreV2ValidationError(f"{description} must contain canonical JSON bytes")
    value = _parse_canonical_json(data, description)
    if not isinstance(value, expected_type):
        raise SceneStoreV2ValidationError(f"{description} must contain a {expected_type.__name__}")
    return value


def _meta_row(connection: sqlite3.Connection) -> sqlite3.Row:
    row = _execute_core(
        connection,
        select(scene_meta).where(scene_meta.c.singleton == 1),
    ).fetchone()
    if row is None:
        raise SceneStoreV2ValidationError("Scene V2 metadata is missing")
    return row


def _validate_no_overlap(
    connection: sqlite3.Connection,
    table: Any,  # noqa: ANN401
    key_columns: Sequence[Any],
    *,
    where: Any | None = None,  # noqa: ANN401
    description: str | None = None,
) -> None:
    prior_max_end = (
        func.max(table.c.end_tick)
        .over(
            partition_by=list(key_columns),
            order_by=(table.c.start_tick, table.c.end_tick, table.c.version_id),
            rows=(None, -1),
        )
        .label("prior_max_end")
    )
    ordered = select(table.c.start_tick, prior_max_end)
    if where is not None:
        ordered = ordered.where(where)
    candidates = ordered.subquery()
    overlap = _execute_core(
        connection,
        select(candidates.c.start_tick).where(candidates.c.prior_max_end > candidates.c.start_tick).limit(1),
    ).fetchone()
    if overlap is not None:
        raise SceneStoreV2ValidationError(description or f"{table.name} contains overlapping intervals")


def _validate_rtrees(connection: sqlite3.Connection) -> None:
    entity_mismatch = connection.execute(
        "SELECT 1 FROM entity_versions AS source "
        "LEFT JOIN entity_versions_rtree AS search ON search.version_id = source.version_id "
        "WHERE search.version_id IS NULL "
        "OR search.min_tick > source.start_tick OR search.max_tick < source.end_tick "
        "OR search.min_x > source.min_x OR search.max_x < source.max_x "
        "OR search.min_y > source.min_y OR search.max_y < source.max_y "
        "OR search.min_z > source.min_z OR search.max_z < source.max_z LIMIT 1"
    ).fetchone()
    entity_extra = connection.execute("SELECT 1 FROM entity_versions_rtree AS search LEFT JOIN entity_versions AS source ON source.version_id = search.version_id WHERE source.version_id IS NULL LIMIT 1").fetchone()
    if entity_mismatch is not None or entity_extra is not None:
        raise SceneStoreV2ValidationError("entity R-tree does not match explicit entity version IDs")

    block_mismatch = connection.execute(
        "SELECT 1 FROM block_entity_versions AS source "
        "LEFT JOIN block_entity_versions_rtree AS search ON search.version_id = source.version_id "
        "WHERE search.version_id IS NULL "
        "OR search.min_tick > source.start_tick OR search.max_tick < source.end_tick "
        "OR search.min_x > source.block_x OR search.max_x < source.block_x + 1 "
        "OR search.min_y > source.block_y OR search.max_y < source.block_y + 1 "
        "OR search.min_z > source.block_z OR search.max_z < source.block_z + 1 LIMIT 1"
    ).fetchone()
    block_extra = connection.execute("SELECT 1 FROM block_entity_versions_rtree AS search LEFT JOIN block_entity_versions AS source ON source.version_id = search.version_id WHERE source.version_id IS NULL LIMIT 1").fetchone()
    if block_mismatch is not None or block_extra is not None:
        raise SceneStoreV2ValidationError("block-entity R-tree does not match explicit block-entity version IDs")


def _validate_entity_version_rows(
    connection: sqlite3.Connection,
    blob_value: Callable[[str, str], bytes],
) -> dict[int, tuple[str, str | None]]:
    linked_identities: dict[int, tuple[str, str | None]] = {}
    rows = _execute_core(
        connection,
        select(
            entity_versions.c.version_id,
            entity_versions.c.instance_id,
            entity_versions.c.network_id,
            entity_versions.c.dimension,
            entity_versions.c.type_id,
            entity_versions.c.min_x,
            entity_versions.c.min_y,
            entity_versions.c.min_z,
            entity_versions.c.max_x,
            entity_versions.c.max_y,
            entity_versions.c.max_z,
            entity_versions.c.blob_sha256,
        ).order_by(entity_versions.c.version_id),
    )
    for row in rows:
        payload = _parse_canonical_json(
            blob_value(row["blob_sha256"], "entity"),
            f"entity version {row['version_id']}",
        )
        if not isinstance(payload, dict):
            raise SceneStoreV2ValidationError(f"entity version {row['version_id']} payload must contain an object")
        try:
            (
                dimension,
                type_id,
                network_id,
                entity_uuid,
                _position,
                _velocity,
                _rotation,
                aabb,
            ) = _entity_fields(row["instance_id"], payload)
        except SceneStoreError as exc:
            raise SceneStoreV2ValidationError(f"entity version {row['version_id']} payload is invalid: {exc}") from exc
        typed = (
            row["dimension"],
            row["type_id"],
            row["network_id"],
            row["min_x"],
            row["min_y"],
            row["min_z"],
            row["max_x"],
            row["max_y"],
            row["max_z"],
        )
        canonical = (dimension, type_id, network_id, *aabb)
        if typed != canonical:
            raise SceneStoreV2ValidationError(f"entity version {row['version_id']} typed columns disagree with its canonical payload")
        linked_identities[row["version_id"]] = (type_id, entity_uuid)
    return linked_identities


def _validate_block_entity_version_rows(
    connection: sqlite3.Connection,
    blob_value: Callable[[str, str], bytes],
) -> None:
    rows = _execute_core(
        connection,
        select(
            block_entity_versions.c.version_id,
            block_entity_versions.c.dimension,
            block_entity_versions.c.block_x,
            block_entity_versions.c.block_y,
            block_entity_versions.c.block_z,
            block_entity_versions.c.type_id,
            block_entity_versions.c.blob_sha256,
        ).order_by(block_entity_versions.c.version_id),
    )
    for row in rows:
        payload = _parse_canonical_json(
            blob_value(row["blob_sha256"], "block_entity"),
            f"block-entity version {row['version_id']}",
        )
        if not isinstance(payload, dict):
            raise SceneStoreV2ValidationError(f"block-entity version {row['version_id']} payload must contain an object")
        try:
            type_id = _block_entity_type(payload)
        except SceneStoreError as exc:
            raise SceneStoreV2ValidationError(f"block-entity version {row['version_id']} payload is invalid: {exc}") from exc
        embedded_dimension = payload.get("dimension")
        if embedded_dimension is not None and embedded_dimension != row["dimension"]:
            raise SceneStoreV2ValidationError(f"block-entity version {row['version_id']} dimension disagrees with its canonical payload")
        embedded_position = payload.get("position")
        if embedded_position is not None:
            if (
                not isinstance(embedded_position, list)
                or len(embedded_position) != 3
                or any(isinstance(coordinate, bool) or not isinstance(coordinate, int) for coordinate in embedded_position)
                or tuple(embedded_position) != (row["block_x"], row["block_y"], row["block_z"])
            ):
                raise SceneStoreV2ValidationError(f"block-entity version {row['version_id']} position disagrees with its canonical payload")
        if type_id != row["type_id"]:
            raise SceneStoreV2ValidationError(f"block-entity version {row['version_id']} type disagrees with its canonical payload")


def _validate_player_state_row(
    row: sqlite3.Row,
    blob_value: Callable[[str, str], bytes],
) -> Mapping[str, Any]:
    value = _parse_canonical_json(
        blob_value(row["payload_sha256"], "player_state"),
        f"player_state blob at tick {row['server_tick']}",
    )
    if not isinstance(value, dict):
        raise SceneStoreV2ValidationError("player_state blob must contain an object")
    try:
        expected = _state_columns(value)
    except SceneStoreV2Error as exc:
        raise SceneStoreV2ValidationError(str(exc)) from exc
    columns = (
        "server_tick",
        "entity_id",
        "dimension",
        "position_x",
        "position_y",
        "position_z",
        "velocity_x",
        "velocity_y",
        "velocity_z",
        "yaw",
        "pitch",
        "head_yaw",
        "alive",
        "on_ground",
        "pose",
        "sprinting",
        "sneaking",
        "swimming",
        "fall_flying",
        "using_item",
        "use_item_remaining_ticks",
        "game_mode",
        "health",
        "max_health",
        "absorption",
        "armor",
        "air",
        "max_air",
        "food_level",
        "saturation",
        "experience_level",
        "experience_progress",
        "total_experience",
        "selected_slot",
        "state_barrier_apply_sequence",
    )
    boolean_columns = {
        "alive",
        "on_ground",
        "sprinting",
        "sneaking",
        "swimming",
        "fall_flying",
        "using_item",
    }
    for name in columns:
        actual = bool(row[name]) if name in boolean_columns else row[name]
        if actual != expected[name]:
            raise SceneStoreV2ValidationError(f"player_state typed column {name} disagrees with its canonical payload at tick {row['server_tick']}")
    return value


def _segment_id_for_frame(
    frame_id: str,
    source_replays: Sequence[Mapping[str, Any]],
) -> str | None:
    if not source_replays:
        return None
    matches = [str(source["segment_id"]) for source in source_replays if frame_id.startswith(f"{source['segment_id']}:")]
    if len(matches) != 1:
        raise SceneStoreV2ValidationError(f"scene frame {frame_id!r} does not identify exactly one source replay segment")
    return matches[0]


def _iter_frame_alignments(
    connection: sqlite3.Connection,
    source_replays: Sequence[Mapping[str, Any]],
) -> Iterator[SceneFrameAlignment]:
    for row in _execute_core(connection, FRAME_ALIGNMENT_SELECT):
        replay_tick = row["replay_tick"]
        if source_replays and (isinstance(replay_tick, bool) or not isinstance(replay_tick, int) or replay_tick < 0):
            raise SceneStoreV2ValidationError(f"scene frame {row['frame_id']!r} has no non-negative replay tick")
        yield SceneFrameAlignment(
            server_tick=row["server_tick"],
            frame_id=row["frame_id"],
            segment_id=_segment_id_for_frame(row["frame_id"], source_replays),
            replay_tick=replay_tick,
        )


def _validate_connection(connection: sqlite3.Connection) -> None:
    user_version = connection.execute("PRAGMA user_version").fetchone()[0]
    if user_version != SCENE_STORE_V2_USER_VERSION:
        raise SceneStoreV2ValidationError(f"unsupported Scene V2 user_version {user_version}; expected {SCENE_STORE_V2_USER_VERSION}")
    integrity = connection.execute("PRAGMA integrity_check").fetchone()
    if integrity is None or integrity[0] != "ok":
        detail = "missing result" if integrity is None else str(integrity[0])
        raise SceneStoreV2ValidationError(f"Scene V2 integrity_check failed: {detail}")
    if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise SceneStoreV2ValidationError("Scene V2 contains a broken foreign key")

    schema = _execute_core(
        connection,
        select(schema_info).where(schema_info.c.singleton == 1),
    ).fetchone()
    if schema is None:
        raise SceneStoreV2ValidationError("Scene V2 schema_info is missing")
    if schema["schema_name"] != SCENE_STORE_V2_SCHEMA or schema["schema_version"] != SCENE_STORE_V2_SCHEMA_VERSION:
        raise SceneStoreV2ValidationError("Scene V2 schema identity is unsupported")
    meta = _meta_row(connection)
    identity_values = (meta["session_id"], meta["player_uuid"], meta["connection_id"])
    if any(not isinstance(value, str) or not value for value in identity_values):
        raise SceneStoreV2ValidationError("Scene V2 identity is incomplete")
    identity = SceneIdentity(*identity_values)
    if meta["start_tick"] > meta["end_tick"]:
        raise SceneStoreV2ValidationError("Scene V2 tick bounds are reversed")
    source_replays_value = _json_meta_value(
        meta["source_replays_json"],
        "Scene V2 source replays",
        list,
    )
    provenance = _json_meta_value(
        meta["provenance_json"],
        "Scene V2 provenance",
        dict,
    )
    try:
        source_replays = _validated_play_source_replays(source_replays_value)
    except SceneStoreV2Error as exc:
        raise SceneStoreV2ValidationError(str(exc)) from exc

    frame_count = _execute_core(
        connection,
        select(func.count()).select_from(frames),
    ).fetchone()[0]
    state_count = _execute_core(
        connection,
        select(func.count()).select_from(player_states),
    ).fetchone()[0]
    if frame_count == 0:
        raise SceneStoreV2ValidationError("Scene V2 contains no frames")
    try:
        _validated_play_provenance(
            provenance,
            source_replays,
            identity=identity,
            start_tick=meta["start_tick"],
            end_tick=meta["end_tick"],
            frame_count=frame_count,
        )
    except SceneStoreV2Error as exc:
        raise SceneStoreV2ValidationError(str(exc)) from exc
    if state_count != frame_count:
        raise SceneStoreV2ValidationError("Scene V2 requires exactly one player_state per frame")
    coverage_gap = _execute_core(
        connection,
        select(frames.c.server_tick).where(frames.c.coverage_complete.is_(False)).limit(1),
    ).fetchone()
    if coverage_gap is not None:
        raise SceneStoreV2ValidationError("Scene V2 contains an incomplete scene frame")
    missing_state = _execute_core(
        connection,
        select(frames.c.server_tick)
        .select_from(
            frames.outerjoin(
                player_states,
                player_states.c.server_tick == frames.c.server_tick,
            )
        )
        .where(player_states.c.server_tick.is_(None))
        .limit(1),
    ).fetchone()
    if missing_state is not None:
        raise SceneStoreV2ValidationError("Scene V2 frame/player_state coverage is not one-to-one")
    invalid_frame = _execute_core(
        connection,
        select(frames.c.server_tick)
        .where(
            or_(
                frames.c.server_tick < bindparam("start_tick"),
                frames.c.server_tick > bindparam("end_tick"),
                frames.c.frame_id == "",
                frames.c.dimension == "",
            )
        )
        .limit(1),
        {"start_tick": meta["start_tick"], "end_tick": meta["end_tick"]},
    ).fetchone()
    if invalid_frame is not None:
        raise SceneStoreV2ValidationError("Scene V2 contains an invalid frame")
    for _alignment in _iter_frame_alignments(connection, source_replays):
        pass

    for table in (section_versions, entity_versions, block_entity_versions):
        invalid = _execute_core(
            connection,
            select(table.c.version_id)
            .where(
                or_(
                    table.c.version_id <= 0,
                    table.c.start_tick < bindparam("start_tick"),
                    table.c.end_tick > bindparam("end_tick_exclusive"),
                    table.c.start_tick >= table.c.end_tick,
                )
            )
            .limit(1),
            {
                "start_tick": meta["start_tick"],
                "end_tick_exclusive": meta["end_tick"] + 1,
            },
        ).fetchone()
        if invalid is not None:
            raise SceneStoreV2ValidationError(f"{table.name} contains an invalid explicit version interval")
    _validate_no_overlap(
        connection,
        section_versions,
        (
            section_versions.c.dimension,
            section_versions.c.section_x,
            section_versions.c.section_y,
            section_versions.c.section_z,
        ),
    )
    _validate_no_overlap(
        connection,
        entity_versions,
        (entity_versions.c.instance_id,),
    )
    _validate_no_overlap(
        connection,
        entity_versions,
        (entity_versions.c.network_id,),
        where=entity_versions.c.network_id.is_not(None),
        description="Scene V2 contains overlapping entity network-id lifetimes",
    )
    _validate_no_overlap(
        connection,
        block_entity_versions,
        (
            block_entity_versions.c.dimension,
            block_entity_versions.c.block_x,
            block_entity_versions.c.block_y,
            block_entity_versions.c.block_z,
        ),
    )
    _validate_rtrees(connection)

    references = (
        (frames, frames.c.payload_sha256, "frame"),
        (player_states, player_states.c.payload_sha256, "player_state"),
        (section_versions, section_versions.c.blob_sha256, "section"),
        (entity_versions, entity_versions.c.blob_sha256, "entity"),
        (
            block_entity_versions,
            block_entity_versions.c.blob_sha256,
            "block_entity",
        ),
    )
    for table, column, kind in references:
        wrong = _execute_core(
            connection,
            select(column).select_from(table.join(blobs, blobs.c.sha256 == column)).where(blobs.c.kind != bindparam("expected_kind")).limit(1),
            {"expected_kind": kind},
        ).fetchone()
        if wrong is not None:
            raise SceneStoreV2ValidationError(f"{table.name} references a blob with the wrong kind")
    blob_cache: OrderedDict[tuple[str, str], bytes] = OrderedDict()
    blob_cache_size = 0

    def cache_blob_value(key: tuple[str, str], value: bytes) -> None:
        nonlocal blob_cache_size
        if len(value) > MAX_VALIDATION_BLOB_CACHE_BYTES:
            return
        previous = blob_cache.pop(key, None)
        if previous is not None:
            blob_cache_size -= len(previous)
        while blob_cache and (blob_cache_size + len(value) > MAX_VALIDATION_BLOB_CACHE_BYTES or len(blob_cache) >= MAX_VALIDATION_BLOB_CACHE_ENTRIES):
            _, evicted = blob_cache.popitem(last=False)
            blob_cache_size -= len(evicted)
        blob_cache[key] = value
        blob_cache_size += len(value)

    def validated_blob_value(digest: str, expected_kind: str) -> bytes:
        key = (digest, expected_kind)
        cached = blob_cache.get(key)
        if cached is not None:
            blob_cache.move_to_end(key)
            return cached
        value = _blob_value(connection, digest, expected_kind)
        cache_blob_value(key, value)
        return value

    # Fetch the blob table once. Point-reading every blob through SQLAlchemy's
    # compiler made import time grow with thousands of avoidable SQL queries.
    for blob in _execute_core(
        connection,
        select(
            blobs.c.sha256,
            blobs.c.kind,
            blobs.c.encoding,
            blobs.c.uncompressed_size,
            blobs.c.compressed_size,
            blobs.c.data,
        ).order_by(blobs.c.sha256),
    ):
        value = _validated_blob_row(blob, blob["sha256"], blob["kind"])
        key = (blob["sha256"], blob["kind"])
        cache_blob_value(key, value)
        if blob["kind"] == "section":
            _decode_section_blob(value)
        else:
            payload = _parse_canonical_json(value, f"{blob['kind']} blob {blob['sha256']}")
            if not isinstance(payload, dict):
                raise SceneStoreV2ValidationError(f"{blob['kind']} blob {blob['sha256']} must contain an object")

    linked_entity_identities = _validate_entity_version_rows(connection, validated_blob_value)
    _validate_block_entity_version_rows(connection, validated_blob_value)

    state_rows = _execute_core(
        connection,
        select(
            player_states,
            frames.c.dimension.label("frame_dimension"),
            frames.c.subject_x,
            frames.c.subject_y,
            frames.c.subject_z,
            entity_versions.c.instance_id.label("linked_instance_id"),
            entity_versions.c.network_id.label("linked_network_id"),
            entity_versions.c.dimension.label("linked_dimension"),
            entity_versions.c.start_tick.label("linked_start_tick"),
            entity_versions.c.end_tick.label("linked_end_tick"),
        )
        .select_from(
            player_states.join(
                frames,
                frames.c.server_tick == player_states.c.server_tick,
            ).join(
                entity_versions,
                entity_versions.c.version_id == player_states.c.entity_version_id,
            )
        )
        .order_by(player_states.c.server_tick),
    )
    for row in state_rows:
        payload = _validate_player_state_row(row, validated_blob_value)
        if (
            payload.get("session_id"),
            payload.get("player_uuid"),
            payload.get("connection_id"),
        ) != identity_values:
            raise SceneStoreV2ValidationError("player_state identity does not match Scene V2 metadata")
        if row["dimension"] != row["frame_dimension"] or (row["position_x"], row["position_y"], row["position_z"]) != (row["subject_x"], row["subject_y"], row["subject_z"]):
            raise SceneStoreV2ValidationError(f"player_state pose does not match its scene frame at tick {row['server_tick']}")
        if row["entity_instance_id"] != row["linked_instance_id"] or row["entity_id"] != row["linked_network_id"] or row["dimension"] != row["linked_dimension"] or not row["linked_start_tick"] <= row["server_tick"] < row["linked_end_tick"]:
            raise SceneStoreV2ValidationError(f"player_state entity link is invalid at tick {row['server_tick']}")
        linked_type_id, linked_uuid = linked_entity_identities[row["entity_version_id"]]
        problem = _subject_entity_problem(linked_type_id, linked_uuid, identity)
        if problem is not None:
            raise SceneStoreV2ValidationError(f"player_state at tick {row['server_tick']} {problem}")


def _validated_ticks(values: Iterable[int]) -> tuple[int, ...]:
    ticks = tuple(values)
    if not ticks:
        raise SceneStoreV2Error("expected Scene V2 ticks cannot be empty")
    if any(isinstance(tick, bool) or not isinstance(tick, int) for tick in ticks):
        raise SceneStoreV2Error("expected Scene V2 ticks must be integers")
    if tuple(sorted(set(ticks))) != ticks:
        raise SceneStoreV2Error("expected Scene V2 ticks must be strictly increasing and unique")
    return ticks


def _info(connection: sqlite3.Connection, path: Path, include_hash: bool) -> SceneStoreV2Info:
    meta = _meta_row(connection)
    ticks = tuple(
        row[0]
        for row in _execute_core(
            connection,
            select(frames.c.server_tick).order_by(frames.c.server_tick),
        )
    )
    source_replays = _json_meta_value(meta["source_replays_json"], "Scene V2 source replays", list)
    provenance = _json_meta_value(meta["provenance_json"], "Scene V2 provenance", dict)
    return SceneStoreV2Info(
        identity=SceneIdentity(meta["session_id"], meta["player_uuid"], meta["connection_id"]),
        start_tick=meta["start_tick"],
        end_tick=meta["end_tick"],
        frame_count=len(ticks),
        ticks=ticks,
        source_replays=tuple(_freeze_json(item) for item in source_replays),
        provenance=_freeze_json(provenance),
        sensitive=bool(meta["sensitive"]),
        sha256=_sha256_file(path) if include_hash else "",
        size_bytes=path.stat().st_size if include_hash else 0,
    )


def validate_scene_store_v2(
    path: Path | str,
    *,
    expected_session_id: str | None = None,
    expected_player_uuid: str | None = None,
    expected_connection_id: str | None = None,
    expected_start_tick: int | None = None,
    expected_end_tick: int | None = None,
    expected_ticks: Iterable[int] | None = None,
) -> SceneStoreV2Info:
    """Fully validate a Scene Store V2 and its SQLite accelerators."""

    store_path = Path(path)
    try:
        _check_regular_file(store_path, "Scene V2 store")
        before = _path_identity(store_path)
        with closing(_connect_read_only(store_path)) as connection:
            _validate_connection(connection)
            info = _info(connection, store_path, include_hash=False)
        digest = _sha256_file(store_path)
        after = _path_identity(store_path)
    except SceneStoreV2Error:
        raise
    except (OSError, sqlite3.Error) as exc:
        raise SceneStoreV2ValidationError(f"cannot validate Scene V2 store {store_path}: {exc}") from exc
    if before != after:
        raise SceneStoreV2ValidationError("Scene V2 store changed while being validated")
    info = SceneStoreV2Info(
        identity=info.identity,
        start_tick=info.start_tick,
        end_tick=info.end_tick,
        frame_count=info.frame_count,
        ticks=info.ticks,
        source_replays=info.source_replays,
        provenance=info.provenance,
        sensitive=info.sensitive,
        sha256=digest,
        size_bytes=after[2],
    )
    expected_identity = SceneIdentity(
        expected_session_id or info.identity.session_id,
        expected_player_uuid or info.identity.player_uuid,
        expected_connection_id or info.identity.connection_id,
    )
    if info.identity != expected_identity:
        raise SceneStoreV2ValidationError("Scene V2 identity does not match the expected session/player/connection")
    if expected_start_tick is not None and info.start_tick != expected_start_tick:
        raise SceneStoreV2ValidationError(f"Scene V2 starts at tick {info.start_tick}, expected {expected_start_tick}")
    if expected_end_tick is not None and info.end_tick != expected_end_tick:
        raise SceneStoreV2ValidationError(f"Scene V2 ends at tick {info.end_tick}, expected {expected_end_tick}")
    if expected_ticks is not None and info.ticks != _validated_ticks(expected_ticks):
        raise SceneStoreV2ValidationError("Scene V2 frame ticks do not match expected ticks")
    return info


def _frame_from_row(connection: sqlite3.Connection, row: sqlite3.Row) -> SceneFrame:
    payload = _parse_canonical_json(
        _blob_value(connection, row["payload_sha256"], "frame"),
        f"frame blob {row['payload_sha256']}",
    )
    if not isinstance(payload, dict):
        raise SceneStoreV2ValidationError("frame payload must contain an object")
    reasons = payload.get("reasons", [])
    metadata = payload.get("metadata", {})
    if not isinstance(reasons, list) or not all(isinstance(item, str) for item in reasons):
        raise SceneStoreV2ValidationError("frame reasons must be an array of text")
    if not isinstance(metadata, dict):
        raise SceneStoreV2ValidationError("frame metadata must be an object")
    return SceneFrame(
        frame_id=row["frame_id"],
        tick=row["server_tick"],
        replay_tick=row["replay_tick"],
        dimension=row["dimension"],
        subject_position=(row["subject_x"], row["subject_y"], row["subject_z"]),
        coverage_complete=bool(row["coverage_complete"]),
        reasons=tuple(reasons),
        metadata=_freeze_json(metadata),
    )


def _player_state_from_row(connection: sqlite3.Connection, row: sqlite3.Row) -> PlayerStateV2:
    payload = _parse_canonical_json(
        _blob_value(connection, row["payload_sha256"], "player_state"),
        f"player_state blob {row['payload_sha256']}",
    )
    if not isinstance(payload, dict):
        raise SceneStoreV2ValidationError("player_state payload must contain an object")
    return PlayerStateV2(
        tick=row["server_tick"],
        entity_version_id=row["entity_version_id"],
        entity_instance_id=row["entity_instance_id"],
        entity_id=row["entity_id"],
        dimension=row["dimension"],
        position=(row["position_x"], row["position_y"], row["position_z"]),
        velocity=(row["velocity_x"], row["velocity_y"], row["velocity_z"]),
        rotation=(row["yaw"], row["pitch"], row["head_yaw"]),
        alive=bool(row["alive"]),
        on_ground=bool(row["on_ground"]),
        pose=row["pose"],
        sprinting=bool(row["sprinting"]),
        sneaking=bool(row["sneaking"]),
        swimming=bool(row["swimming"]),
        fall_flying=bool(row["fall_flying"]),
        using_item=bool(row["using_item"]),
        use_item_remaining_ticks=row["use_item_remaining_ticks"],
        game_mode=row["game_mode"],
        health=row["health"],
        max_health=row["max_health"],
        absorption=row["absorption"],
        armor=row["armor"],
        air=row["air"],
        max_air=row["max_air"],
        food_level=row["food_level"],
        saturation=row["saturation"],
        experience_level=row["experience_level"],
        experience_progress=row["experience_progress"],
        total_experience=row["total_experience"],
        selected_slot=row["selected_slot"],
        state_barrier_apply_sequence=row["state_barrier_apply_sequence"],
        payload=_freeze_json(payload),
    )


def _entity_from_row(connection: sqlite3.Connection, row: sqlite3.Row) -> SceneEntity:
    payload = _parse_canonical_json(
        _blob_value(connection, row["blob_sha256"], "entity"),
        f"entity blob {row['blob_sha256']}",
    )
    if not isinstance(payload, dict):
        raise SceneStoreV2ValidationError("entity payload must contain an object")
    dimension, type_id, network_id, uuid, position, velocity, rotation, aabb = _entity_fields(row["instance_id"], payload)
    return SceneEntity(
        instance_id=row["instance_id"],
        dimension=dimension,
        type_id=type_id,
        network_id=network_id,
        uuid=uuid,
        position=position,
        velocity=velocity,
        rotation=rotation,
        aabb=aabb,
        payload=_freeze_json(payload),
    )


def _block_entity_from_row(connection: sqlite3.Connection, row: sqlite3.Row) -> SceneBlockEntity:
    payload = _parse_canonical_json(
        _blob_value(connection, row["blob_sha256"], "block_entity"),
        f"block-entity blob {row['blob_sha256']}",
    )
    if not isinstance(payload, dict):
        raise SceneStoreV2ValidationError("block-entity payload must contain an object")
    return SceneBlockEntity(
        dimension=row["dimension"],
        position=(row["block_x"], row["block_y"], row["block_z"]),
        type_id=_block_entity_type(payload),
        payload=_freeze_json(payload),
    )


class SceneStoreV2:
    """Validated read-only access to one connection-scoped Scene Store V2."""

    def __init__(self, path: Path | str, *, validate: bool = True) -> None:
        self.path = Path(path)
        self._connection: sqlite3.Connection | None = None
        self._info: SceneStoreV2Info | None = None
        if validate:
            self._info = validate_scene_store_v2(self.path)
        else:
            _check_regular_file(self.path, "Scene V2 store")
        try:
            self._connection = _connect_read_only(self.path)
            if not validate:
                version = self._connection.execute("PRAGMA user_version").fetchone()[0]
                if version != SCENE_STORE_V2_USER_VERSION:
                    raise SceneStoreV2ValidationError(f"unsupported Scene V2 user_version {version}; expected {SCENE_STORE_V2_USER_VERSION}")
                _meta_row(self._connection)
        except Exception:
            if self._connection is not None:
                self._connection.close()
            raise
        self._closed = False

    def __enter__(self) -> SceneStoreV2:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    @property
    def info(self) -> SceneStoreV2Info:
        self._ensure_open()
        assert self._connection is not None
        if self._info is None:
            self._info = _info(self._connection, self.path, include_hash=False)
        return self._info

    def close(self) -> None:
        if not self._closed:
            assert self._connection is not None
            self._connection.close()
            self._closed = True

    def _ensure_open(self) -> None:
        if self._closed:
            raise SceneStoreV2Error("Scene V2 reader is closed")

    def _frame_row(self, tick_or_frame_id: int | str) -> sqlite3.Row:
        self._ensure_open()
        assert self._connection is not None
        if isinstance(tick_or_frame_id, bool):
            raise SceneStoreV2Error("scene frame key must be an integer tick or frame id")
        if isinstance(tick_or_frame_id, int):
            row = _execute_core(
                self._connection,
                select(frames).where(frames.c.server_tick == bindparam("server_tick")),
                {"server_tick": tick_or_frame_id},
            ).fetchone()
        elif isinstance(tick_or_frame_id, str) and tick_or_frame_id:
            row = _execute_core(
                self._connection,
                select(frames).where(frames.c.frame_id == bindparam("frame_id")),
                {"frame_id": tick_or_frame_id},
            ).fetchone()
        else:
            raise SceneStoreV2Error("scene frame key must be an integer tick or frame id")
        if row is None:
            raise SceneStoreV2Error(f"scene frame not found: {tick_or_frame_id}")
        return row

    def frame(self, tick_or_frame_id: int | str) -> SceneFrame:
        assert self._connection is not None
        return _frame_from_row(self._connection, self._frame_row(tick_or_frame_id))

    def iter_frame_alignments(self) -> Iterator[SceneFrameAlignment]:
        """Yield the validated server/replay identity for every scene frame."""

        self._ensure_open()
        assert self._connection is not None
        return _iter_frame_alignments(
            self._connection,
            self.info.source_replays,
        )

    def player_state(self, tick_or_frame_id: int | str) -> PlayerStateV2:
        assert self._connection is not None
        tick = self._frame_row(tick_or_frame_id)["server_tick"]
        row = _execute_core(
            self._connection,
            select(player_states).where(player_states.c.server_tick == bindparam("server_tick")),
            {"server_tick": tick},
        ).fetchone()
        if row is None:
            raise SceneStoreV2Error(f"player state not found: {tick_or_frame_id}")
        return _player_state_from_row(self._connection, row)

    def iter_player_states(
        self,
        *,
        start_tick: int | None = None,
        end_tick: int | None = None,
        limit: int = 10_000,
    ) -> Iterator[PlayerStateV2]:
        self._ensure_open()
        assert self._connection is not None
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_STATE_READ_ROWS:
            raise SceneStoreV2Error(f"player-state limit must be an integer from 1 to {MAX_STATE_READ_ROWS}")
        lower = self.info.start_tick if start_tick is None else _required_int(start_tick, "start_tick")
        upper = self.info.end_tick if end_tick is None else _required_int(end_tick, "end_tick")
        if lower > upper:
            raise SceneStoreV2Error("start_tick cannot follow end_tick")
        rows = _execute_core(
            self._connection,
            select(player_states)
            .where(
                player_states.c.server_tick.between(
                    bindparam("lower_tick"),
                    bindparam("upper_tick"),
                )
            )
            .order_by(player_states.c.server_tick)
            .limit(bindparam("row_limit")),
            {
                "lower_tick": lower,
                "upper_tick": upper,
                "row_limit": limit + 1,
            },
        ).fetchall()
        if len(rows) > limit:
            raise SceneStoreV2Error(f"player-state query exceeds the limit of {limit}")
        return iter(tuple(_player_state_from_row(self._connection, row) for row in rows))

    def trajectory(
        self,
        *,
        start_tick: int | None = None,
        end_tick: int | None = None,
        limit: int = 10_000,
    ) -> tuple[SceneTrajectoryPoint, ...]:
        return tuple(
            SceneTrajectoryPoint(
                tick=state.tick,
                dimension=state.dimension,
                position=state.position,
                entity_instance_id=state.entity_instance_id,
            )
            for state in self.iter_player_states(
                start_tick=start_tick,
                end_tick=end_tick,
                limit=limit,
            )
        )

    def _bounded_object_rows(
        self,
        cursor: sqlite3.Cursor,
        *,
        label: str,
        expected_kind: str,
        max_count: int,
    ) -> list[sqlite3.Row]:
        assert self._connection is not None
        try:
            rows = cursor.fetchmany(max_count + 1)
        finally:
            cursor.close()
        if len(rows) > max_count:
            raise SceneStoreV2Error(f"scene crop {label} count exceeds the limit of {max_count}")
        total = 0
        for row in rows:
            blob = _execute_core(
                self._connection,
                select(blobs.c.kind, blobs.c.uncompressed_size).where(blobs.c.sha256 == bindparam("digest")),
                {"digest": row["blob_sha256"]},
            ).fetchone()
            if blob is None or blob["kind"] != expected_kind:
                raise SceneStoreV2ValidationError(f"scene crop {label} references an invalid blob")
            if blob["uncompressed_size"] > MAX_CROP_OBJECT_PAYLOAD_BYTES:
                raise SceneStoreV2Error(f"scene crop {label} payload exceeds {MAX_CROP_OBJECT_PAYLOAD_BYTES} bytes")
            total += blob["uncompressed_size"]
            if total > MAX_CROP_OBJECT_PAYLOAD_TOTAL_BYTES:
                raise SceneStoreV2Error(f"scene crop {label} payloads exceed {MAX_CROP_OBJECT_PAYLOAD_TOTAL_BYTES} bytes")
        return rows

    def _entities_in_box(
        self,
        tick: int,
        dimension: str,
        origin: tuple[int, int, int],
        shape: tuple[int, int, int],
    ) -> tuple[SceneEntity, ...]:
        assert self._connection is not None
        maximum = tuple(origin[index] + shape[index] for index in range(3))
        cursor = self._connection.execute(
            _ENTITY_BOX_SQL + "LIMIT ?",
            (
                tick,
                tick,
                origin[0],
                maximum[0],
                origin[1],
                maximum[1],
                origin[2],
                maximum[2],
                dimension,
                tick,
                tick,
                origin[0],
                maximum[0],
                origin[1],
                maximum[1],
                origin[2],
                maximum[2],
                MAX_CROP_ENTITIES + 1,
            ),
        )
        rows = self._bounded_object_rows(
            cursor,
            label="entity",
            expected_kind="entity",
            max_count=MAX_CROP_ENTITIES,
        )
        return tuple(_entity_from_row(self._connection, row) for row in rows)

    def _block_entities_in_box(
        self,
        tick: int,
        dimension: str,
        origin: tuple[int, int, int],
        shape: tuple[int, int, int],
    ) -> tuple[SceneBlockEntity, ...]:
        assert self._connection is not None
        maximum = tuple(origin[index] + shape[index] for index in range(3))
        cursor = self._connection.execute(
            _BLOCK_ENTITY_BOX_SQL + "LIMIT ?",
            (
                tick,
                tick,
                origin[0],
                maximum[0],
                origin[1],
                maximum[1],
                origin[2],
                maximum[2],
                dimension,
                tick,
                tick,
                origin[0],
                maximum[0],
                origin[1],
                maximum[1],
                origin[2],
                maximum[2],
                MAX_CROP_BLOCK_ENTITIES + 1,
            ),
        )
        rows = self._bounded_object_rows(
            cursor,
            label="block-entity",
            expected_kind="block_entity",
            max_count=MAX_CROP_BLOCK_ENTITIES,
        )
        return tuple(_block_entity_from_row(self._connection, row) for row in rows)

    def materialize_crop(
        self,
        tick_or_frame_id: int | str,
        origin: tuple[int, int, int],
        shape: tuple[int, int, int],
        max_cells: int = DEFAULT_MAX_CROP_CELLS,
    ) -> SceneCrop:
        assert self._connection is not None
        frame = self.frame(tick_or_frame_id)
        if len(origin) != 3 or any(isinstance(value, bool) or not isinstance(value, int) for value in origin):
            raise SceneStoreV2Error("scene crop origin must contain three integers")
        if len(shape) != 3 or any(isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in shape):
            raise SceneStoreV2Error("scene crop shape must contain three positive integers")
        if isinstance(max_cells, bool) or not isinstance(max_cells, int) or max_cells <= 0:
            raise SceneStoreV2Error("scene crop max_cells must be a positive integer")
        cell_count = shape[0] * shape[1] * shape[2]
        effective_limit = min(max_cells, DEFAULT_MAX_CROP_CELLS)
        if cell_count > effective_limit:
            raise SceneStoreV2Error(f"scene crop contains {cell_count} cells; limit is {effective_limit}")
        for index in range(3):
            maximum_coordinate = origin[index] + shape[index]
            if not SQLITE_INTEGER_MIN <= origin[index] <= SQLITE_INTEGER_MAX or not (SQLITE_INTEGER_MIN <= maximum_coordinate <= SQLITE_INTEGER_MAX):
                raise SceneStoreV2Error("scene crop bounds must fit signed 64-bit SQLite integers")

        palette_values: list[Any] = []
        palette_lookup: dict[bytes, int] = {}
        indices = [UNKNOWN_PALETTE_INDEX] * cell_count
        coverage = [False] * cell_count
        min_section = tuple(value // SECTION_EDGE for value in origin)
        maximum = tuple(origin[index] + shape[index] - 1 for index in range(3))
        max_section = tuple(value // SECTION_EDGE for value in maximum)
        rows = _execute_core(
            self._connection,
            select(section_versions)
            .where(
                and_(
                    section_versions.c.dimension == bindparam("dimension"),
                    section_versions.c.start_tick <= bindparam("tick"),
                    section_versions.c.end_tick > bindparam("tick"),
                    section_versions.c.section_x.between(
                        bindparam("min_section_x"),
                        bindparam("max_section_x"),
                    ),
                    section_versions.c.section_y.between(
                        bindparam("min_section_y"),
                        bindparam("max_section_y"),
                    ),
                    section_versions.c.section_z.between(
                        bindparam("min_section_z"),
                        bindparam("max_section_z"),
                    ),
                )
            )
            .order_by(
                section_versions.c.section_y,
                section_versions.c.section_z,
                section_versions.c.section_x,
            ),
            {
                "dimension": frame.dimension,
                "tick": frame.tick,
                "min_section_x": min_section[0],
                "max_section_x": max_section[0],
                "min_section_y": min_section[1],
                "max_section_y": max_section[1],
                "min_section_z": min_section[2],
                "max_section_z": max_section[2],
            },
        )
        for row in rows:
            section_palette, section_indices = _decode_section_blob(_blob_value(self._connection, row["blob_sha256"], "section"))
            translated = []
            for state in section_palette:
                key = canonical_json_blob(_thaw_json(state))
                index = palette_lookup.get(key)
                if index is None:
                    index = len(palette_values)
                    palette_lookup[key] = index
                    palette_values.append(state)
                translated.append(index)
            section_origin = (
                row["section_x"] * SECTION_EDGE,
                row["section_y"] * SECTION_EDGE,
                row["section_z"] * SECTION_EDGE,
            )
            low = tuple(max(origin[index], section_origin[index]) for index in range(3))
            high = tuple(min(origin[index] + shape[index], section_origin[index] + SECTION_EDGE) for index in range(3))
            for world_y in range(low[1], high[1]):
                local_y = world_y - section_origin[1]
                crop_y = world_y - origin[1]
                for world_z in range(low[2], high[2]):
                    local_z = world_z - section_origin[2]
                    crop_z = world_z - origin[2]
                    for world_x in range(low[0], high[0]):
                        local_x = world_x - section_origin[0]
                        crop_x = world_x - origin[0]
                        section_offset = (local_y * SECTION_EDGE + local_z) * SECTION_EDGE + local_x
                        crop_offset = (crop_y * shape[2] + crop_z) * shape[0] + crop_x
                        indices[crop_offset] = translated[section_indices[section_offset]]
                        coverage[crop_offset] = True
        return SceneCrop(
            tick=frame.tick,
            dimension=frame.dimension,
            origin=origin,
            shape=shape,
            palette=tuple(palette_values),
            indices=tuple(indices),
            coverage=tuple(coverage),
            entities=self._entities_in_box(frame.tick, frame.dimension, origin, shape),
            block_entities=self._block_entities_in_box(frame.tick, frame.dimension, origin, shape),
            coverage_complete=frame.coverage_complete and all(coverage),
        )

    def slice(
        self,
        tick_or_frame_id: int | str,
        axis: str,
        coordinate: int,
        center: tuple[int, int, int],
        radius: int,
    ) -> SceneSlice:
        if axis not in {"x", "y", "z"}:
            raise SceneStoreV2Error("scene slice axis must be x, y, or z")
        if len(center) != 3 or any(isinstance(value, bool) or not isinstance(value, int) for value in center):
            raise SceneStoreV2Error("scene slice center must contain three integers")
        if isinstance(coordinate, bool) or not isinstance(coordinate, int):
            raise SceneStoreV2Error("scene slice coordinate must be an integer")
        if isinstance(radius, bool) or not isinstance(radius, int) or not 0 <= radius <= MAX_SLICE_RADIUS:
            raise SceneStoreV2Error(f"scene slice radius must be an integer from 0 to {MAX_SLICE_RADIUS}")
        if not SQLITE_INTEGER_MIN + MAX_SLICE_RADIUS <= coordinate <= SQLITE_INTEGER_MAX - MAX_SLICE_RADIUS - 1:
            raise SceneStoreV2Error("scene slice coordinate is outside the supported world range")
        if any(not SQLITE_INTEGER_MIN + MAX_SLICE_RADIUS <= value <= SQLITE_INTEGER_MAX - MAX_SLICE_RADIUS - 1 for value in center):
            raise SceneStoreV2Error("scene slice center is outside the supported world range")
        diameter = radius * 2 + 1
        if axis == "x":
            origin = (coordinate, center[1] - radius, center[2] - radius)
            shape = (1, diameter, diameter)
            row_axis, column_axis = "y", "z"
            row_origin, column_origin = origin[1], origin[2]
        elif axis == "y":
            origin = (center[0] - radius, coordinate, center[2] - radius)
            shape = (diameter, 1, diameter)
            row_axis, column_axis = "z", "x"
            row_origin, column_origin = origin[2], origin[0]
        else:
            origin = (center[0] - radius, center[1] - radius, coordinate)
            shape = (diameter, diameter, 1)
            row_axis, column_axis = "y", "x"
            row_origin, column_origin = origin[1], origin[0]
        crop = self.materialize_crop(tick_or_frame_id, origin, shape)
        cells: list[SceneSliceCell] = []
        for row in range(diameter):
            for column in range(diameter):
                if axis == "x":
                    local = (0, row, column)
                    world = (coordinate, row_origin + row, column_origin + column)
                elif axis == "y":
                    local = (column, 0, row)
                    world = (column_origin + column, coordinate, row_origin + row)
                else:
                    local = (column, row, 0)
                    world = (column_origin + column, row_origin + row, coordinate)
                covered, block_state = crop.cell(*local)
                cells.append(
                    SceneSliceCell(
                        world_position=world,
                        covered=covered,
                        block_state=block_state,
                    )
                )
        return SceneSlice(
            tick=crop.tick,
            dimension=crop.dimension,
            axis=axis,
            coordinate=coordinate,
            row_axis=row_axis,
            column_axis=column_axis,
            row_origin=row_origin,
            column_origin=column_origin,
            width=diameter,
            height=diameter,
            cells=tuple(cells),
            entities=crop.entities,
            block_entities=crop.block_entities,
            coverage_complete=crop.coverage_complete,
        )


def _thaw_json(value: Any) -> Any:  # noqa: ANN401
    if isinstance(value, Mapping):
        return {str(key): _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


__all__ = [
    "PlayerStateV2",
    "SceneFrameAlignment",
    "SceneStoreV2",
    "SceneStoreV2Error",
    "SceneStoreV2Info",
    "SceneStoreV2ValidationError",
    "SceneTrajectoryPoint",
    "create_sqlite_scene_schema",
    "finalize_scene_store_v2",
    "validate_scene_store_v2",
]
