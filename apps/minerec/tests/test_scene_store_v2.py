from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
import tempfile
import unittest
import zlib
from contextlib import closing
from pathlib import Path
from typing import cast

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from minerec.processing.scene.schema_v2 import FRAME_ALIGNMENT_SELECT, PORTABLE_TABLES
from minerec.processing.scene.store import SceneIdentity, SceneStoreBuilder
from minerec.processing.scene.store_v2 import (
    SceneStoreV2,
    SceneStoreV2Error,
    SceneStoreV2ValidationError,
    finalize_scene_store_v2,
    validate_scene_store_v2,
)

PLAYER = "00000000-0000-4000-8000-000000000001"
CONNECTION = "00000000-0000-4000-8000-000000000002"
IDENTITY = SceneIdentity("session-v2", PLAYER, CONNECTION)
DIMENSION = "minecraft:overworld"
STONE = {"name": "minecraft:stone", "properties": {}}


def _position(tick: int) -> tuple[float, float, float]:
    return tick - 8.75, 64.0, 2.0


def _entity_payload(tick: int, instance_id: str) -> dict[str, object]:
    x, y, z = _position(tick)
    return {
        "instance_id": instance_id,
        "dimension": DIMENSION,
        "type_id": "minecraft:player",
        "network_id": 7,
        "uuid": PLAYER,
        "position": [x, y, z],
        "velocity": [0.1, 0.0, 0.0],
        "rotation": [12.0, -3.0],
        "aabb": [x - 0.3, y, z - 0.3, x + 0.3, y + 1.8, z + 0.3],
    }


def _state(tick: int) -> dict[str, object]:
    x, y, z = _position(tick)
    return {
        "schema_version": 1,
        "record_type": "player_state",
        "session_id": IDENTITY.session_id,
        "epoch_index": 0,
        "server_tick": tick,
        "sequence": tick * 10,
        "recorded_at_ns": tick * 1_000,
        "player_uuid": PLAYER,
        "player_name": "PlayerOne",
        "connection_id": CONNECTION,
        "entity_id": 7,
        "dimension": DIMENSION,
        "position": {"x": x, "y": y, "z": z},
        "velocity": {"x": 0.1, "y": 0.0, "z": 0.0},
        "rotation": {"yaw": 12.0, "pitch": -3.0, "head_yaw": 13.0},
        "alive": True,
        "on_ground": True,
        "pose": "standing",
        "sprinting": tick % 2 == 0,
        "sneaking": False,
        "swimming": False,
        "fall_flying": False,
        "using_item": False,
        "use_item_remaining_ticks": 0,
        "game_mode": "survival",
        "health": 20.0 - (tick - 10),
        "max_health": 20.0,
        "absorption": 0.0,
        "armor": 2,
        "air": 300,
        "max_air": 300,
        "food_level": 19,
        "saturation": 4.5,
        "experience_level": 3,
        "experience_progress": 0.25,
        "total_experience": 32,
        "selected_slot": 2,
        "state_barrier_apply_sequence": tick - 10,
        "abilities": {
            "invulnerable": False,
            "flying": False,
            "may_fly": False,
            "instant_build": False,
            "may_build": True,
        },
        "effects": [],
        "vehicle": None,
        "passengers": [],
        "inventory": [
            {
                "slot": 2,
                "item": "minecraft:stone",
                "count": tick - 8,
                "damage": 0,
                "max_damage": 0,
            }
        ],
    }


def _write_v1(
    path: Path,
    *,
    source_replays: tuple[dict[str, object], ...] = (),
    provenance: dict[str, object] | None = None,
) -> None:
    with SceneStoreBuilder(
        IDENTITY,
        start_tick=10,
        end_tick=13,
        source_replays=source_replays,
        provenance=provenance,
    ) as builder:
        for tick in range(10, 14):
            frame_id = f"frame-{tick}"
            if source_replays:
                source_index = min((tick - 10) // 2, len(source_replays) - 1)
                frame_id = f"{source_replays[source_index]['segment_id']}:{tick}"
            builder.add_frame(
                tick,
                frame_id=frame_id,
                replay_tick=tick + 100,
                dimension=DIMENSION,
                subject_position=_position(tick),
                coverage_complete=True,
                metadata={"tick": tick},
            )
        builder.set_section(10, DIMENSION, (0, 4, 0), (STONE,), [0] * 4096)
        builder.set_block_entity(
            10,
            DIMENSION,
            (2, 64, 2),
            {
                "dimension": DIMENSION,
                "position": [2, 64, 2],
                "type_id": "minecraft:chest",
                "items": [],
            },
        )
        builder.set_entity(10, "subject-before-respawn", _entity_payload(10, "subject-before-respawn"))
        builder.set_entity(11, "subject-before-respawn", _entity_payload(11, "subject-before-respawn"))
        builder.remove_entity(12, "subject-before-respawn")
        builder.set_entity(12, "subject-after-respawn", _entity_payload(12, "subject-after-respawn"))
        builder.set_entity(13, "subject-after-respawn", _entity_payload(13, "subject-after-respawn"))
        builder.publish(path, expected_ticks=range(10, 14))


def _write_states(path: Path, ticks: tuple[int, ...] = (10, 11, 12, 13)) -> None:
    path.write_bytes(b"".join(json.dumps(_state(tick), sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n" for tick in ticks))


def _source_replays(root: Path) -> tuple[dict[str, object], ...]:
    return (
        {
            "segment_id": "segment-alpha",
            "segment_ordinal": 3,
            "path": str((root / "sealed" / "alpha.zip").resolve()),
            "sha256": "1" * 64,
            "size_bytes": 100,
            "format": "flashback",
        },
        {
            "segment_id": "segment-beta",
            "segment_ordinal": 7,
            "path": str((root / "sealed" / "beta.zip").resolve()),
            "sha256": "2" * 64,
            "size_bytes": 200,
            "format": "flashback",
        },
    )


def _provenance(
    root: Path,
    source_replays: tuple[dict[str, object], ...],
) -> dict[str, object]:
    return {
        "scope": "client_visible",
        "metadata_policy": "full_packet_metadata",
        "result": {
            "schema_version": 1,
            "result_type": "mc-recorder-scene-extraction-result-v1",
            "status": "complete",
            "job_id": "portable-v2-test",
            "session_id": IDENTITY.session_id,
            "player_uuid": IDENTITY.player_uuid,
            "connection_id": IDENTITY.connection_id,
            "global_start_tick": 10,
            "global_end_tick": 13,
            "scope": "client_visible",
            "metadata_policy": "full_packet_metadata",
            "flashback_capture_contract": "client_visible_scene_v1",
            "source_replays": list(source_replays),
            "subject_poses": {
                "format": "mc-recorder-subject-poses-v1",
                "path": str((root / "work" / "subject-poses.jsonl").resolve()),
                "sha256": "3" * 64,
                "size_bytes": 100,
                "record_count": 4,
                "first_tick": 10,
                "last_tick": 13,
                "source_epochs": [
                    {
                        "epoch_index": 0,
                        "events_sha256": "4" * 64,
                        "events_size_bytes": 1000,
                        "record_count": 20,
                    }
                ],
            },
            "stream": {
                "path": str((root / "work" / "scene-stream").resolve()),
                "format": "mc-recorder-scene-stream-v1",
                "frames_index": "frames.jsonl",
                "frames_size_bytes": 1,
                "frames_sha256": "5" * 64,
                "frame_count": 4,
                "changes_index": "changes.jsonl",
                "changes_size_bytes": 1,
                "changes_sha256": "6" * 64,
                "change_count": 0,
                "blobs_directory": "blobs",
                "blob_count": 0,
                "blob_bytes": 0,
            },
            "ignored_packet_counts": {},
            "covered_tick_count": 4,
        },
    }


def _insert_json_blob(
    connection: sqlite3.Connection,
    kind: str,
    payload: dict[str, object],
) -> str:
    value = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    digest = hashlib.sha256(value).hexdigest()
    compressed = zlib.compress(value, level=9)
    connection.execute(
        "INSERT INTO blobs (sha256, kind, encoding, uncompressed_size, compressed_size, data) VALUES (?, ?, 'zlib', ?, ?, ?)",
        (digest, kind, len(value), len(compressed), compressed),
    )
    return digest


class PortableSchemaTest(unittest.TestCase):
    def test_all_base_tables_compile_for_postgresql_without_sqlite_extensions(self) -> None:
        ddl = "\n".join(str(CreateTable(table).compile(dialect=postgresql.dialect())) for table in PORTABLE_TABLES)
        alignment_query = str(FRAME_ALIGNMENT_SELECT.compile(dialect=postgresql.dialect()))
        for name in (
            "schema_info",
            "scene_meta",
            "blobs",
            "frames",
            "player_states",
            "section_versions",
            "entity_versions",
            "block_entity_versions",
        ):
            self.assertIn(f"CREATE TABLE {name}", ddl)
        self.assertIn("BYTEA", ddl)
        self.assertIn("BIGINT", ddl)
        self.assertNotIn("RTREE", ddl.upper())
        self.assertNotIn("PRAGMA", ddl.upper())
        self.assertNotIn("ROWID", ddl.upper())
        self.assertIn("FROM frames", alignment_query)
        self.assertIn("ORDER BY frames.server_tick", alignment_query)


class SceneStoreV2Test(unittest.TestCase):
    def test_sqlite_round_trip_relinks_respawn_and_preserves_full_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "scene-v1.sqlite3"
            states = root / "states.jsonl"
            output = root / "scene.sqlite3"
            _write_v1(source)
            _write_states(states)

            info = finalize_scene_store_v2(source, states, output)
            self.assertEqual((10, 11, 12, 13), info.ticks)
            self.assertEqual(4, info.frame_count)

            with SceneStoreV2(output) as store:
                before = store.player_state("frame-11")
                after = store.player_state(12)
                self.assertEqual("subject-before-respawn", before.entity_instance_id)
                self.assertEqual("subject-after-respawn", after.entity_instance_id)
                self.assertNotEqual(before.entity_version_id, after.entity_version_id)
                self.assertEqual("minecraft:stone", after.payload["inventory"][0]["item"])
                self.assertEqual(_position(12), after.position)
                self.assertEqual(
                    ("subject-before-respawn", "subject-before-respawn", "subject-after-respawn", "subject-after-respawn"),
                    tuple(point.entity_instance_id for point in store.trajectory()),
                )
                crop = store.materialize_crop(12, (2, 64, 2), (1, 1, 1))
                self.assertEqual("minecraft:stone", crop.cell(0, 0, 0)[1]["name"])  # ty:ignore[not-subscriptable]
                self.assertEqual("subject-after-respawn", crop.entities[0].instance_id)
                self.assertEqual("minecraft:chest", crop.block_entities[0].type_id)
                scene_slice = store.slice(12, "y", 64, (2, 64, 2), 0)
                self.assertTrue(scene_slice.cells[0].covered)

    def test_normalizes_replay_paths_and_redacts_host_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "scene-v1.sqlite3"
            states = root / "states.jsonl"
            output = root / "scene.sqlite3"
            source_replays = _source_replays(root)
            _write_v1(
                source,
                source_replays=source_replays,
                provenance=_provenance(root, source_replays),
            )
            _write_states(states)

            info = finalize_scene_store_v2(source, states, output)

            self.assertEqual(
                (
                    "replays/000000--segment-alpha.zip",
                    "replays/000001--segment-beta.zip",
                ),
                tuple(item["path"] for item in info.source_replays),
            )
            self.assertEqual(
                (3, 7),
                tuple(item["segment_ordinal"] for item in info.source_replays),
            )
            provenance_text = json.dumps(
                info.provenance,
                default=lambda value: dict(value),
            )
            self.assertNotIn(str(root), provenance_text)
            self.assertIn("recorder-intermediate://host-path-removed", provenance_text)

            with SceneStoreV2(output) as store:
                alignments = tuple(store.iter_frame_alignments())
            self.assertEqual((10, 11, 12, 13), tuple(row.server_tick for row in alignments))
            self.assertEqual(
                ("segment-alpha", "segment-alpha", "segment-beta", "segment-beta"),
                tuple(row.segment_id for row in alignments),
            )
            self.assertEqual((110, 111, 112, 113), tuple(row.replay_tick for row in alignments))

    def test_rejects_incomplete_private_player_payload_before_publication(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "scene-v1.sqlite3"
            states = root / "states.jsonl"
            output = root / "scene.sqlite3"
            _write_v1(source)
            records = [_state(tick) for tick in (10, 11, 12, 13)]
            abilities = cast(dict[str, object], records[0]["abilities"])
            abilities.pop("may_build")
            states.write_bytes(b"".join(json.dumps(record, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n" for record in records))

            with self.assertRaisesRegex(
                SceneStoreV2Error,
                "complete v1 ability fields",
            ):
                finalize_scene_store_v2(source, states, output)
            self.assertFalse(output.exists())

    def test_validation_rejects_incomplete_private_player_blob(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "scene-v1.sqlite3"
            states = root / "states.jsonl"
            output = root / "scene.sqlite3"
            _write_v1(source)
            _write_states(states)
            finalize_scene_store_v2(source, states, output)

            with closing(sqlite3.connect(output)) as connection:
                row = connection.execute("SELECT player_states.server_tick, blobs.data FROM player_states JOIN blobs ON blobs.sha256 = player_states.payload_sha256 ORDER BY player_states.server_tick LIMIT 1").fetchone()
                payload = json.loads(zlib.decompress(row[1]))
                payload["inventory"].append(dict(payload["inventory"][0]))
                digest = _insert_json_blob(connection, "player_state", payload)
                connection.execute(
                    "UPDATE player_states SET payload_sha256 = ? WHERE server_tick = ?",
                    (digest, row[0]),
                )
                connection.commit()

            with self.assertRaisesRegex(
                SceneStoreV2ValidationError,
                "inventory must have unique slots",
            ):
                validate_scene_store_v2(output)

    def test_validation_binds_entity_and_block_entity_rows_to_payloads(self) -> None:
        for table, message in (
            ("entity_versions", "entity version .* typed columns"),
            ("block_entity_versions", "block-entity version .* type"),
        ):
            with self.subTest(table=table), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                source = root / "scene-v1.sqlite3"
                states = root / "states.jsonl"
                output = root / "scene.sqlite3"
                _write_v1(source)
                _write_states(states)
                finalize_scene_store_v2(source, states, output)

                with closing(sqlite3.connect(output)) as connection:
                    connection.execute(f"UPDATE {table} SET type_id = 'minecraft:zombie' WHERE version_id = (SELECT MIN(version_id) FROM {table})")
                    connection.commit()

                with self.assertRaisesRegex(SceneStoreV2ValidationError, message):
                    validate_scene_store_v2(output)

    def test_validation_requires_linked_entity_player_type_and_uuid(self) -> None:
        for mutation, message in (
            ("uuid", "linked player entity UUID"),
            ("type", "linked entity type"),
        ):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                source = root / "scene-v1.sqlite3"
                states = root / "states.jsonl"
                output = root / "scene.sqlite3"
                _write_v1(source)
                _write_states(states)
                finalize_scene_store_v2(source, states, output)

                with closing(sqlite3.connect(output)) as connection:
                    row = connection.execute(
                        "SELECT entity_versions.version_id, blobs.data "
                        "FROM player_states JOIN entity_versions "
                        "ON entity_versions.version_id = player_states.entity_version_id "
                        "JOIN blobs ON blobs.sha256 = entity_versions.blob_sha256 "
                        "ORDER BY player_states.server_tick LIMIT 1"
                    ).fetchone()
                    payload = json.loads(zlib.decompress(row[1]))
                    if mutation == "uuid":
                        payload["uuid"] = "00000000-0000-4000-8000-000000000099"
                    else:
                        payload["type_id"] = "minecraft:zombie"
                    digest = _insert_json_blob(connection, "entity", payload)
                    connection.execute(
                        "UPDATE entity_versions SET blob_sha256 = ?, type_id = ? WHERE version_id = ?",
                        (digest, payload["type_id"], row[0]),
                    )
                    connection.commit()

                with self.assertRaisesRegex(SceneStoreV2ValidationError, message):
                    validate_scene_store_v2(output)

    def test_exact_frame_state_coverage_is_required(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "scene-v1.sqlite3"
            states = root / "states.jsonl"
            output = root / "scene.sqlite3"
            _write_v1(source)
            _write_states(states, (10, 11, 13))

            with self.assertRaisesRegex(SceneStoreV2Error, "exactly match"):
                finalize_scene_store_v2(source, states, output)
            self.assertFalse(output.exists())

    def test_explicit_version_ids_drive_rtree_and_tampering_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "scene-v1.sqlite3"
            states = root / "states.jsonl"
            output = root / "scene.sqlite3"
            _write_v1(source)
            _write_states(states)
            finalize_scene_store_v2(source, states, output)

            with closing(sqlite3.connect(output)) as connection:
                entity_ids = connection.execute("SELECT version_id FROM entity_versions ORDER BY version_id").fetchall()
                rtree_ids = connection.execute("SELECT version_id FROM entity_versions_rtree ORDER BY version_id").fetchall()
                trigger_sql = "\n".join(row[0] for row in connection.execute("SELECT sql FROM sqlite_master WHERE type = 'trigger' ORDER BY name"))
                self.assertEqual(entity_ids, rtree_ids)
                self.assertNotIn("rowid", trigger_sql.lower())
                connection.execute(
                    "DELETE FROM entity_versions_rtree WHERE version_id = ?",
                    (entity_ids[0][0],),
                )
                connection.commit()

            with self.assertRaisesRegex(
                SceneStoreV2ValidationError,
                "R-tree",
            ):
                validate_scene_store_v2(output)

    def test_noncanonical_zlib_representation_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "scene-v1.sqlite3"
            states = root / "states.jsonl"
            output = root / "scene.sqlite3"
            _write_v1(source)
            _write_states(states)
            finalize_scene_store_v2(source, states, output)

            with closing(sqlite3.connect(output)) as connection:
                row = connection.execute("SELECT blobs.sha256, blobs.data FROM blobs JOIN player_states ON player_states.payload_sha256 = blobs.sha256 LIMIT 1").fetchone()
                value = zlib.decompress(row[1])
                noncanonical = zlib.compress(value, level=1)
                self.assertNotEqual(row[1], noncanonical)
                connection.execute(
                    "UPDATE blobs SET compressed_size = ?, data = ? WHERE sha256 = ?",
                    (len(noncanonical), noncanonical, row[0]),
                )
                connection.commit()

            with self.assertRaisesRegex(SceneStoreV2ValidationError, "canonical zlib"):
                validate_scene_store_v2(output)


if __name__ == "__main__":
    unittest.main()
