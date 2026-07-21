from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import sqlite3
import struct
import tempfile
import zlib
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import quote

from .errors import RecorderError
from .render_sources import FLASHBACK_CAPTURE_CONTRACT
from .scene_integrity import (
    SceneStreamIntegrityError,
    VerifiedSceneStream,
    verify_scene_stream,
)


SCENE_STORE_SCHEMA = "mc-recorder-scene-store-v1"
SCENE_STREAM_SCHEMA = "mc-recorder-scene-stream-v1"
SCENE_EXTRACTION_RESULT_TYPE = "mc-recorder-scene-extraction-result-v1"
SCENE_SCOPE_CLIENT_VISIBLE = "client_visible"
SCENE_METADATA_POLICY_FULL = "full_packet_metadata"
SCENE_STORE_USER_VERSION = 1
SECTION_EDGE = 16
SECTION_CELL_COUNT = SECTION_EDGE**3
SECTION_INDEX_ORDER = "x_fastest_then_z_then_y"
DEFAULT_MAX_CROP_CELLS = 2_000_000
MAX_SLICE_RADIUS = 64
MAX_BLOB_UNCOMPRESSED_BYTES = 64 * 1024 * 1024
MAX_JSONL_LINE_BYTES = 64 * 1024 * 1024
UNKNOWN_PALETTE_INDEX = -1

_SOURCE_REPLAY_FIELDS = frozenset(
    {"segment_id", "segment_ordinal", "path", "sha256", "size_bytes", "format"}
)
_EXTRACTION_PROVENANCE_FIELDS = frozenset(
    {"scope", "metadata_policy", "result"}
)
_EXTRACTION_RESULT_FIELDS = frozenset(
    {
        "schema_version",
        "result_type",
        "status",
        "job_id",
        "session_id",
        "player_uuid",
        "connection_id",
        "global_start_tick",
        "global_end_tick",
        "scope",
        "metadata_policy",
        "flashback_capture_contract",
        "source_replays",
        "subject_poses",
        "stream",
        "ignored_packet_counts",
        "covered_tick_count",
    }
)
_SUBJECT_POSE_FIELDS = frozenset(
    {
        "format",
        "path",
        "sha256",
        "size_bytes",
        "record_count",
        "first_tick",
        "last_tick",
        "source_epochs",
    }
)
_SUBJECT_POSE_EPOCH_FIELDS = frozenset(
    {"epoch_index", "events_sha256", "events_size_bytes", "record_count"}
)
_EXTRACTION_STREAM_FIELDS = frozenset(
    {
        "format",
        "path",
        "frames_index",
        "changes_index",
        "blobs_directory",
        "frame_count",
        "change_count",
        "blob_count",
        "blob_bytes",
        "frames_sha256",
        "frames_size_bytes",
        "changes_sha256",
        "changes_size_bytes",
    }
)


class SceneStoreError(RecorderError):
    """A deterministic scene-store contract or integrity failure."""


class SceneStoreValidationError(SceneStoreError):
    """A scene store failed its persisted integrity contract."""


@dataclass(frozen=True)
class SceneIdentity:
    session_id: str
    player_uuid: str
    connection_id: str


@dataclass(frozen=True)
class SceneExtractionProvenance:
    scope: str
    metadata_policy: str
    result: Mapping[str, Any]


@dataclass(frozen=True)
class SceneStoreInfo:
    identity: SceneIdentity
    start_tick: int
    end_tick: int
    frame_count: int
    ticks: tuple[int, ...]
    coverage_complete: bool
    source_replays: tuple[Mapping[str, Any], ...]
    extraction: SceneExtractionProvenance | None
    sensitive: bool
    sha256: str
    size_bytes: int


@dataclass(frozen=True)
class SceneFrame:
    frame_id: str
    tick: int
    replay_tick: int | None
    dimension: str
    subject_position: tuple[float, float, float]
    coverage_complete: bool
    reasons: tuple[str, ...]
    metadata: Mapping[str, Any]


@dataclass(frozen=True)
class SceneEntity:
    instance_id: str
    dimension: str
    type_id: str
    network_id: int | None
    uuid: str | None
    position: tuple[float, float, float]
    velocity: tuple[float, float, float] | None
    rotation: tuple[float, float] | None
    aabb: tuple[float, float, float, float, float, float]
    payload: Mapping[str, Any]


@dataclass(frozen=True)
class SceneBlockEntity:
    dimension: str
    position: tuple[int, int, int]
    type_id: str
    payload: Mapping[str, Any]


@dataclass(frozen=True)
class SceneCrop:
    tick: int
    dimension: str
    origin: tuple[int, int, int]
    shape: tuple[int, int, int]
    palette: tuple[Any, ...]
    indices: tuple[int, ...]
    coverage: tuple[bool, ...]
    entities: tuple[SceneEntity, ...]
    block_entities: tuple[SceneBlockEntity, ...]
    coverage_complete: bool

    def cell(self, x: int, y: int, z: int) -> tuple[bool, Any | None]:
        sx, sy, sz = self.shape
        if not (0 <= x < sx and 0 <= y < sy and 0 <= z < sz):
            raise IndexError("scene crop coordinate is outside the crop")
        offset = (y * sz + z) * sx + x
        palette_index = self.indices[offset]
        return self.coverage[offset], (
            None if palette_index == UNKNOWN_PALETTE_INDEX else self.palette[palette_index]
        )


@dataclass(frozen=True)
class SceneSliceCell:
    world_position: tuple[int, int, int]
    covered: bool
    block_state: Any | None


@dataclass(frozen=True)
class SceneSlice:
    tick: int
    dimension: str
    axis: str
    coordinate: int
    row_axis: str
    column_axis: str
    row_origin: int
    column_origin: int
    width: int
    height: int
    cells: tuple[SceneSliceCell, ...]
    entities: tuple[SceneEntity, ...]
    block_entities: tuple[SceneBlockEntity, ...]
    coverage_complete: bool


def _canonical_json_bytes(value: Any, *, description: str) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise SceneStoreError(f"{description} is not canonical JSON: {exc}") from exc


def canonical_json_blob(value: Any) -> bytes:
    """Encode a JSON-compatible value using the scene-store canonical form."""

    return _canonical_json_bytes(value, description="scene payload")


def _parse_canonical_json(data: bytes, *, description: str) -> Any:
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SceneStoreValidationError(f"{description} is not valid UTF-8 JSON") from exc
    try:
        encoded = _canonical_json_bytes(value, description=description)
    except SceneStoreError as exc:
        raise SceneStoreValidationError(str(exc)) from exc
    if encoded != data:
        raise SceneStoreValidationError(f"{description} is not canonically encoded")
    return value


def _freeze_json(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({str(key): _freeze_json(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    return value


def _thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


def canonical_section_blob(palette: Sequence[Any], indices: Sequence[int]) -> bytes:
    """Encode a canonical 16x16x16 palette section.

    Indices use ``x_fastest_then_z_then_y`` order and are serialized as
    unsigned little-endian 16-bit integers.
    """

    if len(indices) != SECTION_CELL_COUNT:
        raise SceneStoreError(
            f"section indices must contain exactly {SECTION_CELL_COUNT} cells"
        )
    if not palette:
        raise SceneStoreError("section palette cannot be empty")

    encoded_states = [
        _canonical_json_bytes(state, description=f"section palette entry {index}")
        for index, state in enumerate(palette)
    ]
    unique_states = sorted(set(encoded_states))
    if len(unique_states) > 65_535:
        raise SceneStoreError("section palette exceeds the uint16 index limit")
    canonical_index = {state: index for index, state in enumerate(unique_states)}
    remap = [canonical_index[state] for state in encoded_states]
    packed = bytearray(SECTION_CELL_COUNT * 2)
    for offset, source_index in enumerate(indices):
        if isinstance(source_index, bool) or not isinstance(source_index, int):
            raise SceneStoreError(f"section index {offset} is not an integer")
        if not 0 <= source_index < len(palette):
            raise SceneStoreError(
                f"section index {offset} references missing palette entry {source_index}"
            )
        struct.pack_into("<H", packed, offset * 2, remap[source_index])
    canonical_palette = [json.loads(item.decode("utf-8")) for item in unique_states]
    envelope = {
        "format": "mc-recorder-section-v1",
        "index_order": SECTION_INDEX_ORDER,
        "indices_le_u16_b64": base64.b64encode(packed).decode("ascii"),
        "palette": canonical_palette,
        "shape": [SECTION_EDGE, SECTION_EDGE, SECTION_EDGE],
    }
    return _canonical_json_bytes(envelope, description="section blob")


def _decode_section_blob(data: bytes) -> tuple[tuple[Any, ...], tuple[int, ...]]:
    value = _parse_canonical_json(data, description="section blob")
    if not isinstance(value, dict):
        raise SceneStoreValidationError("section blob root must be an object")
    if value.get("format") != "mc-recorder-section-v1":
        raise SceneStoreValidationError("section blob has an unsupported format")
    if value.get("index_order") != SECTION_INDEX_ORDER:
        raise SceneStoreValidationError("section blob has an unsupported index order")
    if value.get("shape") != [SECTION_EDGE, SECTION_EDGE, SECTION_EDGE]:
        raise SceneStoreValidationError("section blob must have shape [16,16,16]")
    palette = value.get("palette")
    encoded_indices = value.get("indices_le_u16_b64")
    if not isinstance(palette, list) or not palette:
        raise SceneStoreValidationError("section blob palette must be a non-empty array")
    if len(palette) > 65_535:
        raise SceneStoreValidationError("section blob palette exceeds the uint16 limit")
    if not isinstance(encoded_indices, str):
        raise SceneStoreValidationError("section blob indices must be base64 text")
    try:
        packed = base64.b64decode(encoded_indices, validate=True)
    except (ValueError, base64.binascii.Error) as exc:
        raise SceneStoreValidationError("section blob indices are not valid base64") from exc
    if len(packed) != SECTION_CELL_COUNT * 2:
        raise SceneStoreValidationError("section blob indices have the wrong byte length")
    indices = tuple(value[0] for value in struct.iter_unpack("<H", packed))
    if any(index >= len(palette) for index in indices):
        raise SceneStoreValidationError("section blob references a missing palette entry")
    try:
        recoded = canonical_section_blob(palette, indices)
    except SceneStoreError as exc:
        raise SceneStoreValidationError(str(exc)) from exc
    if recoded != data:
        raise SceneStoreValidationError("section blob palette is not locally canonical")
    return tuple(_freeze_json(item) for item in palette), indices


def _required_text(value: Any, description: str) -> str:
    if not isinstance(value, str) or not value:
        raise SceneStoreError(f"{description} must be non-empty text")
    return value


def _required_int(value: Any, description: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise SceneStoreError(f"{description} must be an integer")
    return value


def _required_sha256(value: Any, description: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise SceneStoreError(f"{description} must be lowercase SHA-256 text")
    return value


def _finite_number(value: Any, description: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SceneStoreError(f"{description} must be a number")
    result = float(value)
    if not math.isfinite(result):
        raise SceneStoreError(f"{description} must be finite")
    return result


def _float_tuple(value: Any, length: int, description: str) -> tuple[float, ...]:
    if not isinstance(value, (list, tuple)) or len(value) != length:
        raise SceneStoreError(f"{description} must contain exactly {length} numbers")
    return tuple(
        _finite_number(item, f"{description}[{index}]")
        for index, item in enumerate(value)
    )


def _entity_fields(
    instance_id: str, payload: Mapping[str, Any]
) -> tuple[
    str,
    str,
    int | None,
    str | None,
    tuple[float, float, float],
    tuple[float, float, float] | None,
    tuple[float, float] | None,
    tuple[float, float, float, float, float, float],
]:
    _required_text(instance_id, "entity instance_id")
    embedded_id = payload.get("instance_id")
    if embedded_id is not None and embedded_id != instance_id:
        raise SceneStoreError("entity payload instance_id does not match its event")
    dimension = _required_text(payload.get("dimension"), "entity dimension")
    type_id = _required_text(
        payload.get("type_id", payload.get("type")), "entity type_id"
    )
    network_value = payload.get("network_id")
    network_id = (
        None
        if network_value is None
        else _required_int(network_value, "entity network_id")
    )
    uuid_value = payload.get("uuid")
    uuid = None if uuid_value is None else _required_text(uuid_value, "entity uuid")
    position = _float_tuple(payload.get("position"), 3, "entity position")
    velocity_value = payload.get("velocity")
    velocity = (
        None
        if velocity_value is None
        else _float_tuple(velocity_value, 3, "entity velocity")
    )
    rotation_value = payload.get("rotation")
    rotation = (
        None
        if rotation_value is None
        else _float_tuple(rotation_value, 2, "entity rotation")
    )
    aabb = _float_tuple(payload.get("aabb"), 6, "entity aabb")
    if aabb[3] < aabb[0] or aabb[4] < aabb[1] or aabb[5] < aabb[2]:
        raise SceneStoreError("entity aabb maximums must not precede minimums")
    return (
        dimension,
        type_id,
        network_id,
        uuid,
        position,  # type: ignore[return-value]
        velocity,  # type: ignore[return-value]
        rotation,  # type: ignore[return-value]
        aabb,  # type: ignore[return-value]
    )


def _block_entity_type(payload: Mapping[str, Any]) -> str:
    return _required_text(
        payload.get("type_id", payload.get("type")), "block entity type_id"
    )


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _decompress_bounded(data: bytes, *, expected_size: int, description: str) -> bytes:
    if expected_size < 0 or expected_size > MAX_BLOB_UNCOMPRESSED_BYTES:
        raise SceneStoreValidationError(
            f"{description} uncompressed size is outside the allowed bounds"
        )
    inflater = zlib.decompressobj()
    try:
        value = inflater.decompress(data, expected_size + 1)
        if len(value) > expected_size or inflater.unconsumed_tail:
            raise SceneStoreValidationError(
                f"{description} expands beyond its declared size"
            )
        value += inflater.flush()
    except zlib.error as exc:
        raise SceneStoreValidationError(f"{description} is not valid zlib data") from exc
    if not inflater.eof or inflater.unused_data:
        raise SceneStoreValidationError(f"{description} has an invalid zlib boundary")
    if len(value) != expected_size:
        raise SceneStoreValidationError(
            f"{description} size does not match its declared size"
        )
    return value


def _reject_symlink(path: Path, *, description: str, must_exist: bool) -> None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        if must_exist:
            raise SceneStoreError(f"{description} does not exist: {path}")
        return
    if path.is_symlink():
        raise SceneStoreError(f"refusing symlinked {description}: {path}")
    if must_exist and not path.is_file():
        raise SceneStoreError(f"{description} is not a regular file: {path}")
    if not must_exist and not path.is_file():
        raise SceneStoreError(f"existing {description} is not a regular file: {path}")


_SCHEMA_SQL = """
PRAGMA user_version = 1;
CREATE TABLE scene_meta (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    schema_name TEXT NOT NULL,
    session_id TEXT NOT NULL,
    player_uuid TEXT NOT NULL,
    connection_id TEXT NOT NULL,
    start_tick INTEGER NOT NULL,
    end_tick INTEGER NOT NULL,
    source_replays_json TEXT NOT NULL,
    sensitive INTEGER NOT NULL CHECK (sensitive IN (0, 1)),
    provenance_json TEXT NOT NULL
);
CREATE TABLE blobs (
    sha256 TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('section', 'entity', 'block_entity', 'frame')),
    uncompressed_size INTEGER NOT NULL CHECK (uncompressed_size >= 0),
    compressed_size INTEGER NOT NULL CHECK (compressed_size >= 0),
    zlib_data BLOB NOT NULL
);
CREATE TABLE frames (
    server_tick INTEGER PRIMARY KEY,
    frame_id TEXT NOT NULL UNIQUE,
    replay_tick INTEGER,
    dimension TEXT NOT NULL,
    subject_x REAL NOT NULL,
    subject_y REAL NOT NULL,
    subject_z REAL NOT NULL,
    coverage_complete INTEGER NOT NULL CHECK (coverage_complete IN (0, 1)),
    payload_sha256 TEXT NOT NULL REFERENCES blobs(sha256)
);
CREATE TABLE section_versions (
    dimension TEXT NOT NULL,
    section_x INTEGER NOT NULL,
    section_y INTEGER NOT NULL,
    section_z INTEGER NOT NULL,
    start_tick INTEGER NOT NULL,
    end_tick INTEGER NOT NULL,
    blob_sha256 TEXT NOT NULL REFERENCES blobs(sha256),
    PRIMARY KEY (dimension, section_x, section_y, section_z, start_tick),
    CHECK (start_tick < end_tick)
);
CREATE INDEX section_versions_tick_idx
    ON section_versions(dimension, start_tick, end_tick);
CREATE TABLE entity_versions (
    instance_id TEXT NOT NULL,
    network_id INTEGER,
    dimension TEXT NOT NULL,
    type_id TEXT NOT NULL,
    start_tick INTEGER NOT NULL,
    end_tick INTEGER NOT NULL,
    min_x REAL NOT NULL,
    min_y REAL NOT NULL,
    min_z REAL NOT NULL,
    max_x REAL NOT NULL,
    max_y REAL NOT NULL,
    max_z REAL NOT NULL,
    blob_sha256 TEXT NOT NULL REFERENCES blobs(sha256),
    PRIMARY KEY (instance_id, start_tick),
    CHECK (start_tick < end_tick),
    CHECK (min_x <= max_x AND min_y <= max_y AND min_z <= max_z)
);
CREATE INDEX entity_versions_tick_idx
    ON entity_versions(dimension, start_tick, end_tick);
CREATE INDEX entity_versions_network_interval_idx
    ON entity_versions(network_id, start_tick, end_tick, instance_id)
    WHERE network_id IS NOT NULL;
CREATE VIRTUAL TABLE entity_versions_rtree USING rtree(
    version_rowid,
    min_tick, max_tick,
    min_x, max_x,
    min_y, max_y,
    min_z, max_z
);
CREATE TRIGGER entity_versions_rtree_insert AFTER INSERT ON entity_versions BEGIN
    INSERT INTO entity_versions_rtree VALUES (
        new.rowid,
        new.start_tick, new.end_tick,
        new.min_x, new.max_x,
        new.min_y, new.max_y,
        new.min_z, new.max_z
    );
END;
CREATE TRIGGER entity_versions_rtree_delete AFTER DELETE ON entity_versions BEGIN
    DELETE FROM entity_versions_rtree WHERE version_rowid = old.rowid;
END;
CREATE TRIGGER entity_versions_rtree_update
AFTER UPDATE OF start_tick, end_tick, min_x, max_x, min_y, max_y, min_z, max_z
ON entity_versions BEGIN
    DELETE FROM entity_versions_rtree WHERE version_rowid = old.rowid;
    INSERT INTO entity_versions_rtree VALUES (
        new.rowid,
        new.start_tick, new.end_tick,
        new.min_x, new.max_x,
        new.min_y, new.max_y,
        new.min_z, new.max_z
    );
END;
CREATE TABLE block_entity_versions (
    dimension TEXT NOT NULL,
    block_x INTEGER NOT NULL,
    block_y INTEGER NOT NULL,
    block_z INTEGER NOT NULL,
    type_id TEXT NOT NULL,
    start_tick INTEGER NOT NULL,
    end_tick INTEGER NOT NULL,
    blob_sha256 TEXT NOT NULL REFERENCES blobs(sha256),
    PRIMARY KEY (dimension, block_x, block_y, block_z, start_tick),
    CHECK (start_tick < end_tick)
);
CREATE INDEX block_entity_versions_tick_idx
    ON block_entity_versions(dimension, start_tick, end_tick);
CREATE VIRTUAL TABLE block_entity_versions_rtree USING rtree(
    version_rowid,
    min_tick, max_tick,
    min_x, max_x,
    min_y, max_y,
    min_z, max_z
);
CREATE TRIGGER block_entity_versions_rtree_insert
AFTER INSERT ON block_entity_versions BEGIN
    INSERT INTO block_entity_versions_rtree VALUES (
        new.rowid,
        new.start_tick, new.end_tick,
        new.block_x, new.block_x + 1,
        new.block_y, new.block_y + 1,
        new.block_z, new.block_z + 1
    );
END;
CREATE TRIGGER block_entity_versions_rtree_delete
AFTER DELETE ON block_entity_versions BEGIN
    DELETE FROM block_entity_versions_rtree WHERE version_rowid = old.rowid;
END;
CREATE TRIGGER block_entity_versions_rtree_update
AFTER UPDATE OF start_tick, end_tick, block_x, block_y, block_z
ON block_entity_versions BEGIN
    DELETE FROM block_entity_versions_rtree WHERE version_rowid = old.rowid;
    INSERT INTO block_entity_versions_rtree VALUES (
        new.rowid,
        new.start_tick, new.end_tick,
        new.block_x, new.block_x + 1,
        new.block_y, new.block_y + 1,
        new.block_z, new.block_z + 1
    );
END;
"""


_ENTITY_BOX_SQL = """
SELECT source.*
FROM entity_versions_rtree AS search
CROSS JOIN entity_versions AS source
WHERE search.min_tick <= ? AND search.max_tick > ?
  AND search.max_x > ? AND search.min_x < ?
  AND search.max_y > ? AND search.min_y < ?
  AND search.max_z > ? AND search.min_z < ?
  AND source.rowid = search.version_rowid
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
  AND source.rowid = search.version_rowid
  AND source.dimension = ?
  AND source.start_tick <= ? AND source.end_tick > ?
  AND source.block_x >= ? AND source.block_x < ?
  AND source.block_y >= ? AND source.block_y < ?
  AND source.block_z >= ? AND source.block_z < ?
ORDER BY source.block_y, source.block_z, source.block_x
"""


def _connect_read_only(path: Path) -> sqlite3.Connection:
    uri = f"file:{quote(str(path), safe='/')}?mode=ro&immutable=1"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _blob_value(
    connection: sqlite3.Connection, sha256: str, *, expected_kind: str
) -> bytes:
    row = connection.execute(
        "SELECT kind, uncompressed_size, compressed_size, zlib_data "
        "FROM blobs WHERE sha256 = ?",
        (sha256,),
    ).fetchone()
    if row is None:
        raise SceneStoreValidationError(f"referenced scene blob is missing: {sha256}")
    if row["kind"] != expected_kind:
        raise SceneStoreValidationError(
            f"scene blob {sha256} has kind {row['kind']}, expected {expected_kind}"
        )
    compressed = bytes(row["zlib_data"])
    if len(compressed) != row["compressed_size"]:
        raise SceneStoreValidationError(
            f"scene blob {sha256} compressed size does not match"
        )
    value = _decompress_bounded(
        compressed,
        expected_size=row["uncompressed_size"],
        description=f"scene blob {sha256}",
    )
    if _sha256_bytes(value) != sha256:
        raise SceneStoreValidationError(f"scene blob hash mismatch: {sha256}")
    return value


def _meta_row(connection: sqlite3.Connection) -> sqlite3.Row:
    row = connection.execute("SELECT * FROM scene_meta WHERE singleton = 1").fetchone()
    if row is None:
        raise SceneStoreValidationError("scene store metadata is missing")
    return row


def _json_object_from_text(value: str, description: str) -> dict[str, Any]:
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError as exc:
        raise SceneStoreValidationError(f"{description} is not valid JSON") from exc
    if not isinstance(decoded, dict):
        raise SceneStoreValidationError(f"{description} must be a JSON object")
    if _canonical_json_bytes(decoded, description=description).decode("utf-8") != value:
        raise SceneStoreValidationError(f"{description} is not canonically encoded")
    return decoded


def _json_array_from_text(value: str, description: str) -> list[Any]:
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError as exc:
        raise SceneStoreValidationError(f"{description} is not valid JSON") from exc
    if not isinstance(decoded, list):
        raise SceneStoreValidationError(f"{description} must be a JSON array")
    if _canonical_json_bytes(decoded, description=description).decode("utf-8") != value:
        raise SceneStoreValidationError(f"{description} is not canonically encoded")
    return decoded


def _validated_nonnegative_int(value: object, description: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise SceneStoreValidationError(
            f"{description} must be a non-negative integer"
        )
    return value


def _validated_source_replays(
    value: object, description: str = "scene source_replays"
) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, (list, tuple)) or not value:
        raise SceneStoreValidationError(
            f"{description} must be a non-empty array"
        )
    normalized: list[dict[str, Any]] = []
    segment_ids: set[str] = set()
    previous_ordinal = -1
    for index, raw in enumerate(value):
        context = f"{description}[{index}]"
        if not isinstance(raw, Mapping) or set(raw) != _SOURCE_REPLAY_FIELDS:
            raise SceneStoreValidationError(
                f"{context} fields do not match the source replay contract"
            )
        segment_id = _required_text(raw.get("segment_id"), f"{context} segment_id")
        ordinal = _validated_nonnegative_int(
            raw.get("segment_ordinal"), f"{context} segment_ordinal"
        )
        path = _required_text(raw.get("path"), f"{context} path")
        digest = _required_sha256(raw.get("sha256"), f"{context} sha256")
        size_bytes = _validated_nonnegative_int(
            raw.get("size_bytes"), f"{context} size_bytes"
        )
        if size_bytes == 0:
            raise SceneStoreValidationError(f"{context} size_bytes must be positive")
        if raw.get("format") != "flashback":
            raise SceneStoreValidationError(f"{context} format must be flashback")
        if ordinal <= previous_ordinal or segment_id in segment_ids:
            raise SceneStoreValidationError(
                f"{description} must have unique IDs and strictly increasing ordinals"
            )
        previous_ordinal = ordinal
        segment_ids.add(segment_id)
        normalized.append(
            {
                "segment_id": segment_id,
                "segment_ordinal": ordinal,
                "path": path,
                "sha256": digest,
                "size_bytes": size_bytes,
                "format": "flashback",
            }
        )
    return tuple(normalized)


def _validated_subject_poses(
    value: object,
    *,
    start_tick: int,
    end_tick: int,
    frame_count: int,
) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != _SUBJECT_POSE_FIELDS:
        raise SceneStoreValidationError(
            "scene extraction subject_poses fields do not match the contract"
        )
    if value.get("format") != "mc-recorder-subject-poses-v1":
        raise SceneStoreValidationError("scene extraction subject_poses format is unsupported")
    path_text = _required_text(value.get("path"), "scene extraction subject_poses path")
    path = Path(path_text)
    if not path.is_absolute() or path != path.resolve() or path.name != "subject-poses.jsonl":
        raise SceneStoreValidationError(
            "scene extraction subject_poses path must be an absolute normalized subject-poses.jsonl"
        )
    digest = _required_sha256(value.get("sha256"), "scene extraction subject_poses sha256")
    size_bytes = _validated_nonnegative_int(
        value.get("size_bytes"), "scene extraction subject_poses size_bytes"
    )
    record_count = _validated_nonnegative_int(
        value.get("record_count"), "scene extraction subject_poses record_count"
    )
    first_tick = _validated_nonnegative_int(
        value.get("first_tick"), "scene extraction subject_poses first_tick"
    )
    last_tick = _validated_nonnegative_int(
        value.get("last_tick"), "scene extraction subject_poses last_tick"
    )
    if (
        size_bytes == 0
        or record_count != frame_count
        or first_tick != start_tick
        or last_tick != end_tick
        or end_tick - start_tick + 1 != frame_count
    ):
        raise SceneStoreValidationError(
            "scene extraction subject_poses coverage does not match the store"
        )
    raw_epochs = value.get("source_epochs")
    if not isinstance(raw_epochs, (list, tuple)) or not raw_epochs:
        raise SceneStoreValidationError(
            "scene extraction subject_poses source_epochs must be a non-empty array"
        )
    epochs: list[dict[str, Any]] = []
    previous_index = -1
    for index, raw in enumerate(raw_epochs):
        context = f"scene extraction subject_poses source_epochs[{index}]"
        if not isinstance(raw, Mapping) or set(raw) != _SUBJECT_POSE_EPOCH_FIELDS:
            raise SceneStoreValidationError(f"{context} fields do not match the contract")
        epoch_index = _validated_nonnegative_int(raw.get("epoch_index"), f"{context} epoch_index")
        if epoch_index <= previous_index:
            raise SceneStoreValidationError(
                "scene extraction subject_poses source epochs must be strictly increasing"
            )
        previous_index = epoch_index
        events_sha256 = _required_sha256(raw.get("events_sha256"), f"{context} events_sha256")
        events_size_bytes = _validated_nonnegative_int(
            raw.get("events_size_bytes"), f"{context} events_size_bytes"
        )
        source_record_count = _validated_nonnegative_int(
            raw.get("record_count"), f"{context} record_count"
        )
        if events_size_bytes == 0 or source_record_count == 0:
            raise SceneStoreValidationError(f"{context} integrity counts must be positive")
        epochs.append(
            {
                "epoch_index": epoch_index,
                "events_sha256": events_sha256,
                "events_size_bytes": events_size_bytes,
                "record_count": source_record_count,
            }
        )
    return {
        "format": "mc-recorder-subject-poses-v1",
        "path": path_text,
        "sha256": digest,
        "size_bytes": size_bytes,
        "record_count": record_count,
        "first_tick": first_tick,
        "last_tick": last_tick,
        "source_epochs": epochs,
    }


def _validated_extraction_provenance(
    source_replays: object,
    provenance: object,
    *,
    identity: SceneIdentity,
    start_tick: int,
    end_tick: int,
    frame_count: int,
) -> SceneExtractionProvenance:
    sources = _validated_source_replays(source_replays)
    if not isinstance(provenance, Mapping) or set(provenance) != _EXTRACTION_PROVENANCE_FIELDS:
        raise SceneStoreValidationError(
            "scene extraction provenance fields do not match the contract"
        )
    scope = provenance.get("scope")
    metadata_policy = provenance.get("metadata_policy")
    if scope != SCENE_SCOPE_CLIENT_VISIBLE:
        raise SceneStoreValidationError(
            "scene extraction scope must be client_visible"
        )
    if metadata_policy != SCENE_METADATA_POLICY_FULL:
        raise SceneStoreValidationError(
            "scene extraction metadata_policy must be full_packet_metadata"
        )
    result = provenance.get("result")
    if not isinstance(result, Mapping) or set(result) != _EXTRACTION_RESULT_FIELDS:
        raise SceneStoreValidationError(
            "scene extraction result fields do not match the complete contract"
        )
    if (
        result.get("schema_version") != 1
        or isinstance(result.get("schema_version"), bool)
        or result.get("result_type") != SCENE_EXTRACTION_RESULT_TYPE
        or result.get("status") != "complete"
    ):
        raise SceneStoreValidationError(
            "scene extraction result contract is unsupported or incomplete"
        )
    expected_identity = {
        "session_id": identity.session_id,
        "player_uuid": identity.player_uuid,
        "connection_id": identity.connection_id,
        "global_start_tick": start_tick,
        "global_end_tick": end_tick,
    }
    if any(result.get(key) != expected for key, expected in expected_identity.items()):
        raise SceneStoreValidationError(
            "scene extraction result identity or tick range does not match the store"
        )
    _required_text(result.get("job_id"), "scene extraction result job_id")
    if (
        result.get("scope") != scope
        or result.get("metadata_policy") != metadata_policy
        or result.get("flashback_capture_contract") != FLASHBACK_CAPTURE_CONTRACT
    ):
        raise SceneStoreValidationError(
            "scene extraction result policy or capture contract does not match persisted provenance"
        )
    result_sources = _validated_source_replays(
        result.get("source_replays"), "scene extraction result source_replays"
    )
    if result_sources != sources:
        raise SceneStoreValidationError(
            "scene extraction result source replays do not match the store"
        )
    _validated_subject_poses(
        result.get("subject_poses"),
        start_tick=start_tick,
        end_tick=end_tick,
        frame_count=frame_count,
    )
    ignored = result.get("ignored_packet_counts")
    if not isinstance(ignored, Mapping) or any(
        not isinstance(name, str)
        or not name
        or not isinstance(count, int)
        or isinstance(count, bool)
        or count < 0
        for name, count in ignored.items()
    ):
        raise SceneStoreValidationError(
            "scene extraction result ignored_packet_counts is invalid"
        )
    if _validated_nonnegative_int(
        result.get("covered_tick_count"),
        "scene extraction result covered_tick_count",
    ) != frame_count:
        raise SceneStoreValidationError(
            "scene extraction result does not cover every stored frame"
        )
    stream = result.get("stream")
    if not isinstance(stream, Mapping) or set(stream) != _EXTRACTION_STREAM_FIELDS:
        raise SceneStoreValidationError(
            "scene extraction result stream fields do not match the contract"
        )
    if (
        stream.get("format") != SCENE_STREAM_SCHEMA
        or stream.get("frames_index") != "frames.jsonl"
        or stream.get("changes_index") != "changes.jsonl"
        or stream.get("blobs_directory") != "blobs"
    ):
        raise SceneStoreValidationError(
            "scene extraction result stream layout is unsupported"
        )
    _required_text(stream.get("path"), "scene extraction result stream path")
    for key in ("frames_sha256", "changes_sha256"):
        _required_sha256(stream.get(key), f"scene extraction result stream {key}")
    counts = {
        key: _validated_nonnegative_int(
            stream.get(key), f"scene extraction result stream {key}"
        )
        for key in (
            "frame_count",
            "change_count",
            "blob_count",
            "blob_bytes",
            "frames_size_bytes",
            "changes_size_bytes",
        )
    }
    if counts["frame_count"] != frame_count:
        raise SceneStoreValidationError(
            "scene extraction stream frame_count does not match the store"
        )
    if counts["frames_size_bytes"] == 0 or counts["changes_size_bytes"] == 0:
        raise SceneStoreValidationError(
            "scene extraction result stream indexes must be non-empty"
        )
    return SceneExtractionProvenance(
        scope=scope,
        metadata_policy=metadata_policy,
        result=_freeze_json(_thaw_json(result)),
    )


def validate_scene_attachment_provenance(
    info: SceneStoreInfo,
) -> SceneExtractionProvenance:
    """Require extraction-authenticated provenance before dataset attachment."""

    if info.extraction is None:
        raise SceneStoreValidationError(
            "scene store lacks authenticated extraction provenance"
        )
    return info.extraction


def _store_info(
    connection: sqlite3.Connection, path: Path, *, include_hash: bool
) -> SceneStoreInfo:
    meta = _meta_row(connection)
    ticks = tuple(
        row[0]
        for row in connection.execute("SELECT server_tick FROM frames ORDER BY server_tick")
    )
    source_replays = _json_array_from_text(
        meta["source_replays_json"], "scene source_replays"
    )
    provenance = _json_object_from_text(
        meta["provenance_json"], "scene provenance"
    )
    identity = SceneIdentity(
        session_id=meta["session_id"],
        player_uuid=meta["player_uuid"],
        connection_id=meta["connection_id"],
    )
    extraction = (
        None
        if not source_replays and not provenance
        else _validated_extraction_provenance(
            source_replays,
            provenance,
            identity=identity,
            start_tick=meta["start_tick"],
            end_tick=meta["end_tick"],
            frame_count=len(ticks),
        )
    )
    return SceneStoreInfo(
        identity=identity,
        start_tick=meta["start_tick"],
        end_tick=meta["end_tick"],
        frame_count=len(ticks),
        ticks=ticks,
        coverage_complete=connection.execute(
            "SELECT NOT EXISTS(SELECT 1 FROM frames WHERE coverage_complete = 0)"
        ).fetchone()[0]
        == 1,
        source_replays=tuple(_freeze_json(item) for item in source_replays),
        extraction=extraction,
        sensitive=bool(meta["sensitive"]),
        sha256=_sha256_file(path) if include_hash else "",
        size_bytes=path.stat().st_size if include_hash else 0,
    )


def _validate_interval_bounds(
    connection: sqlite3.Connection,
    table: str,
    start_tick: int,
    end_tick: int,
) -> None:
    bad = connection.execute(
        f"SELECT start_tick, end_tick FROM {table} "
        "WHERE start_tick < ? OR end_tick > ? OR start_tick >= end_tick LIMIT 1",
        (start_tick, end_tick + 1),
    ).fetchone()
    if bad is not None:
        raise SceneStoreValidationError(
            f"{table} contains an interval outside the store tick bounds"
        )


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
        "ORDER BY start_tick, end_tick, rowid "
        "ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING"
        f") AS prior_max_end FROM {table} {filter_sql}"
        ") WHERE prior_max_end > start_tick LIMIT 1"
    ).fetchone()
    if overlap is not None:
        raise SceneStoreValidationError(
            description or f"{table} contains overlapping intervals"
        )


def _validate_entity_rtree(connection: sqlite3.Connection) -> None:
    mismatched = connection.execute(
        "SELECT 1 FROM entity_versions AS source "
        "LEFT JOIN entity_versions_rtree AS search "
        "ON search.version_rowid = source.rowid "
        "WHERE search.version_rowid IS NULL "
        "OR search.min_tick > source.start_tick "
        "OR search.max_tick < source.end_tick "
        "OR search.min_x > source.min_x OR search.max_x < source.max_x "
        "OR search.min_y > source.min_y OR search.max_y < source.max_y "
        "OR search.min_z > source.min_z OR search.max_z < source.max_z "
        "LIMIT 1"
    ).fetchone()
    extra = connection.execute(
        "SELECT 1 FROM entity_versions_rtree AS search "
        "LEFT JOIN entity_versions AS source "
        "ON source.rowid = search.version_rowid "
        "WHERE source.rowid IS NULL LIMIT 1"
    ).fetchone()
    if mismatched is not None or extra is not None:
        raise SceneStoreValidationError(
            "entity temporal/spatial index does not match entity versions"
        )


def _validate_block_entity_rtree(connection: sqlite3.Connection) -> None:
    mismatched = connection.execute(
        "SELECT 1 FROM block_entity_versions AS source "
        "LEFT JOIN block_entity_versions_rtree AS search "
        "ON search.version_rowid = source.rowid "
        "WHERE search.version_rowid IS NULL "
        "OR search.min_tick > source.start_tick "
        "OR search.max_tick < source.end_tick "
        "OR search.min_x > source.block_x OR search.max_x < source.block_x + 1 "
        "OR search.min_y > source.block_y OR search.max_y < source.block_y + 1 "
        "OR search.min_z > source.block_z OR search.max_z < source.block_z + 1 "
        "LIMIT 1"
    ).fetchone()
    extra = connection.execute(
        "SELECT 1 FROM block_entity_versions_rtree AS search "
        "LEFT JOIN block_entity_versions AS source "
        "ON source.rowid = search.version_rowid "
        "WHERE source.rowid IS NULL LIMIT 1"
    ).fetchone()
    if mismatched is not None or extra is not None:
        raise SceneStoreValidationError(
            "block-entity temporal/spatial index does not match block-entity versions"
        )


def _validate_connection(connection: sqlite3.Connection, path: Path) -> None:
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    if version != SCENE_STORE_USER_VERSION:
        raise SceneStoreValidationError(
            f"unsupported scene store user_version {version}; expected 1"
        )
    integrity = connection.execute("PRAGMA integrity_check").fetchone()
    if integrity is None or integrity[0] != "ok":
        detail = "missing result" if integrity is None else str(integrity[0])
        raise SceneStoreValidationError(f"scene store integrity_check failed: {detail}")
    foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchone()
    if foreign_keys is not None:
        raise SceneStoreValidationError("scene store contains a broken foreign key")

    meta = _meta_row(connection)
    if meta["schema_name"] != SCENE_STORE_SCHEMA:
        raise SceneStoreValidationError("scene store schema_name is unsupported")
    identity = (
        meta["session_id"],
        meta["player_uuid"],
        meta["connection_id"],
    )
    if any(not isinstance(item, str) or not item for item in identity):
        raise SceneStoreValidationError("scene store identity is incomplete")
    if meta["start_tick"] > meta["end_tick"]:
        raise SceneStoreValidationError("scene store tick bounds are reversed")
    _json_array_from_text(meta["source_replays_json"], "scene source_replays")
    _json_object_from_text(meta["provenance_json"], "scene provenance")

    frame_count = connection.execute("SELECT COUNT(*) FROM frames").fetchone()[0]
    if frame_count == 0:
        raise SceneStoreValidationError("scene store contains no frames")
    bad_frame = connection.execute(
        "SELECT server_tick FROM frames "
        "WHERE server_tick < ? OR server_tick > ? "
        "OR frame_id = '' OR dimension = '' LIMIT 1",
        (meta["start_tick"], meta["end_tick"]),
    ).fetchone()
    if bad_frame is not None:
        raise SceneStoreValidationError("scene store contains an invalid frame")

    for table in ("section_versions", "entity_versions", "block_entity_versions"):
        _validate_interval_bounds(
            connection, table, meta["start_tick"], meta["end_tick"]
        )
    _validate_no_overlap(
        connection,
        "section_versions",
        ("dimension", "section_x", "section_y", "section_z"),
    )
    _validate_no_overlap(connection, "entity_versions", ("instance_id",))
    _validate_no_overlap(
        connection,
        "block_entity_versions",
        ("dimension", "block_x", "block_y", "block_z"),
    )
    _validate_no_overlap(
        connection,
        "entity_versions",
        ("network_id",),
        where="network_id IS NOT NULL",
        description="scene store contains overlapping entity network-id lifetimes",
    )
    _validate_entity_rtree(connection)
    _validate_block_entity_rtree(connection)

    references = (
        ("frames", "payload_sha256", "frame"),
        ("section_versions", "blob_sha256", "section"),
        ("entity_versions", "blob_sha256", "entity"),
        ("block_entity_versions", "blob_sha256", "block_entity"),
    )
    for table, column, expected_kind in references:
        wrong_kind = connection.execute(
            f"SELECT 1 FROM {table} AS source JOIN blobs "
            f"ON blobs.sha256 = source.{column} WHERE blobs.kind != ? LIMIT 1",
            (expected_kind,),
        ).fetchone()
        if wrong_kind is not None:
            raise SceneStoreValidationError(
                f"{table} references a blob with the wrong content kind"
            )
    for row in connection.execute(
        "SELECT sha256, kind FROM blobs ORDER BY sha256"
    ):
        data = _blob_value(connection, row["sha256"], expected_kind=row["kind"])
        if row["kind"] == "section":
            _decode_section_blob(data)
        else:
            payload = _parse_canonical_json(
                data, description=f"{row['kind']} blob {row['sha256']}"
            )
            if not isinstance(payload, dict):
                raise SceneStoreValidationError(
                    f"{row['kind']} blob {row['sha256']} must contain an object"
                )


def validate_scene_store(
    path: Path | str,
    *,
    expected_session_id: str | None = None,
    expected_player_uuid: str | None = None,
    expected_connection_id: str | None = None,
    expected_start_tick: int | None = None,
    expected_end_tick: int | None = None,
    expected_ticks: Iterable[int] | None = None,
) -> SceneStoreInfo:
    """Fully validate a scene store and return its immutable envelope."""

    store_path = Path(path)
    try:
        _reject_symlink(store_path, description="scene store", must_exist=True)
        before = store_path.stat()
        with closing(_connect_read_only(store_path)) as connection:
            _validate_connection(connection, store_path)
            info = _store_info(connection, store_path, include_hash=False)
        sha256 = _sha256_file(store_path)
        after = store_path.stat()
    except SceneStoreError:
        raise
    except (OSError, sqlite3.Error) as exc:
        raise SceneStoreValidationError(f"cannot validate scene store {store_path}: {exc}") from exc
    if (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
    ) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ):
        raise SceneStoreValidationError("scene store changed while it was being validated")
    info = SceneStoreInfo(
        identity=info.identity,
        start_tick=info.start_tick,
        end_tick=info.end_tick,
        frame_count=info.frame_count,
        ticks=info.ticks,
        coverage_complete=info.coverage_complete,
        source_replays=info.source_replays,
        extraction=info.extraction,
        sensitive=info.sensitive,
        sha256=sha256,
        size_bytes=after.st_size,
    )
    expected_identity = SceneIdentity(
        expected_session_id or info.identity.session_id,
        expected_player_uuid or info.identity.player_uuid,
        expected_connection_id or info.identity.connection_id,
    )
    if info.identity != expected_identity:
        raise SceneStoreValidationError(
            "scene store identity does not match the expected session/player/connection"
        )
    if expected_start_tick is not None and info.start_tick != expected_start_tick:
        raise SceneStoreValidationError(
            f"scene store starts at tick {info.start_tick}, expected {expected_start_tick}"
        )
    if expected_end_tick is not None and info.end_tick != expected_end_tick:
        raise SceneStoreValidationError(
            f"scene store ends at tick {info.end_tick}, expected {expected_end_tick}"
        )
    if expected_ticks is not None:
        ticks = _validated_ticks(expected_ticks)
        if info.ticks != ticks:
            raise SceneStoreValidationError("scene store frame ticks do not match expected ticks")
    return info


def _validated_ticks(values: Iterable[int]) -> tuple[int, ...]:
    ticks = tuple(values)
    if not ticks:
        raise SceneStoreError("expected scene ticks cannot be empty")
    if any(isinstance(tick, bool) or not isinstance(tick, int) for tick in ticks):
        raise SceneStoreError("expected scene ticks must be integers")
    if tuple(sorted(set(ticks))) != ticks:
        raise SceneStoreError("expected scene ticks must be strictly increasing and unique")
    return ticks


class SceneStoreBuilder:
    """Incrementally compact sequential scene changes into a publishable store."""

    def __init__(
        self,
        identity: SceneIdentity,
        *,
        start_tick: int,
        end_tick: int,
        source_replays: Sequence[Mapping[str, Any]] = (),
        sensitive: bool = True,
        provenance: Mapping[str, Any] | None = None,
        staging_dir: Path | None = None,
    ):
        for field_name, value in (
            ("session_id", identity.session_id),
            ("player_uuid", identity.player_uuid),
            ("connection_id", identity.connection_id),
        ):
            _required_text(value, f"scene identity {field_name}")
        _required_int(start_tick, "scene start_tick")
        _required_int(end_tick, "scene end_tick")
        if start_tick > end_tick:
            raise SceneStoreError("scene start_tick cannot follow end_tick")
        staging = None if staging_dir is None else str(staging_dir)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix="mc-recorder-scene-", suffix=".sqlite3", dir=staging
        )
        os.close(descriptor)
        self._path = Path(temporary_name)
        self._connection = sqlite3.connect(self._path)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute("PRAGMA journal_mode = DELETE")
        self._connection.executescript(_SCHEMA_SQL)
        source_json = _canonical_json_bytes(
            list(source_replays), description="scene source_replays"
        ).decode("utf-8")
        provenance_json = _canonical_json_bytes(
            dict(provenance or {}), description="scene provenance"
        ).decode("utf-8")
        self._connection.execute(
            "INSERT INTO scene_meta VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                SCENE_STORE_SCHEMA,
                identity.session_id,
                identity.player_uuid,
                identity.connection_id,
                start_tick,
                end_tick,
                source_json,
                1 if sensitive else 0,
                provenance_json,
            ),
        )
        self.identity = identity
        self.start_tick = start_tick
        self.end_tick = end_tick
        self._last_frame_tick: int | None = None
        self._last_change_tick: int | None = None
        self._active_sections: dict[tuple[str, int, int, int], tuple[int, str]] = {}
        self._active_entities: dict[
            str, tuple[int, int | None, str, str, tuple[float, ...], str]
        ] = {}
        self._active_network_ids: dict[int, str] = {}
        self._closed_entity_instances: set[str] = set()
        self._active_block_entities: dict[
            tuple[str, int, int, int], tuple[int, str, str]
        ] = {}
        self._published = False
        self._closed = False

    def __enter__(self) -> SceneStoreBuilder:
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()

    def close(self) -> None:
        if not self._closed:
            self._connection.close()
            self._closed = True
        if not self._published:
            try:
                self._path.unlink()
            except FileNotFoundError:
                pass

    def _ensure_open(self) -> None:
        if self._closed or self._published:
            raise SceneStoreError("scene store builder is already closed")

    def _tick(self, tick: int, *, stream: str) -> int:
        self._ensure_open()
        _required_int(tick, f"scene {stream} tick")
        if not self.start_tick <= tick <= self.end_tick:
            raise SceneStoreError(
                f"scene {stream} tick {tick} is outside "
                f"[{self.start_tick},{self.end_tick}]"
            )
        attribute = "_last_frame_tick" if stream == "frame" else "_last_change_tick"
        previous = getattr(self, attribute)
        if previous is not None and tick < previous:
            raise SceneStoreError(f"scene {stream} ticks must be nondecreasing")
        setattr(self, attribute, tick)
        return tick

    def _put_blob(self, kind: str, value: bytes) -> str:
        if len(value) > MAX_BLOB_UNCOMPRESSED_BYTES:
            raise SceneStoreError(f"{kind} blob exceeds the uncompressed size limit")
        if kind == "section":
            _decode_section_blob(value)
        else:
            payload = _parse_canonical_json(value, description=f"{kind} blob")
            if not isinstance(payload, dict):
                raise SceneStoreError(f"{kind} blob must contain a JSON object")
        digest = _sha256_bytes(value)
        existing = self._connection.execute(
            "SELECT kind, uncompressed_size FROM blobs WHERE sha256 = ?", (digest,)
        ).fetchone()
        if existing is not None:
            if existing["kind"] != kind or existing["uncompressed_size"] != len(value):
                raise SceneStoreError(f"scene blob hash collision for {digest}")
            return digest
        compressed = zlib.compress(value, level=9)
        self._connection.execute(
            "INSERT INTO blobs VALUES (?, ?, ?, ?, ?)",
            (digest, kind, len(value), len(compressed), compressed),
        )
        return digest

    def add_frame(
        self,
        tick: int,
        *,
        frame_id: str | None = None,
        replay_tick: int | None = None,
        dimension: str,
        subject_position: Sequence[float],
        coverage_complete: bool = True,
        reasons: Sequence[str] = (),
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        tick = self._tick(tick, stream="frame")
        identifier = str(tick) if frame_id is None else _required_text(frame_id, "frame_id")
        if replay_tick is not None:
            _required_int(replay_tick, "frame replay_tick")
        dimension = _required_text(dimension, "frame dimension")
        position = _float_tuple(subject_position, 3, "frame subject_position")
        if not isinstance(coverage_complete, bool):
            raise SceneStoreError("frame coverage_complete must be boolean")
        normalized_reasons = []
        for index, reason in enumerate(reasons):
            normalized_reasons.append(_required_text(reason, f"frame reason {index}"))
        payload = {
            "metadata": dict(metadata or {}),
            "reasons": normalized_reasons,
        }
        payload_sha = self._put_blob(
            "frame", _canonical_json_bytes(payload, description="frame payload")
        )
        try:
            self._connection.execute(
                "INSERT INTO frames VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    tick,
                    identifier,
                    replay_tick,
                    dimension,
                    position[0],
                    position[1],
                    position[2],
                    1 if coverage_complete else 0,
                    payload_sha,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise SceneStoreError(f"duplicate scene frame tick or id: {identifier}") from exc

    def _close_section(self, key: tuple[str, int, int, int], end_tick: int) -> None:
        start_tick, digest = self._active_sections.pop(key)
        if start_tick == end_tick:
            return
        self._connection.execute(
            "INSERT INTO section_versions VALUES (?, ?, ?, ?, ?, ?, ?)",
            (*key, start_tick, end_tick, digest),
        )

    def set_section(
        self,
        tick: int,
        dimension: str,
        section_position: Sequence[int],
        palette: Sequence[Any],
        indices: Sequence[int],
    ) -> None:
        self.set_section_blob(
            tick,
            dimension,
            section_position,
            canonical_section_blob(palette, indices),
        )

    def set_section_blob(
        self,
        tick: int,
        dimension: str,
        section_position: Sequence[int],
        canonical_blob: bytes,
    ) -> None:
        tick = self._tick(tick, stream="change")
        dimension = _required_text(dimension, "section dimension")
        if len(section_position) != 3:
            raise SceneStoreError("section position must contain x, y, z")
        coordinates = tuple(
            _required_int(value, f"section coordinate {index}")
            for index, value in enumerate(section_position)
        )
        key = (dimension, coordinates[0], coordinates[1], coordinates[2])
        digest = self._put_blob("section", canonical_blob)
        active = self._active_sections.get(key)
        if active is not None and active[1] == digest:
            return
        if active is not None:
            self._close_section(key, tick)
        self._active_sections[key] = (tick, digest)

    def unload_section(
        self, tick: int, dimension: str, section_position: Sequence[int]
    ) -> None:
        tick = self._tick(tick, stream="change")
        dimension = _required_text(dimension, "section dimension")
        if len(section_position) != 3:
            raise SceneStoreError("section position must contain x, y, z")
        coordinates = tuple(
            _required_int(value, f"section coordinate {index}")
            for index, value in enumerate(section_position)
        )
        key = (dimension, coordinates[0], coordinates[1], coordinates[2])
        if key in self._active_sections:
            self._close_section(key, tick)

    def _close_entity(self, instance_id: str, end_tick: int) -> None:
        start_tick, network_id, dimension, type_id, aabb, digest = (
            self._active_entities.pop(instance_id)
        )
        if network_id is not None:
            self._active_network_ids.pop(network_id, None)
        if start_tick != end_tick:
            self._connection.execute(
                "INSERT INTO entity_versions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    instance_id,
                    network_id,
                    dimension,
                    type_id,
                    start_tick,
                    end_tick,
                    *aabb,
                    digest,
                ),
            )
        self._closed_entity_instances.add(instance_id)

    def set_entity(
        self,
        tick: int,
        instance_id: str,
        payload: Mapping[str, Any],
    ) -> None:
        self.set_entity_blob(
            tick,
            instance_id,
            _canonical_json_bytes(dict(payload), description="entity payload"),
        )

    def set_entity_blob(self, tick: int, instance_id: str, canonical_blob: bytes) -> None:
        tick = self._tick(tick, stream="change")
        instance_id = _required_text(instance_id, "entity instance_id")
        value = _parse_canonical_json(canonical_blob, description="entity blob")
        if not isinstance(value, dict):
            raise SceneStoreError("entity blob must contain an object")
        dimension, type_id, network_id, _uuid, _position, _velocity, _rotation, aabb = (
            _entity_fields(instance_id, value)
        )
        current = self._active_entities.get(instance_id)
        if current is None and instance_id in self._closed_entity_instances:
            raise SceneStoreError(f"entity instance_id cannot be reused: {instance_id}")
        if current is not None and current[1] != network_id:
            raise SceneStoreError("an entity instance cannot change network_id")
        owner = None if network_id is None else self._active_network_ids.get(network_id)
        if owner is not None and owner != instance_id:
            raise SceneStoreError(
                f"entity network_id {network_id} is already active as {owner}"
            )
        digest = self._put_blob("entity", canonical_blob)
        if current is not None and current[-1] == digest:
            return
        if current is not None:
            self._close_entity(instance_id, tick)
            self._closed_entity_instances.discard(instance_id)
        self._active_entities[instance_id] = (
            tick,
            network_id,
            dimension,
            type_id,
            aabb,
            digest,
        )
        if network_id is not None:
            self._active_network_ids[network_id] = instance_id

    def remove_entity(self, tick: int, instance_id: str) -> None:
        tick = self._tick(tick, stream="change")
        instance_id = _required_text(instance_id, "entity instance_id")
        if instance_id in self._active_entities:
            self._close_entity(instance_id, tick)

    def _close_block_entity(
        self, key: tuple[str, int, int, int], end_tick: int
    ) -> None:
        start_tick, type_id, digest = self._active_block_entities.pop(key)
        if start_tick == end_tick:
            return
        self._connection.execute(
            "INSERT INTO block_entity_versions VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (*key, type_id, start_tick, end_tick, digest),
        )

    def set_block_entity(
        self,
        tick: int,
        dimension: str,
        position: Sequence[int],
        payload: Mapping[str, Any],
    ) -> None:
        self.set_block_entity_blob(
            tick,
            dimension,
            position,
            _canonical_json_bytes(dict(payload), description="block entity payload"),
        )

    def set_block_entity_blob(
        self,
        tick: int,
        dimension: str,
        position: Sequence[int],
        canonical_blob: bytes,
    ) -> None:
        tick = self._tick(tick, stream="change")
        dimension = _required_text(dimension, "block entity dimension")
        if len(position) != 3:
            raise SceneStoreError("block entity position must contain x, y, z")
        coordinates = tuple(
            _required_int(value, f"block entity coordinate {index}")
            for index, value in enumerate(position)
        )
        value = _parse_canonical_json(canonical_blob, description="block entity blob")
        if not isinstance(value, dict):
            raise SceneStoreError("block entity blob must contain an object")
        embedded_dimension = value.get("dimension")
        if embedded_dimension is not None and embedded_dimension != dimension:
            raise SceneStoreError("block entity payload dimension does not match its event")
        embedded_position = value.get("position")
        if embedded_position is not None and tuple(embedded_position) != coordinates:
            raise SceneStoreError("block entity payload position does not match its event")
        type_id = _block_entity_type(value)
        digest = self._put_blob("block_entity", canonical_blob)
        key = (dimension, coordinates[0], coordinates[1], coordinates[2])
        active = self._active_block_entities.get(key)
        if active is not None and active[-1] == digest:
            return
        if active is not None:
            self._close_block_entity(key, tick)
        self._active_block_entities[key] = (tick, type_id, digest)

    def remove_block_entity(
        self, tick: int, dimension: str, position: Sequence[int]
    ) -> None:
        tick = self._tick(tick, stream="change")
        dimension = _required_text(dimension, "block entity dimension")
        if len(position) != 3:
            raise SceneStoreError("block entity position must contain x, y, z")
        coordinates = tuple(
            _required_int(value, f"block entity coordinate {index}")
            for index, value in enumerate(position)
        )
        key = (dimension, coordinates[0], coordinates[1], coordinates[2])
        if key in self._active_block_entities:
            self._close_block_entity(key, tick)

    def _finalize(self) -> None:
        final_tick = self.end_tick + 1
        for key in tuple(self._active_sections):
            self._close_section(key, final_tick)
        for instance_id in tuple(self._active_entities):
            self._close_entity(instance_id, final_tick)
        for key in tuple(self._active_block_entities):
            self._close_block_entity(key, final_tick)
        self._connection.commit()

    def publish(
        self,
        output_path: Path | str,
        *,
        expected_ticks: Iterable[int] | None = None,
    ) -> SceneStoreInfo:
        self._ensure_open()
        output = Path(output_path)
        if not output.parent.is_dir() or output.parent.is_symlink():
            raise SceneStoreError(f"scene store parent is not a safe directory: {output.parent}")
        _reject_symlink(output, description="scene store output", must_exist=False)
        self._finalize()
        if expected_ticks is not None:
            ticks = _validated_ticks(expected_ticks)
            actual = tuple(
                row[0]
                for row in self._connection.execute(
                    "SELECT server_tick FROM frames ORDER BY server_tick"
                )
            )
            if actual != ticks:
                raise SceneStoreError("scene builder frame ticks do not match expected ticks")
        temporary_descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{output.name}.tmp-", suffix=".sqlite3", dir=output.parent
        )
        os.close(temporary_descriptor)
        temporary = Path(temporary_name)
        try:
            destination = sqlite3.connect(temporary)
            try:
                self._connection.backup(destination)
                destination.execute("PRAGMA journal_mode = DELETE")
                destination.commit()
            finally:
                destination.close()
            validate_scene_store(
                temporary,
                expected_session_id=self.identity.session_id,
                expected_player_uuid=self.identity.player_uuid,
                expected_connection_id=self.identity.connection_id,
                expected_start_tick=self.start_tick,
                expected_end_tick=self.end_tick,
                expected_ticks=expected_ticks,
            )
            with temporary.open("rb") as handle:
                os.fsync(handle.fileno())
            os.replace(temporary, output)
            directory_fd = os.open(output.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except Exception:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
            raise
        self._published = True
        self._connection.close()
        self._closed = True
        try:
            self._path.unlink()
        except FileNotFoundError:
            pass
        return validate_scene_store(
            output,
            expected_session_id=self.identity.session_id,
            expected_player_uuid=self.identity.player_uuid,
            expected_connection_id=self.identity.connection_id,
            expected_start_tick=self.start_tick,
            expected_end_tick=self.end_tick,
            expected_ticks=expected_ticks,
        )


def _frame_payload(connection: sqlite3.Connection, digest: str) -> dict[str, Any]:
    value = _parse_canonical_json(
        _blob_value(connection, digest, expected_kind="frame"),
        description=f"frame blob {digest}",
    )
    if not isinstance(value, dict):
        raise SceneStoreValidationError("frame payload must be an object")
    return value


def _row_to_frame(connection: sqlite3.Connection, row: sqlite3.Row) -> SceneFrame:
    payload = _frame_payload(connection, row["payload_sha256"])
    reasons = payload.get("reasons", [])
    metadata = payload.get("metadata", {})
    if not isinstance(reasons, list) or not all(isinstance(item, str) for item in reasons):
        raise SceneStoreValidationError("frame reasons must be an array of text")
    if not isinstance(metadata, dict):
        raise SceneStoreValidationError("frame metadata must be an object")
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


def _entity_from_row(connection: sqlite3.Connection, row: sqlite3.Row) -> SceneEntity:
    data = _blob_value(connection, row["blob_sha256"], expected_kind="entity")
    payload = _parse_canonical_json(data, description="entity payload")
    if not isinstance(payload, dict):
        raise SceneStoreValidationError("entity payload must be an object")
    fields = _entity_fields(row["instance_id"], payload)
    dimension, type_id, network_id, uuid, position, velocity, rotation, aabb = fields
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


def _block_entity_from_row(
    connection: sqlite3.Connection, row: sqlite3.Row
) -> SceneBlockEntity:
    data = _blob_value(connection, row["blob_sha256"], expected_kind="block_entity")
    payload = _parse_canonical_json(data, description="block entity payload")
    if not isinstance(payload, dict):
        raise SceneStoreValidationError("block entity payload must be an object")
    return SceneBlockEntity(
        dimension=row["dimension"],
        position=(row["block_x"], row["block_y"], row["block_z"]),
        type_id=_block_entity_type(payload),
        payload=_freeze_json(payload),
    )


class SceneStore:
    """Verified random-access reader for one connection-scoped scene store."""

    def __init__(self, path: Path | str, *, validate: bool = True):
        self.path = Path(path)
        self._connection: sqlite3.Connection | None = None
        self._info: SceneStoreInfo | None = None
        if validate:
            self._info = validate_scene_store(self.path)
        else:
            _reject_symlink(self.path, description="scene store", must_exist=True)
        try:
            self._connection = _connect_read_only(self.path)
            if not validate:
                version = self._connection.execute("PRAGMA user_version").fetchone()[0]
                if version != SCENE_STORE_USER_VERSION:
                    raise SceneStoreValidationError(
                        f"unsupported scene store user_version {version}; expected 1"
                    )
                _meta_row(self._connection)
        except Exception:
            if self._connection is not None:
                self._connection.close()
            raise
        self._closed = False

    @property
    def info(self) -> SceneStoreInfo:
        self._ensure_open()
        assert self._connection is not None
        if self._info is None:
            self._info = _store_info(self._connection, self.path, include_hash=False)
        return self._info

    def __enter__(self) -> SceneStore:
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()

    def close(self) -> None:
        if not self._closed:
            assert self._connection is not None
            self._connection.close()
            self._closed = True

    def _ensure_open(self) -> None:
        if self._closed:
            raise SceneStoreError("scene store reader is closed")

    def _frame_row(self, tick_or_frame_id: int | str) -> sqlite3.Row:
        self._ensure_open()
        assert self._connection is not None
        if isinstance(tick_or_frame_id, bool):
            raise SceneStoreError("scene frame key must be an integer tick or frame id")
        if isinstance(tick_or_frame_id, int):
            row = self._connection.execute(
                "SELECT * FROM frames WHERE server_tick = ?", (tick_or_frame_id,)
            ).fetchone()
        elif isinstance(tick_or_frame_id, str) and tick_or_frame_id:
            row = self._connection.execute(
                "SELECT * FROM frames WHERE frame_id = ?", (tick_or_frame_id,)
            ).fetchone()
        else:
            raise SceneStoreError("scene frame key must be an integer tick or frame id")
        if row is None:
            raise SceneStoreError(f"scene frame not found: {tick_or_frame_id}")
        return row

    def frame(self, tick_or_frame_id: int | str) -> SceneFrame:
        assert self._connection is not None
        return _row_to_frame(self._connection, self._frame_row(tick_or_frame_id))

    def _entities_in_box(
        self,
        tick: int,
        dimension: str,
        origin: tuple[int, int, int],
        shape: tuple[int, int, int],
    ) -> tuple[SceneEntity, ...]:
        assert self._connection is not None
        maximum = tuple(origin[index] + shape[index] for index in range(3))
        rows = self._connection.execute(
            _ENTITY_BOX_SQL,
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
            ),
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
        rows = self._connection.execute(
            _BLOCK_ENTITY_BOX_SQL,
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
            ),
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
        if len(origin) != 3 or any(
            isinstance(value, bool) or not isinstance(value, int) for value in origin
        ):
            raise SceneStoreError("scene crop origin must contain three integers")
        if len(shape) != 3 or any(
            isinstance(value, bool) or not isinstance(value, int) or value <= 0
            for value in shape
        ):
            raise SceneStoreError("scene crop shape must contain three positive integers")
        if isinstance(max_cells, bool) or not isinstance(max_cells, int) or max_cells <= 0:
            raise SceneStoreError("scene crop max_cells must be a positive integer")
        cell_count = shape[0] * shape[1] * shape[2]
        if cell_count > min(max_cells, DEFAULT_MAX_CROP_CELLS):
            raise SceneStoreError(
                f"scene crop contains {cell_count} cells; limit is "
                f"{min(max_cells, DEFAULT_MAX_CROP_CELLS)}"
            )
        palette_values: list[Any] = []
        palette_lookup: dict[bytes, int] = {}
        indices = [UNKNOWN_PALETTE_INDEX] * cell_count
        coverage = [False] * cell_count
        min_section = tuple(value // SECTION_EDGE for value in origin)
        maximum = tuple(origin[index] + shape[index] - 1 for index in range(3))
        max_section = tuple(value // SECTION_EDGE for value in maximum)
        rows = self._connection.execute(
            "SELECT * FROM section_versions WHERE dimension = ? "
            "AND start_tick <= ? AND end_tick > ? "
            "AND section_x BETWEEN ? AND ? "
            "AND section_y BETWEEN ? AND ? "
            "AND section_z BETWEEN ? AND ? "
            "ORDER BY section_y, section_z, section_x",
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
            section_palette, section_indices = _decode_section_blob(
                _blob_value(
                    self._connection, row["blob_sha256"], expected_kind="section"
                )
            )
            translated: list[int] = []
            for state in section_palette:
                key = _canonical_json_bytes(_thaw_json(state), description="block state")
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
            high = tuple(
                min(origin[index] + shape[index], section_origin[index] + SECTION_EDGE)
                for index in range(3)
            )
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
            block_entities=self._block_entities_in_box(
                frame.tick, frame.dimension, origin, shape
            ),
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
            raise SceneStoreError("scene slice axis must be x, y, or z")
        _required_int(coordinate, "scene slice coordinate")
        if len(center) != 3 or any(
            isinstance(value, bool) or not isinstance(value, int) for value in center
        ):
            raise SceneStoreError("scene slice center must contain three integers")
        if isinstance(radius, bool) or not isinstance(radius, int) or not 0 <= radius <= MAX_SLICE_RADIUS:
            raise SceneStoreError(
                f"scene slice radius must be an integer from 0 to {MAX_SLICE_RADIUS}"
            )
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


def scene_slice_to_json(value: SceneSlice) -> dict[str, Any]:
    """Convert a slice to JSON primitives without exposing mutable aliases."""

    return {
        "tick": value.tick,
        "dimension": value.dimension,
        "axis": value.axis,
        "coordinate": value.coordinate,
        "world_coordinate": value.coordinate,
        "row_axis": value.row_axis,
        "column_axis": value.column_axis,
        "row_origin": value.row_origin,
        "column_origin": value.column_origin,
        "width": value.width,
        "height": value.height,
        "coverage_complete": value.coverage_complete,
        "cells": [
            {
                "world_position": list(cell.world_position),
                "covered": cell.covered,
                "block_state": _thaw_json(cell.block_state),
            }
            for cell in value.cells
        ],
        "entities": [
            {
                "instance_id": entity.instance_id,
                "dimension": entity.dimension,
                "type_id": entity.type_id,
                "network_id": entity.network_id,
                "uuid": entity.uuid,
                "position": list(entity.position),
                "velocity": None if entity.velocity is None else list(entity.velocity),
                "rotation": None if entity.rotation is None else list(entity.rotation),
                "aabb": list(entity.aabb),
                "payload": _thaw_json(entity.payload),
            }
            for entity in value.entities
        ],
        "block_entities": [
            {
                "dimension": block_entity.dimension,
                "position": list(block_entity.position),
                "type_id": block_entity.type_id,
                "payload": _thaw_json(block_entity.payload),
            }
            for block_entity in value.block_entities
        ],
    }


def _read_jsonl(path: Path, description: str) -> Iterable[tuple[int, dict[str, Any]]]:
    _reject_symlink(path, description=description, must_exist=True)
    with path.open("rb") as handle:
        for line_number, line in enumerate(handle, start=1):
            if len(line) > MAX_JSONL_LINE_BYTES:
                raise SceneStoreError(
                    f"{description} line {line_number} exceeds the size limit"
                )
            if not line.strip():
                raise SceneStoreError(f"{description} line {line_number} is empty")
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise SceneStoreError(
                    f"{description} line {line_number} is not valid JSON"
                ) from exc
            if not isinstance(value, dict):
                raise SceneStoreError(
                    f"{description} line {line_number} must contain an object"
                )
            yield line_number, value


def _stream_blob(stream_dir: Path, digest: str) -> bytes:
    if (
        not isinstance(digest, str)
        or len(digest) != 64
        or any(character not in "0123456789abcdef" for character in digest)
    ):
        raise SceneStoreError("scene stream blob_sha256 must be lowercase SHA-256 text")
    candidates = (
        stream_dir / "blobs" / f"{digest}.json.zlib",
        stream_dir / "blobs" / f"{digest}.zlib",
    )
    path = next(
        (candidate for candidate in candidates if candidate.exists() or candidate.is_symlink()),
        candidates[0],
    )
    _reject_symlink(path, description="scene stream blob", must_exist=True)
    compressed = path.read_bytes()
    if len(compressed) > MAX_BLOB_UNCOMPRESSED_BYTES:
        raise SceneStoreError(f"scene stream blob is too large: {digest}")
    inflater = zlib.decompressobj()
    try:
        value = inflater.decompress(compressed, MAX_BLOB_UNCOMPRESSED_BYTES + 1)
        if len(value) > MAX_BLOB_UNCOMPRESSED_BYTES or inflater.unconsumed_tail:
            raise SceneStoreError(f"scene stream blob expands beyond the limit: {digest}")
        value += inflater.flush()
    except zlib.error as exc:
        raise SceneStoreError(f"scene stream blob is not valid zlib data: {digest}") from exc
    if len(value) > MAX_BLOB_UNCOMPRESSED_BYTES:
        raise SceneStoreError(f"scene stream blob expands beyond the limit: {digest}")
    if not inflater.eof or inflater.unused_data:
        raise SceneStoreError(f"scene stream blob has an invalid zlib boundary: {digest}")
    if _sha256_bytes(value) != digest:
        raise SceneStoreError(f"scene stream blob hash mismatch: {digest}")
    return value


def _canonicalize_stream_json_blob(data: bytes, description: str) -> bytes:
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SceneStoreError(f"{description} is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise SceneStoreError(f"{description} must contain a JSON object")
    return _canonical_json_bytes(value, description=description)


def _canonicalize_stream_section_blob(data: bytes) -> bytes:
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SceneStoreError("scene stream section blob is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise SceneStoreError("scene stream section blob must contain an object")
    if value.get("format") == "mc-recorder-section-v1":
        canonical = _canonical_json_bytes(value, description="scene stream section blob")
        _decode_section_blob(canonical)
        return canonical
    if (
        value.get("schema_version") != 1
        or value.get("cell_order") not in {"y_z_x", SECTION_INDEX_ORDER}
    ):
        raise SceneStoreError("scene stream section blob has an unsupported contract")
    palette = value.get("palette")
    indices = value.get("indices")
    if not isinstance(palette, list) or not isinstance(indices, list):
        raise SceneStoreError("scene stream section blob lacks palette/indices arrays")
    return canonical_section_blob(palette, indices)


def _verified_scene_stream_context(
    directory: Path,
    verified_stream: VerifiedSceneStream,
    *,
    identity: SceneIdentity,
    ticks: tuple[int, ...],
) -> tuple[list[Mapping[str, Any]], bool, dict[str, Any]]:
    _required_sha256(
        verified_stream.result_sha256, "verified scene result sha256"
    )
    result = _thaw_json(verified_stream.result)
    if not isinstance(result, dict):
        raise SceneStoreError("verified scene result must contain an object")
    if result.get("job_id") != verified_stream.job_id:
        raise SceneStoreError("verified scene result job_id is inconsistent")
    stream = result.get("stream")
    if not isinstance(stream, dict):
        raise SceneStoreError("verified scene result lacks its stream envelope")
    stream_path = stream.get("path")
    if (
        not isinstance(stream_path, str)
        or Path(stream_path).resolve() != directory.resolve()
    ):
        raise SceneStoreError(
            "verified scene result stream path does not match compaction input"
        )
    integrity = dict(stream)
    integrity.pop("path")
    if integrity != verified_stream.integrity.as_dict():
        raise SceneStoreError(
            "verified scene result stream envelope does not match its integrity snapshot"
        )
    provenance = {
        "scope": result.get("scope"),
        "metadata_policy": result.get("metadata_policy"),
        "result": result,
    }
    extraction = _validated_extraction_provenance(
        result.get("source_replays"),
        provenance,
        identity=identity,
        start_tick=ticks[0],
        end_tick=ticks[-1],
        frame_count=len(ticks),
    )
    return (
        list(_thaw_json(result["source_replays"])),
        extraction.metadata_policy == SCENE_METADATA_POLICY_FULL,
        {
            "scope": extraction.scope,
            "metadata_policy": extraction.metadata_policy,
            "result": _thaw_json(extraction.result),
        },
    )


def _frame_logical_value(frame: Mapping[str, Any], line_number: int) -> dict[str, Any]:
    complete = frame.get("complete", True)
    if not isinstance(complete, bool):
        raise SceneStoreError(f"scene frames line {line_number} complete must be boolean")
    reasons = frame.get("reasons", [])
    if not isinstance(reasons, list) or not all(isinstance(item, str) for item in reasons):
        raise SceneStoreError(f"scene frames line {line_number} reasons must be text")
    metadata = frame.get("metadata", {})
    if not isinstance(metadata, dict):
        raise SceneStoreError(f"scene frames line {line_number} metadata must be an object")
    position = frame.get("subject_position")
    normalized_position = _float_tuple(
        position, 3, f"scene frames line {line_number} subject_position"
    )
    return {
        "dimension": _required_text(
            frame.get("dimension"), f"scene frames line {line_number} dimension"
        ),
        "subject_position": list(normalized_position),
        "complete": complete,
        "reasons": reasons,
        "metadata": metadata,
    }


def _change_body(change: Mapping[str, Any]) -> tuple[Any, Mapping[str, Any]]:
    event_type = change.get("type", change.get("kind"))
    data = change.get("data")
    if data is None:
        return event_type, change
    if not isinstance(data, dict):
        raise SceneStoreError("scene change data must be an object")
    return event_type, data


def _change_resource(
    event_type: str, body: Mapping[str, Any]
) -> tuple[int, tuple[Any, ...], dict[str, Any]]:
    if event_type in {"section_set", "section_unload"}:
        payload = {
            "type": event_type,
            "dimension": body.get("dimension"),
            "x": body.get("x"),
            "y": body.get("y"),
            "z": body.get("z"),
        }
        if event_type == "section_set":
            payload["blob_sha256"] = body.get("blob_sha256", body.get("blob"))
        return (
            0 if event_type == "section_unload" else 1,
            ("section", payload["dimension"], payload["x"], payload["y"], payload["z"]),
            payload,
        )
    if event_type in {"entity_set", "entity_remove"}:
        payload = {"type": event_type, "instance_id": body.get("instance_id")}
        if event_type == "entity_set":
            payload["blob_sha256"] = body.get("blob_sha256", body.get("blob"))
        return (
            # Apply a new segment's aliases before retiring the preceding
            # segment's aliases at the same global tick.
            0 if event_type == "entity_set" else 1,
            ("entity", payload["instance_id"]),
            payload,
        )
    if event_type in {"block_entity_set", "block_entity_remove"}:
        payload = {
            "type": event_type,
            "dimension": body.get("dimension"),
            "x": body.get("x"),
            "y": body.get("y"),
            "z": body.get("z"),
        }
        if event_type == "block_entity_set":
            payload["blob_sha256"] = body.get("blob_sha256", body.get("blob"))
        return (
            0 if event_type == "block_entity_remove" else 1,
            (
                "block_entity",
                payload["dimension"],
                payload["x"],
                payload["y"],
                payload["z"],
            ),
            payload,
        )
    raise SceneStoreError(f"unsupported normalized scene change type {event_type!r}")


def compact_scene_stream(
    stream_dir: Path | str,
    output_path: Path | str,
    *,
    expected_session_id: str,
    expected_player_uuid: str,
    expected_connection_id: str,
    expected_ticks: Iterable[int],
    verified_stream: VerifiedSceneStream,
    force: bool = False,
) -> SceneStoreInfo:
    """Compact a verified ``scene-stream-v1`` spool into an atomic store.

    The spool contains ``frames.jsonl``, ``changes.jsonl``, and zlib-compressed
    canonical blobs at ``blobs/<sha256>.zlib``. Segment overlap is sorted and
    deduplicated before the half-open versions are written.
    """

    directory = Path(stream_dir)
    if directory.is_symlink() or not directory.is_dir():
        raise SceneStoreError(f"scene stream is not a safe directory: {directory}")
    output = Path(output_path)
    if not output.parent.is_dir() or output.parent.is_symlink():
        raise SceneStoreError(f"scene store parent is not a safe directory: {output.parent}")
    existing_output: tuple[int, int, int, int] | None = None
    if output.exists() or output.is_symlink():
        if not force:
            raise SceneStoreError(
                f"scene store output exists: {output}; pass --force to replace it"
            )
        validate_scene_store(output)
        output_stat = output.stat()
        existing_output = (
            output_stat.st_dev,
            output_stat.st_ino,
            output_stat.st_size,
            output_stat.st_mtime_ns,
        )
    if verified_stream.stream_path.resolve() != directory.resolve():
        raise SceneStoreError("verified scene stream path does not match compaction input")
    try:
        verify_scene_stream(directory, verified_stream.integrity)
    except SceneStreamIntegrityError as exc:
        raise SceneStoreError(f"verified scene stream is invalid: {exc}") from exc
    ticks = _validated_ticks(expected_ticks)
    tick_set = set(ticks)
    identity = SceneIdentity(
        _required_text(expected_session_id, "expected session_id"),
        _required_text(expected_player_uuid, "expected player_uuid"),
        _required_text(expected_connection_id, "expected connection_id"),
    )
    source_replays, sensitive, provenance = _verified_scene_stream_context(
        directory,
        verified_stream,
        identity=identity,
        ticks=ticks,
    )
    frames: dict[int, tuple[dict[str, Any], dict[str, Any]]] = {}
    for line_number, frame in _read_jsonl(directory / "frames.jsonl", "scene frames"):
        tick_value = frame.get("server_tick", frame.get("global_tick"))
        tick = _required_int(tick_value, f"frame line {line_number} tick")
        if tick not in tick_set:
            raise SceneStoreError(
                f"scene frames line {line_number} has unexpected tick {tick}"
            )
        logical = _frame_logical_value(frame, line_number)
        if (
            logical["metadata"].get("scope") != provenance["scope"]
            or logical["metadata"].get("metadata_policy")
            != provenance["metadata_policy"]
        ):
            raise SceneStoreError(
                f"scene frames line {line_number} policy does not match "
                "the verified extraction result"
            )
        existing = frames.get(tick)
        if existing is not None:
            if existing[1] != logical:
                raise SceneStoreError(
                    f"overlapping scene frames disagree at global tick {tick}"
                )
            previous_segment = existing[0].get("segment_id")
            current_segment = frame.get("segment_id")
            if previous_segment != current_segment:
                snapshot_hash = logical["metadata"].get("scene_snapshot_sha256")
                if (
                    not isinstance(snapshot_hash, str)
                    or len(snapshot_hash) != 64
                    or any(
                        character not in "0123456789abcdef"
                        for character in snapshot_hash
                    )
                ):
                    raise SceneStoreError(
                        "overlapping scene frames require an equal logical snapshot hash"
                    )
            previous_order = (
                existing[0].get("segment_ordinal", 2**31),
                existing[0].get("replay_tick", 2**63),
            )
            current_order = (
                frame.get("segment_ordinal", 2**31),
                frame.get("replay_tick", 2**63),
            )
            if current_order < previous_order:
                frames[tick] = (frame, logical)
        else:
            frames[tick] = (frame, logical)
    if tuple(sorted(frames)) != ticks:
        raise SceneStoreError("scene stream frame coverage does not match expected ticks")

    stage_descriptor, stage_name = tempfile.mkstemp(
        prefix="mc-recorder-scene-events-", suffix=".sqlite3", dir=output.parent
    )
    os.close(stage_descriptor)
    stage_path = Path(stage_name)
    segment_begins: dict[str, dict[str, Any]] = {}
    try:
        with closing(sqlite3.connect(stage_path)) as stage:
            stage.execute(
                "CREATE TABLE events ("
                "server_tick INTEGER NOT NULL, priority INTEGER NOT NULL, "
                "resource_key TEXT NOT NULL, payload_json TEXT NOT NULL, "
                "PRIMARY KEY(server_tick, resource_key))"
            )
            previous_sequence: int | None = None
            for line_number, change in _read_jsonl(
                directory / "changes.jsonl", "scene changes"
            ):
                sequence_value = change.get("sequence")
                if sequence_value is not None:
                    sequence = _required_int(
                        sequence_value, f"change line {line_number} sequence"
                    )
                    if previous_sequence is not None and sequence != previous_sequence + 1:
                        raise SceneStoreError(
                            "scene change sequence must be contiguous and increasing"
                        )
                    previous_sequence = sequence
                event_value, body = _change_body(change)
                event_type = _required_text(
                    event_value, f"change line {line_number} type"
                )
                if event_type == "segment_begin":
                    segment_id = _required_text(
                        change.get("segment_id", body.get("segment_id")),
                        f"change line {line_number} segment_id",
                    )
                    begin = {
                        "segment_id": segment_id,
                        "segment_ordinal": _required_int(
                            change.get("segment_ordinal", body.get("segment_ordinal")),
                            f"change line {line_number} segment_ordinal",
                        ),
                        "sha256": _required_sha256(
                            body.get("sha256", change.get("sha256")),
                            f"change line {line_number} sha256",
                        ),
                        "size_bytes": _required_int(
                            body.get("size_bytes", change.get("size_bytes")),
                            f"change line {line_number} size_bytes",
                        ),
                    }
                    if begin["segment_ordinal"] < 0 or begin["size_bytes"] < 0:
                        raise SceneStoreError(
                            f"change line {line_number} segment provenance cannot be negative"
                        )
                    previous = segment_begins.get(segment_id)
                    if previous is not None and previous != begin:
                        raise SceneStoreError(
                            f"conflicting segment_begin provenance for {segment_id}"
                        )
                    segment_begins[segment_id] = begin
                    continue
                tick = _required_int(
                    change.get("server_tick", body.get("server_tick")),
                    f"change line {line_number} tick",
                )
                if tick not in tick_set:
                    raise SceneStoreError(
                        f"scene changes line {line_number} has unexpected tick {tick}"
                    )
                priority, resource, payload = _change_resource(event_type, body)
                resource_json = _canonical_json_bytes(
                    list(resource), description="scene change resource"
                ).decode("utf-8")
                payload_json = _canonical_json_bytes(
                    payload, description="scene change payload"
                ).decode("utf-8")
                existing = stage.execute(
                    "SELECT payload_json FROM events "
                    "WHERE server_tick = ? AND resource_key = ?",
                    (tick, resource_json),
                ).fetchone()
                if existing is not None:
                    if existing[0] != payload_json:
                        raise SceneStoreError(
                            f"overlapping scene changes disagree at tick {tick} "
                            f"for resource {resource_json}"
                        )
                    continue
                stage.execute(
                    "INSERT INTO events VALUES (?, ?, ?, ?)",
                    (tick, priority, resource_json, payload_json),
                )
            stage.commit()

            if not segment_begins:
                raise SceneStoreError(
                    "scene stream lacks segment_begin source replay provenance"
                )
            known_sources = {
                source["segment_id"]: source for source in source_replays
            }
            if set(segment_begins) != set(known_sources):
                raise SceneStoreError(
                    "scene segment_begin records do not exactly match source replays"
                )
            for segment_id, begin in segment_begins.items():
                known = known_sources[segment_id]
                if any(
                    known[key] != begin[key]
                    for key in ("segment_ordinal", "sha256", "size_bytes")
                ):
                    raise SceneStoreError(
                        f"segment_begin does not match source replay {segment_id}"
                    )

            builder = SceneStoreBuilder(
                identity,
                start_tick=ticks[0],
                end_tick=ticks[-1],
                source_replays=source_replays,
                sensitive=sensitive,
                provenance=provenance,
                staging_dir=output.parent,
            )
            try:
                for tick in ticks:
                    frame, logical = frames[tick]
                    builder.add_frame(
                        tick,
                        frame_id=frame.get("frame_id"),
                        replay_tick=frame.get("replay_tick"),
                        dimension=logical["dimension"],
                        subject_position=logical["subject_position"],
                        coverage_complete=logical["complete"],
                        reasons=logical["reasons"],
                        metadata=logical["metadata"],
                    )
                rows = stage.execute(
                    "SELECT server_tick, payload_json FROM events "
                    "ORDER BY server_tick, priority, resource_key"
                )
                entity_aliases: dict[str, str] = {}
                entity_alias_identity: dict[str, tuple[Any, ...]] = {}
                active_entity_identities: dict[tuple[Any, ...], str] = {}
                canonical_entity_aliases: dict[str, set[str]] = {}
                for tick, payload_json in rows:
                    payload = json.loads(payload_json)
                    event_type = payload["type"]
                    if event_type == "section_set":
                        builder.set_section_blob(
                            tick,
                            payload["dimension"],
                            (payload["x"], payload["y"], payload["z"]),
                            _canonicalize_stream_section_blob(
                                _stream_blob(directory, payload["blob_sha256"])
                            ),
                        )
                    elif event_type == "section_unload":
                        builder.unload_section(
                            tick,
                            payload["dimension"],
                            (payload["x"], payload["y"], payload["z"]),
                        )
                    elif event_type == "entity_set":
                        incoming_instance = payload["instance_id"]
                        canonical_blob = _canonicalize_stream_json_blob(
                            _stream_blob(directory, payload["blob_sha256"]),
                            "scene stream entity blob",
                        )
                        entity_payload = json.loads(canonical_blob)
                        fields = _entity_fields(incoming_instance, entity_payload)
                        identity_key: tuple[Any, ...]
                        parts = incoming_instance.rsplit(":", 2)
                        generation = parts[2] if len(parts) == 3 else None
                        if fields[3] is not None:
                            identity_key = (
                                "uuid",
                                fields[3],
                                fields[2],
                                generation,
                            )
                        elif fields[2] is not None:
                            identity_key = (
                                "network",
                                fields[2],
                                fields[1],
                                generation,
                            )
                        else:
                            identity_key = ("instance", incoming_instance)
                        canonical_instance = entity_aliases.get(incoming_instance)
                        if canonical_instance is None:
                            canonical_instance = active_entity_identities.get(identity_key)
                            if canonical_instance is None:
                                canonical_instance = incoming_instance
                                active_entity_identities[identity_key] = canonical_instance
                            entity_aliases[incoming_instance] = canonical_instance
                            entity_alias_identity[incoming_instance] = identity_key
                            canonical_entity_aliases.setdefault(
                                canonical_instance, set()
                            ).add(incoming_instance)
                        elif entity_alias_identity[incoming_instance] != identity_key:
                            raise SceneStoreError(
                                f"scene entity alias changes identity: {incoming_instance}"
                            )
                        builder.set_entity_blob(
                            tick,
                            canonical_instance,
                            canonical_blob,
                        )
                    elif event_type == "entity_remove":
                        incoming_instance = payload["instance_id"]
                        canonical_instance = entity_aliases.get(incoming_instance)
                        if canonical_instance is None:
                            raise SceneStoreError(
                                f"scene removes an unknown entity alias: {incoming_instance}"
                            )
                        identity_key = entity_alias_identity[incoming_instance]
                        aliases = canonical_entity_aliases[canonical_instance]
                        aliases.discard(incoming_instance)
                        if not aliases:
                            builder.remove_entity(tick, canonical_instance)
                            if active_entity_identities.get(identity_key) == canonical_instance:
                                active_entity_identities.pop(identity_key)
                    elif event_type == "block_entity_set":
                        builder.set_block_entity_blob(
                            tick,
                            payload["dimension"],
                            (payload["x"], payload["y"], payload["z"]),
                            _canonicalize_stream_json_blob(
                                _stream_blob(directory, payload["blob_sha256"]),
                                "scene stream block entity blob",
                            ),
                        )
                    else:
                        builder.remove_block_entity(
                            tick,
                            payload["dimension"],
                            (payload["x"], payload["y"], payload["z"]),
                        )
                if existing_output is None:
                    if output.exists() or output.is_symlink():
                        raise SceneStoreError(
                            "scene store output appeared while compaction was running"
                        )
                else:
                    try:
                        output_stat = output.stat()
                    except OSError as exc:
                        raise SceneStoreError(
                            "existing scene store changed while compaction was running"
                        ) from exc
                    if output.is_symlink() or (
                        output_stat.st_dev,
                        output_stat.st_ino,
                        output_stat.st_size,
                        output_stat.st_mtime_ns,
                    ) != existing_output:
                        raise SceneStoreError(
                            "existing scene store changed while compaction was running"
                        )
                try:
                    verify_scene_stream(directory, verified_stream.integrity)
                except SceneStreamIntegrityError as exc:
                    raise SceneStoreError(
                        f"scene stream changed during compaction: {exc}"
                    ) from exc
                return builder.publish(output, expected_ticks=ticks)
            finally:
                builder.close()
    finally:
        try:
            stage_path.unlink()
        except FileNotFoundError:
            pass
