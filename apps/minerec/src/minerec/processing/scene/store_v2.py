"""Portable Scene Store V2 writer, validator, and read-only SQLite adapter."""

from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
import tempfile
import zlib
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType, TracebackType
from typing import Any, Iterable, Iterator, Mapping, Sequence
from urllib.parse import quote

from sqlalchemy import create_engine, insert
from sqlalchemy.engine import URL, Connection

from minerec.processing.scene.schema_v2 import (
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
    canonical_json_blob,
    validate_scene_store,
)

MAX_STATE_JSONL_LINE_BYTES = 64 * 1024 * 1024
MAX_STATE_READ_ROWS = 100_000


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
    """Create the portable base schema plus SQLite-only R-tree adapters."""

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


def _blob_value(connection: sqlite3.Connection, digest: str, expected_kind: str) -> bytes:
    row = connection.execute(
        "SELECT kind, encoding, uncompressed_size, compressed_size, data FROM blobs WHERE sha256 = ?",
        (digest,),
    ).fetchone()
    if row is None:
        raise SceneStoreV2ValidationError(f"missing {expected_kind} blob {digest}")
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
    position = _vector(record.get("position"), "player_state position")
    velocity = _vector(record.get("velocity"), "player_state velocity")
    rotation = _rotation(record.get("rotation"))
    for name, expected in (
        ("abilities", dict),
        ("effects", list),
        ("passengers", list),
        ("inventory", list),
    ):
        if not isinstance(record.get(name), expected):
            raise SceneStoreV2Error(f"player_state {name} must be a {expected.__name__}")
    vehicle = record.get("vehicle")
    if vehicle is not None and not isinstance(vehicle, dict):
        raise SceneStoreV2Error("player_state vehicle must be an object or null")
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


def _canonical_meta_json(value: object, description: str, expected_type: type[Any]) -> bytes:
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
    return _canonical_json(parsed, description)


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
    entity_uuid = payload.get("uuid")
    if entity_uuid is not None and entity_uuid != identity.player_uuid:
        raise SceneStoreV2Error(f"player_state at tick {tick} links to entity UUID {entity_uuid!r}, not the subject player")
    if entity_uuid is None and selected["type_id"] != "minecraft:player":
        raise SceneStoreV2Error(f"player_state at tick {tick} links to a non-player entity without a matching UUID")
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
    source_replays_json = _canonical_meta_json(
        meta["source_replays_json"],
        "Scene V1 source_replays_json",
        list,
    )
    provenance_json = _canonical_meta_json(
        meta["provenance_json"],
        "Scene V1 provenance_json",
        dict,
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
            source_replays_json=source_replays_json,
            sensitive=bool(meta["sensitive"]),
            provenance_json=provenance_json,
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
) -> SceneStoreV2Info:
    """Build and atomically publish an immutable V2 store from verified V1 inputs."""

    source_path = Path(scene_v1_path)
    states_path = Path(states_jsonl_path)
    output = Path(output_path)
    _check_regular_file(source_path, "Scene V1 store")
    source_before = _path_identity(source_path)
    source_info = validate_scene_store(source_path)
    if not source_info.coverage_complete:
        raise SceneStoreV2Error("Scene V1 coverage is incomplete; refusing V2 publication")
    states_by_tick, states_identity, states_sha256 = _read_authoritative_states(
        states_path,
        source_info.identity,
    )
    if not output.parent.is_dir() or output.parent.is_symlink():
        raise SceneStoreV2Error(f"Scene V2 parent is not a safe directory: {output.parent}")
    try:
        output.lstat()
    except FileNotFoundError:
        pass
    else:
        raise SceneStoreV2Error(f"refusing to replace immutable Scene V2 output: {output}")

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
        try:
            os.link(temporary, output)
        except FileExistsError as exc:
            raise SceneStoreV2Error(f"refusing to replace immutable Scene V2 output: {output}") from exc
        temporary.unlink()
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
    row = connection.execute("SELECT * FROM scene_meta WHERE singleton = 1").fetchone()
    if row is None:
        raise SceneStoreV2ValidationError("Scene V2 metadata is missing")
    return row


def _validate_no_overlap(
    connection: sqlite3.Connection,
    table: str,
    key_columns: Sequence[str],
    *,
    where: str | None = None,
    description: str | None = None,
) -> None:
    partition = ", ".join(key_columns)
    filter_sql = "" if where is None else f"WHERE {where}"
    overlap = connection.execute(
        "SELECT 1 FROM ("
        "SELECT start_tick, MAX(end_tick) OVER ("
        f"PARTITION BY {partition} "
        "ORDER BY start_tick, end_tick, version_id "
        "ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING"
        f") AS prior_max_end FROM {table} {filter_sql}"
        ") WHERE prior_max_end > start_tick LIMIT 1"
    ).fetchone()
    if overlap is not None:
        raise SceneStoreV2ValidationError(description or f"{table} contains overlapping intervals")


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


def _validate_player_state_row(connection: sqlite3.Connection, row: sqlite3.Row) -> None:
    value = _parse_canonical_json(
        _blob_value(connection, row["payload_sha256"], "player_state"),
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

    schema = connection.execute("SELECT * FROM schema_info WHERE singleton = 1").fetchone()
    if schema is None:
        raise SceneStoreV2ValidationError("Scene V2 schema_info is missing")
    if schema["schema_name"] != SCENE_STORE_V2_SCHEMA or schema["schema_version"] != SCENE_STORE_V2_SCHEMA_VERSION:
        raise SceneStoreV2ValidationError("Scene V2 schema identity is unsupported")
    meta = _meta_row(connection)
    identity = (meta["session_id"], meta["player_uuid"], meta["connection_id"])
    if any(not isinstance(value, str) or not value for value in identity):
        raise SceneStoreV2ValidationError("Scene V2 identity is incomplete")
    if meta["start_tick"] > meta["end_tick"]:
        raise SceneStoreV2ValidationError("Scene V2 tick bounds are reversed")
    _json_meta_value(meta["source_replays_json"], "Scene V2 source replays", list)
    _json_meta_value(meta["provenance_json"], "Scene V2 provenance", dict)

    frame_count = connection.execute("SELECT COUNT(*) FROM frames").fetchone()[0]
    state_count = connection.execute("SELECT COUNT(*) FROM player_states").fetchone()[0]
    if frame_count == 0:
        raise SceneStoreV2ValidationError("Scene V2 contains no frames")
    if state_count != frame_count:
        raise SceneStoreV2ValidationError("Scene V2 requires exactly one player_state per frame")
    coverage_gap = connection.execute("SELECT server_tick FROM frames WHERE coverage_complete = 0 LIMIT 1").fetchone()
    if coverage_gap is not None:
        raise SceneStoreV2ValidationError("Scene V2 contains an incomplete scene frame")
    missing_state = connection.execute("SELECT frames.server_tick FROM frames LEFT JOIN player_states ON player_states.server_tick = frames.server_tick WHERE player_states.server_tick IS NULL LIMIT 1").fetchone()
    if missing_state is not None:
        raise SceneStoreV2ValidationError("Scene V2 frame/player_state coverage is not one-to-one")
    invalid_frame = connection.execute(
        "SELECT server_tick FROM frames WHERE server_tick < ? OR server_tick > ? OR frame_id = '' OR dimension = '' LIMIT 1",
        (meta["start_tick"], meta["end_tick"]),
    ).fetchone()
    if invalid_frame is not None:
        raise SceneStoreV2ValidationError("Scene V2 contains an invalid frame")

    for table in ("section_versions", "entity_versions", "block_entity_versions"):
        invalid = connection.execute(
            f"SELECT version_id FROM {table} WHERE version_id <= 0 OR start_tick < ? OR end_tick > ? OR start_tick >= end_tick LIMIT 1",
            (meta["start_tick"], meta["end_tick"] + 1),
        ).fetchone()
        if invalid is not None:
            raise SceneStoreV2ValidationError(f"{table} contains an invalid explicit version interval")
    _validate_no_overlap(
        connection,
        "section_versions",
        ("dimension", "section_x", "section_y", "section_z"),
    )
    _validate_no_overlap(connection, "entity_versions", ("instance_id",))
    _validate_no_overlap(
        connection,
        "entity_versions",
        ("network_id",),
        where="network_id IS NOT NULL",
        description="Scene V2 contains overlapping entity network-id lifetimes",
    )
    _validate_no_overlap(
        connection,
        "block_entity_versions",
        ("dimension", "block_x", "block_y", "block_z"),
    )
    _validate_rtrees(connection)

    references = (
        ("frames", "payload_sha256", "frame"),
        ("player_states", "payload_sha256", "player_state"),
        ("section_versions", "blob_sha256", "section"),
        ("entity_versions", "blob_sha256", "entity"),
        ("block_entity_versions", "blob_sha256", "block_entity"),
    )
    for table, column, kind in references:
        wrong = connection.execute(
            f"SELECT 1 FROM {table} AS source JOIN blobs ON blobs.sha256 = source.{column} WHERE blobs.kind != ? LIMIT 1",
            (kind,),
        ).fetchone()
        if wrong is not None:
            raise SceneStoreV2ValidationError(f"{table} references a blob with the wrong kind")
    for blob in connection.execute("SELECT sha256, kind FROM blobs ORDER BY sha256"):
        value = _blob_value(connection, blob["sha256"], blob["kind"])
        if blob["kind"] == "section":
            _decode_section_blob(value)
        else:
            payload = _parse_canonical_json(value, f"{blob['kind']} blob {blob['sha256']}")
            if not isinstance(payload, dict):
                raise SceneStoreV2ValidationError(f"{blob['kind']} blob {blob['sha256']} must contain an object")

    state_rows = connection.execute(
        "SELECT player_states.*, "
        "frames.dimension AS frame_dimension, frames.subject_x, frames.subject_y, frames.subject_z, "
        "entity_versions.instance_id AS linked_instance_id, "
        "entity_versions.network_id AS linked_network_id, "
        "entity_versions.dimension AS linked_dimension, "
        "entity_versions.start_tick AS linked_start_tick, "
        "entity_versions.end_tick AS linked_end_tick "
        "FROM player_states "
        "JOIN frames ON frames.server_tick = player_states.server_tick "
        "JOIN entity_versions ON entity_versions.version_id = player_states.entity_version_id "
        "ORDER BY player_states.server_tick"
    )
    for row in state_rows:
        _validate_player_state_row(connection, row)
        payload = _parse_canonical_json(
            _blob_value(connection, row["payload_sha256"], "player_state"),
            f"player_state at tick {row['server_tick']}",
        )
        if (
            payload.get("session_id"),
            payload.get("player_uuid"),
            payload.get("connection_id"),
        ) != identity:
            raise SceneStoreV2ValidationError("player_state identity does not match Scene V2 metadata")
        if row["dimension"] != row["frame_dimension"] or (row["position_x"], row["position_y"], row["position_z"]) != (row["subject_x"], row["subject_y"], row["subject_z"]):
            raise SceneStoreV2ValidationError(f"player_state pose does not match its scene frame at tick {row['server_tick']}")
        if row["entity_instance_id"] != row["linked_instance_id"] or row["entity_id"] != row["linked_network_id"] or row["dimension"] != row["linked_dimension"] or not row["linked_start_tick"] <= row["server_tick"] < row["linked_end_tick"]:
            raise SceneStoreV2ValidationError(f"player_state entity link is invalid at tick {row['server_tick']}")


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
    ticks = tuple(row[0] for row in connection.execute("SELECT server_tick FROM frames ORDER BY server_tick"))
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
    """Fully validate a portable Scene Store V2 and its SQLite accelerators."""

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
            row = self._connection.execute(
                "SELECT * FROM frames WHERE server_tick = ?",
                (tick_or_frame_id,),
            ).fetchone()
        elif isinstance(tick_or_frame_id, str) and tick_or_frame_id:
            row = self._connection.execute(
                "SELECT * FROM frames WHERE frame_id = ?",
                (tick_or_frame_id,),
            ).fetchone()
        else:
            raise SceneStoreV2Error("scene frame key must be an integer tick or frame id")
        if row is None:
            raise SceneStoreV2Error(f"scene frame not found: {tick_or_frame_id}")
        return row

    def frame(self, tick_or_frame_id: int | str) -> SceneFrame:
        assert self._connection is not None
        return _frame_from_row(self._connection, self._frame_row(tick_or_frame_id))

    def player_state(self, tick_or_frame_id: int | str) -> PlayerStateV2:
        assert self._connection is not None
        tick = self._frame_row(tick_or_frame_id)["server_tick"]
        row = self._connection.execute(
            "SELECT * FROM player_states WHERE server_tick = ?",
            (tick,),
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
        rows = self._connection.execute(
            "SELECT * FROM player_states WHERE server_tick BETWEEN ? AND ? ORDER BY server_tick LIMIT ?",
            (lower, upper, limit + 1),
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
            blob = self._connection.execute(
                "SELECT kind, uncompressed_size FROM blobs WHERE sha256 = ?",
                (row["blob_sha256"],),
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
        rows = self._connection.execute(
            "SELECT * FROM section_versions WHERE dimension = ? AND start_tick <= ? AND end_tick > ? AND section_x BETWEEN ? AND ? AND section_y BETWEEN ? AND ? AND section_z BETWEEN ? AND ? ORDER BY section_y, section_z, section_x",
            (
                frame.dimension,
                frame.tick,
                frame.tick,
                min_section[0],
                max_section[0],
                min_section[1],
                max_section[1],
                min_section[2],
                max_section[2],
            ),
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
    "SceneStoreV2",
    "SceneStoreV2Error",
    "SceneStoreV2Info",
    "SceneStoreV2ValidationError",
    "SceneTrajectoryPoint",
    "create_sqlite_scene_schema",
    "finalize_scene_store_v2",
    "validate_scene_store_v2",
]
