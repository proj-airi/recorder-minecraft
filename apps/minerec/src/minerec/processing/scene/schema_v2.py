"""Vendor-neutral Scene Store V2 base schema.

The tables in this module deliberately use only portable SQLAlchemy Core
types and constraints.  SQLite-only acceleration lives in
``store_v2.create_sqlite_scene_schema``; a future PostgreSQL adapter can add
its own spatial indexes without changing this logical schema.
"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    SmallInteger,
    String,
    Table,
    UniqueConstraint,
)

SCENE_STORE_V2_SCHEMA = "mc-recorder-scene-store-v2"
SCENE_STORE_V2_SCHEMA_VERSION = 2
SCENE_STORE_V2_USER_VERSION = 2

metadata = MetaData(
    naming_convention={
        "ck": "ck_%(table_name)s_%(constraint_name)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "ix": "ix_%(table_name)s_%(column_0_name)s",
        "pk": "pk_%(table_name)s",
        "uq": "uq_%(table_name)s_%(column_0_name)s",
    }
)

schema_info = Table(
    "schema_info",
    metadata,
    Column("singleton", SmallInteger, primary_key=True),
    Column("schema_name", String(128), nullable=False),
    Column("schema_version", Integer, nullable=False),
    CheckConstraint("singleton = 1", name="singleton"),
    CheckConstraint("schema_version > 0", name="positive_version"),
)

scene_meta = Table(
    "scene_meta",
    metadata,
    Column("singleton", SmallInteger, primary_key=True),
    Column("session_id", String(255), nullable=False),
    Column("player_uuid", String(36), nullable=False),
    Column("connection_id", String(36), nullable=False),
    Column("start_tick", BigInteger, nullable=False),
    Column("end_tick", BigInteger, nullable=False),
    # Canonical UTF-8 JSON bytes, never a dialect-specific JSON value.
    Column("source_replays_json", LargeBinary, nullable=False),
    Column("sensitive", Boolean, nullable=False),
    Column("provenance_json", LargeBinary, nullable=False),
    CheckConstraint("singleton = 1", name="singleton"),
    CheckConstraint("start_tick <= end_tick", name="tick_order"),
)

blobs = Table(
    "blobs",
    metadata,
    Column("sha256", String(64), primary_key=True),
    Column("kind", String(32), nullable=False),
    Column("encoding", String(16), nullable=False),
    Column("uncompressed_size", BigInteger, nullable=False),
    Column("compressed_size", BigInteger, nullable=False),
    Column("data", LargeBinary, nullable=False),
    CheckConstraint(
        "kind IN ('section', 'entity', 'block_entity', 'frame', 'player_state')",
        name="known_kind",
    ),
    CheckConstraint("encoding = 'zlib'", name="zlib_encoding"),
    CheckConstraint("uncompressed_size >= 0", name="nonnegative_uncompressed_size"),
    CheckConstraint("compressed_size >= 0", name="nonnegative_compressed_size"),
)

frames = Table(
    "frames",
    metadata,
    Column("server_tick", BigInteger, primary_key=True),
    Column("frame_id", String(255), nullable=False, unique=True),
    Column("replay_tick", BigInteger),
    Column("dimension", String(255), nullable=False),
    Column("subject_x", Float, nullable=False),
    Column("subject_y", Float, nullable=False),
    Column("subject_z", Float, nullable=False),
    Column("coverage_complete", Boolean, nullable=False),
    Column("payload_sha256", String(64), ForeignKey("blobs.sha256"), nullable=False),
)

section_versions = Table(
    "section_versions",
    metadata,
    Column("version_id", BigInteger, primary_key=True, autoincrement=False),
    Column("dimension", String(255), nullable=False),
    Column("section_x", BigInteger, nullable=False),
    Column("section_y", BigInteger, nullable=False),
    Column("section_z", BigInteger, nullable=False),
    Column("start_tick", BigInteger, nullable=False),
    Column("end_tick", BigInteger, nullable=False),
    Column("blob_sha256", String(64), ForeignKey("blobs.sha256"), nullable=False),
    CheckConstraint("version_id > 0", name="positive_version_id"),
    CheckConstraint("start_tick < end_tick", name="tick_order"),
    UniqueConstraint(
        "dimension",
        "section_x",
        "section_y",
        "section_z",
        "start_tick",
        name="logical_version",
    ),
)
Index(
    "ix_section_versions_dimension_ticks",
    section_versions.c.dimension,
    section_versions.c.start_tick,
    section_versions.c.end_tick,
)

entity_versions = Table(
    "entity_versions",
    metadata,
    Column("version_id", BigInteger, primary_key=True, autoincrement=False),
    Column("instance_id", String(255), nullable=False),
    Column("network_id", BigInteger),
    Column("dimension", String(255), nullable=False),
    Column("type_id", String(255), nullable=False),
    Column("start_tick", BigInteger, nullable=False),
    Column("end_tick", BigInteger, nullable=False),
    Column("min_x", Float, nullable=False),
    Column("min_y", Float, nullable=False),
    Column("min_z", Float, nullable=False),
    Column("max_x", Float, nullable=False),
    Column("max_y", Float, nullable=False),
    Column("max_z", Float, nullable=False),
    Column("blob_sha256", String(64), ForeignKey("blobs.sha256"), nullable=False),
    CheckConstraint("version_id > 0", name="positive_version_id"),
    CheckConstraint("start_tick < end_tick", name="tick_order"),
    CheckConstraint(
        "min_x <= max_x AND min_y <= max_y AND min_z <= max_z",
        name="ordered_bounds",
    ),
    UniqueConstraint("instance_id", "start_tick", name="logical_version"),
)
Index(
    "ix_entity_versions_dimension_ticks",
    entity_versions.c.dimension,
    entity_versions.c.start_tick,
    entity_versions.c.end_tick,
)
Index(
    "ix_entity_versions_network_ticks",
    entity_versions.c.network_id,
    entity_versions.c.start_tick,
    entity_versions.c.end_tick,
    entity_versions.c.instance_id,
)

block_entity_versions = Table(
    "block_entity_versions",
    metadata,
    Column("version_id", BigInteger, primary_key=True, autoincrement=False),
    Column("dimension", String(255), nullable=False),
    Column("block_x", BigInteger, nullable=False),
    Column("block_y", BigInteger, nullable=False),
    Column("block_z", BigInteger, nullable=False),
    Column("type_id", String(255), nullable=False),
    Column("start_tick", BigInteger, nullable=False),
    Column("end_tick", BigInteger, nullable=False),
    Column("blob_sha256", String(64), ForeignKey("blobs.sha256"), nullable=False),
    CheckConstraint("version_id > 0", name="positive_version_id"),
    CheckConstraint("start_tick < end_tick", name="tick_order"),
    UniqueConstraint(
        "dimension",
        "block_x",
        "block_y",
        "block_z",
        "start_tick",
        name="logical_version",
    ),
)
Index(
    "ix_block_entity_versions_dimension_ticks",
    block_entity_versions.c.dimension,
    block_entity_versions.c.start_tick,
    block_entity_versions.c.end_tick,
)

player_states = Table(
    "player_states",
    metadata,
    Column("server_tick", BigInteger, ForeignKey("frames.server_tick"), primary_key=True),
    Column("entity_version_id", BigInteger, ForeignKey("entity_versions.version_id"), nullable=False),
    Column("entity_instance_id", String(255), nullable=False),
    Column("entity_id", BigInteger, nullable=False),
    Column("dimension", String(255), nullable=False),
    Column("position_x", Float, nullable=False),
    Column("position_y", Float, nullable=False),
    Column("position_z", Float, nullable=False),
    Column("velocity_x", Float, nullable=False),
    Column("velocity_y", Float, nullable=False),
    Column("velocity_z", Float, nullable=False),
    Column("yaw", Float, nullable=False),
    Column("pitch", Float, nullable=False),
    Column("head_yaw", Float, nullable=False),
    Column("alive", Boolean, nullable=False),
    Column("on_ground", Boolean, nullable=False),
    Column("pose", String(64), nullable=False),
    Column("sprinting", Boolean, nullable=False),
    Column("sneaking", Boolean, nullable=False),
    Column("swimming", Boolean, nullable=False),
    Column("fall_flying", Boolean, nullable=False),
    Column("using_item", Boolean, nullable=False),
    Column("use_item_remaining_ticks", Integer, nullable=False),
    Column("game_mode", String(64), nullable=False),
    Column("health", Float, nullable=False),
    Column("max_health", Float, nullable=False),
    Column("absorption", Float, nullable=False),
    Column("armor", Integer, nullable=False),
    Column("air", Integer, nullable=False),
    Column("max_air", Integer, nullable=False),
    Column("food_level", Integer, nullable=False),
    Column("saturation", Float, nullable=False),
    Column("experience_level", Integer, nullable=False),
    Column("experience_progress", Float, nullable=False),
    Column("total_experience", Integer, nullable=False),
    Column("selected_slot", Integer, nullable=False),
    Column("state_barrier_apply_sequence", BigInteger, nullable=False),
    Column("payload_sha256", String(64), ForeignKey("blobs.sha256"), nullable=False),
)
Index("ix_player_states_entity_instance", player_states.c.entity_instance_id)

PORTABLE_TABLES = (
    schema_info,
    scene_meta,
    blobs,
    frames,
    player_states,
    section_versions,
    entity_versions,
    block_entity_versions,
)

__all__ = [
    "PORTABLE_TABLES",
    "SCENE_STORE_V2_SCHEMA",
    "SCENE_STORE_V2_SCHEMA_VERSION",
    "SCENE_STORE_V2_USER_VERSION",
    "block_entity_versions",
    "blobs",
    "entity_versions",
    "frames",
    "metadata",
    "player_states",
    "scene_meta",
    "schema_info",
    "section_versions",
]
