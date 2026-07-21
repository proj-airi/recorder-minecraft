from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from mc_recorder.scene_store import (
    DEFAULT_MAX_CROP_CELLS,
    SceneIdentity,
    SceneStore,
    SceneStoreBuilder,
    SceneStoreError,
    SceneStoreValidationError,
    canonical_json_blob,
    canonical_section_blob,
    compact_scene_stream,
    scene_slice_to_json,
    validate_scene_store,
)


IDENTITY = SceneIdentity("session-a", "player-a", "connection-a")
DIMENSION = "minecraft:overworld"
AIR = {"name": "minecraft:air", "properties": {}}
STONE = {"name": "minecraft:stone", "properties": {}}
DIRT = {"name": "minecraft:dirt", "properties": {}}


def _indices(fill: int = 0) -> list[int]:
    return [fill] * 4096


def _section_offset(x: int, y: int, z: int) -> int:
    return (y * 16 + z) * 16 + x


def _add_frames(
    builder: SceneStoreBuilder,
    ticks: tuple[int, ...],
    *,
    complete: bool = True,
) -> None:
    for tick in ticks:
        builder.add_frame(
            tick,
            frame_id=f"frame-{tick}",
            replay_tick=tick + 1000,
            dimension=DIMENSION,
            subject_position=(tick + 0.25, 64.0, -2.5),
            coverage_complete=complete,
            reasons=() if complete else ("unsupported_packet",),
            metadata={"tick_label": str(tick)},
        )


class SceneStoreBuilderTest(unittest.TestCase):
    def test_structurally_deduplicates_locally_canonical_sections(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "scene.sqlite3"
            first = _indices()
            first[0] = 1
            second = _indices(1)
            second[0] = 0
            with SceneStoreBuilder(
                IDENTITY,
                start_tick=1,
                end_tick=2,
                source_replays=({"path": "replay.zip", "sha256": "a" * 64},),
                provenance={"decoder": "test"},
            ) as builder:
                _add_frames(builder, (1, 2))
                builder.set_section(1, DIMENSION, (0, 4, 0), (AIR, STONE), first)
                builder.set_section(1, DIMENSION, (1, 4, 0), (STONE, AIR), second)
                info = builder.publish(path, expected_ticks=(1, 2))

            self.assertEqual(info.identity, IDENTITY)
            self.assertEqual(info.start_tick, 1)
            self.assertEqual(info.end_tick, 2)
            self.assertEqual(info.ticks, (1, 2))
            self.assertEqual(info.frame_count, 2)
            self.assertTrue(info.coverage_complete)
            self.assertTrue(info.sensitive)
            self.assertEqual(info.source_replays[0]["path"], "replay.zip")
            with sqlite3.connect(path) as connection:
                section_blobs = connection.execute(
                    "SELECT COUNT(*) FROM blobs WHERE kind = 'section'"
                ).fetchone()[0]
                version_hashes = connection.execute(
                    "SELECT DISTINCT blob_sha256 FROM section_versions"
                ).fetchall()
            self.assertEqual(section_blobs, 1)
            self.assertEqual(len(version_hashes), 1)

    def test_random_tick_access_tracks_changes_and_unloads_as_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "scene.sqlite3"
            with SceneStoreBuilder(
                IDENTITY, start_tick=10, end_tick=14
            ) as builder:
                _add_frames(builder, (10, 12, 14))
                builder.set_section(10, DIMENSION, (0, 0, 0), (STONE,), _indices())
                builder.set_section(12, DIMENSION, (0, 0, 0), (DIRT,), _indices())
                builder.unload_section(14, DIMENSION, (0, 0, 0))
                builder.publish(path, expected_ticks=(10, 12, 14))

            with SceneStore(path) as store:
                newest = store.materialize_crop("frame-14", (0, 0, 0), (1, 1, 1))
                oldest = store.materialize_crop(10, (0, 0, 0), (1, 1, 1))
                middle = store.materialize_crop("frame-12", (0, 0, 0), (1, 1, 1))
                again = store.materialize_crop(10, (0, 0, 0), (1, 1, 1))

            self.assertEqual(newest.cell(0, 0, 0), (False, None))
            self.assertEqual(oldest.cell(0, 0, 0)[1]["name"], "minecraft:stone")
            self.assertEqual(middle.cell(0, 0, 0)[1]["name"], "minecraft:dirt")
            self.assertEqual(again, oldest)

    def test_entity_network_id_can_be_reused_by_a_new_nonoverlapping_instance(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "scene.sqlite3"
            with SceneStoreBuilder(
                IDENTITY, start_tick=1, end_tick=4
            ) as builder:
                _add_frames(builder, (1, 2, 3, 4))
                builder.set_section(1, DIMENSION, (0, 4, 0), (AIR,), _indices())
                builder.set_entity(
                    1,
                    "spawn-1",
                    {
                        "dimension": DIMENSION,
                        "type_id": "minecraft:pig",
                        "network_id": 7,
                        "uuid": "uuid-pig",
                        "position": [1.0, 64.0, 1.0],
                        "velocity": [0.0, 0.0, 0.0],
                        "rotation": [20.0, 5.0],
                        "aabb": [0.5, 64.0, 0.5, 1.5, 65.0, 1.5],
                        "metadata": {"custom_name": "Pig One", "flags": [1, 2]},
                    },
                )
                builder.remove_entity(3, "spawn-1")
                builder.set_entity(
                    3,
                    "spawn-2",
                    {
                        "dimension": DIMENSION,
                        "type_id": "minecraft:cow",
                        "network_id": 7,
                        "uuid": "uuid-cow",
                        "position": [2.0, 64.0, 2.0],
                        "aabb": [1.5, 64.0, 1.5, 2.5, 65.5, 2.5],
                        "metadata": {"equipment": {"head": "minecraft:carved_pumpkin"}},
                    },
                )
                builder.publish(path, expected_ticks=(1, 2, 3, 4))

            with SceneStore(path) as store:
                before = store.materialize_crop(2, (0, 64, 0), (16, 2, 16))
                after = store.materialize_crop(3, (0, 64, 0), (16, 2, 16))

            self.assertEqual([entity.instance_id for entity in before.entities], ["spawn-1"])
            self.assertEqual([entity.instance_id for entity in after.entities], ["spawn-2"])
            self.assertEqual(after.entities[0].network_id, 7)
            self.assertEqual(
                after.entities[0].payload["metadata"]["equipment"]["head"],
                "minecraft:carved_pumpkin",
            )
            with self.assertRaises(TypeError):
                after.entities[0].payload["metadata"] = {}  # type: ignore[index]

    def test_rejects_overlapping_live_network_ids(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with SceneStoreBuilder(
                IDENTITY, start_tick=1, end_tick=2
            ) as builder:
                _add_frames(builder, (1, 2))
                payload = {
                    "dimension": DIMENSION,
                    "type_id": "minecraft:pig",
                    "network_id": 4,
                    "position": [0.0, 64.0, 0.0],
                    "aabb": [0.0, 64.0, 0.0, 1.0, 65.0, 1.0],
                }
                builder.set_entity(1, "first", payload)
                with self.assertRaisesRegex(SceneStoreError, "already active"):
                    builder.set_entity(2, "second", payload)

    def test_slice_has_world_coordinates_and_intersecting_scene_objects(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "scene.sqlite3"
            indices = _indices()
            indices[_section_offset(8, 0, 8)] = 1
            with SceneStoreBuilder(
                IDENTITY, start_tick=5, end_tick=5
            ) as builder:
                _add_frames(builder, (5,))
                builder.set_section(5, DIMENSION, (0, 4, 0), (AIR, STONE), indices)
                builder.set_entity(
                    5,
                    "pig",
                    {
                        "dimension": DIMENSION,
                        "type_id": "minecraft:pig",
                        "network_id": 9,
                        "position": [8.5, 64.0, 8.5],
                        "aabb": [8.1, 64.0, 8.1, 8.9, 64.9, 8.9],
                    },
                )
                builder.set_entity(
                    5,
                    "high-pig",
                    {
                        "dimension": DIMENSION,
                        "type_id": "minecraft:pig",
                        "network_id": 10,
                        "position": [8.5, 66.0, 8.5],
                        "aabb": [8.1, 66.0, 8.1, 8.9, 66.9, 8.9],
                    },
                )
                builder.set_block_entity(
                    5,
                    DIMENSION,
                    (8, 64, 8),
                    {
                        "type_id": "minecraft:chest",
                        "items": [{"id": "minecraft:diamond", "count": 2}],
                    },
                )
                builder.publish(path, expected_ticks=(5,))

            with SceneStore(path) as store:
                value = store.slice("frame-5", "y", 64, (8, 64, 8), 1)
                as_json = scene_slice_to_json(value)

            self.assertEqual((value.row_axis, value.column_axis), ("z", "x"))
            self.assertEqual((value.width, value.height), (3, 3))
            self.assertTrue(value.coverage_complete)
            center = value.cells[4]
            self.assertEqual(center.world_position, (8, 64, 8))
            self.assertEqual(center.block_state["name"], "minecraft:stone")
            self.assertEqual([entity.instance_id for entity in value.entities], ["pig"])
            self.assertEqual(value.block_entities[0].type_id, "minecraft:chest")
            self.assertEqual(as_json["world_coordinate"], 64)
            self.assertEqual(as_json["cells"][4]["world_position"], [8, 64, 8])
            self.assertEqual(as_json["entities"][0]["payload"]["type_id"], "minecraft:pig")

    def test_enforces_crop_and_slice_bounds(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "scene.sqlite3"
            with SceneStoreBuilder(
                IDENTITY, start_tick=1, end_tick=1
            ) as builder:
                _add_frames(builder, (1,))
                builder.publish(path, expected_ticks=(1,))
            with SceneStore(path) as store:
                with self.assertRaisesRegex(SceneStoreError, "limit"):
                    store.materialize_crop(
                        1,
                        (0, 0, 0),
                        (DEFAULT_MAX_CROP_CELLS + 1, 1, 1),
                    )
                with self.assertRaisesRegex(SceneStoreError, "0 to 64"):
                    store.slice(1, "y", 64, (0, 64, 0), 65)
                with self.assertRaisesRegex(SceneStoreError, "not found"):
                    store.frame(2)


class SceneStoreValidationTest(unittest.TestCase):
    def _store(self, root: Path) -> Path:
        path = root / "scene.sqlite3"
        with SceneStoreBuilder(IDENTITY, start_tick=1, end_tick=2) as builder:
            _add_frames(builder, (1, 2))
            builder.set_section(1, DIMENSION, (0, 0, 0), (STONE,), _indices())
            builder.publish(path, expected_ticks=(1, 2))
        return path

    def test_rejects_corrupt_content_addressed_blob(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = self._store(Path(temporary))
            with sqlite3.connect(path) as connection:
                connection.execute(
                    "UPDATE blobs SET zlib_data = ? WHERE kind = 'section'", (b"broken",)
                )
                connection.execute(
                    "UPDATE blobs SET compressed_size = 6 WHERE kind = 'section'"
                )
            with self.assertRaisesRegex(SceneStoreValidationError, "zlib"):
                validate_scene_store(path)

    def test_rejects_persisted_interval_overlap(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = self._store(Path(temporary))
            with sqlite3.connect(path) as connection:
                digest = connection.execute(
                    "SELECT blob_sha256 FROM section_versions LIMIT 1"
                ).fetchone()[0]
                connection.execute(
                    "INSERT INTO section_versions VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (DIMENSION, 0, 0, 0, 2, 3, digest),
                )
            with self.assertRaisesRegex(SceneStoreValidationError, "overlapping"):
                validate_scene_store(path)

    def test_rejects_store_symlink_and_identity_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = self._store(root)
            link = root / "linked.sqlite3"
            os.symlink(path, link)
            with self.assertRaisesRegex(SceneStoreError, "symlinked"):
                validate_scene_store(link)
            with self.assertRaisesRegex(SceneStoreValidationError, "identity"):
                validate_scene_store(path, expected_session_id="other")

    def test_failed_publish_does_not_replace_existing_store(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = self._store(root)
            original = path.read_bytes()
            with SceneStoreBuilder(IDENTITY, start_tick=3, end_tick=4) as builder:
                _add_frames(builder, (3, 4))
                with self.assertRaisesRegex(SceneStoreError, "expected ticks"):
                    builder.publish(path, expected_ticks=(3,))
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(validate_scene_store(path).ticks, (1, 2))


class SceneStreamCompactorTest(unittest.TestCase):
    def test_compacts_frames_changes_and_verified_external_blobs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            job = root / "job"
            stream = job / "stream"
            blobs = stream / "blobs"
            blobs.mkdir(parents=True)
            section = canonical_section_blob((STONE,), _indices())
            entity = canonical_json_blob(
                {
                    "dimension": DIMENSION,
                    "type_id": "minecraft:item",
                    "network_id": 20,
                    "position": [1.5, 1.0, 1.5],
                    "aabb": [1.25, 1.0, 1.25, 1.75, 1.5, 1.75],
                    "metadata": {"stack": {"id": "minecraft:apple", "count": 1}},
                }
            )
            import hashlib

            section_hash = hashlib.sha256(section).hexdigest()
            entity_hash = hashlib.sha256(entity).hexdigest()
            (blobs / f"{section_hash}.zlib").write_bytes(zlib.compress(section))
            (blobs / f"{entity_hash}.zlib").write_bytes(zlib.compress(entity))
            snapshot_five = "5" * 64
            snapshot_seven = "7" * 64
            frames = [
                {
                    "frame_id": "segment-a:50",
                    "server_tick": 5,
                    "replay_tick": 50,
                    "segment_id": "segment-a",
                    "segment_ordinal": 0,
                    "dimension": DIMENSION,
                    "subject_position": [0.5, 1.0, 0.5],
                    "complete": True,
                    "reasons": [],
                    "metadata": {
                        "entity_count": 1,
                        "event_sequence": 50,
                        "loaded_section_count": 1,
                        "metadata_policy": "full_packet_metadata",
                        "scene_snapshot_sha256": snapshot_five,
                        "scope": "client_visible",
                        "subject_entity_id": 2,
                    },
                },
                {
                    "frame_id": "segment-b:1",
                    "server_tick": 5,
                    "replay_tick": 1,
                    "segment_id": "segment-b",
                    "segment_ordinal": 1,
                    "dimension": DIMENSION,
                    "subject_position": [0.5, 1.0, 0.5],
                    "complete": True,
                    "reasons": [],
                    "metadata": {
                        "entity_count": 1,
                        "event_sequence": 50,
                        "loaded_section_count": 1,
                        "metadata_policy": "full_packet_metadata",
                        "scene_snapshot_sha256": snapshot_five,
                        "scope": "client_visible",
                        "subject_entity_id": 2,
                    },
                },
                {
                    "frame_id": "segment-a:52",
                    "server_tick": 7,
                    "replay_tick": 52,
                    "segment_id": "segment-a",
                    "segment_ordinal": 0,
                    "dimension": DIMENSION,
                    "subject_position": [0.5, 1.0, 0.5],
                    "complete": True,
                    "reasons": [],
                    "metadata": {
                        "entity_count": 0,
                        "event_sequence": 52,
                        "loaded_section_count": 1,
                        "metadata_policy": "full_packet_metadata",
                        "scene_snapshot_sha256": snapshot_seven,
                        "scope": "client_visible",
                        "subject_entity_id": 2,
                    },
                },
                # The next replay segment overlaps tick 7. Its replay-local
                # identity differs, but its complete logical snapshot agrees.
                {
                    "frame_id": "segment-b:3",
                    "server_tick": 7,
                    "replay_tick": 3,
                    "segment_id": "segment-b",
                    "segment_ordinal": 1,
                    "dimension": DIMENSION,
                    "subject_position": [0.5, 1.0, 0.5],
                    "complete": True,
                    "reasons": [],
                    "metadata": {
                        "entity_count": 0,
                        "event_sequence": 52,
                        "loaded_section_count": 1,
                        "metadata_policy": "full_packet_metadata",
                        "scene_snapshot_sha256": snapshot_seven,
                        "scope": "client_visible",
                        "subject_entity_id": 2,
                    },
                },
            ]
            changes = [
                {
                    "schema_version": 1,
                    "sequence": 0,
                    "server_tick": 5,
                    "replay_tick": -1,
                    "segment_id": "segment-a",
                    "segment_ordinal": 0,
                    "type": "segment_begin",
                    "sha256": "a" * 64,
                    "size_bytes": 100,
                },
                {
                    "schema_version": 1,
                    "sequence": 1,
                    "type": "section_set",
                    "server_tick": 5,
                    "replay_tick": 50,
                    "segment_id": "segment-a",
                    "segment_ordinal": 0,
                    "dimension": DIMENSION,
                    "x": 0,
                    "y": 0,
                    "z": 0,
                    "blob_sha256": section_hash,
                },
                {
                    "schema_version": 1,
                    "sequence": 2,
                    "type": "entity_set",
                    "server_tick": 5,
                    "replay_tick": 50,
                    "segment_id": "segment-a",
                    "segment_ordinal": 0,
                    "instance_id": "segment-a:20:1",
                    "blob_sha256": entity_hash,
                },
                {
                    "schema_version": 1,
                    "sequence": 3,
                    "type": "entity_remove",
                    "server_tick": 7,
                    "replay_tick": 52,
                    "segment_id": "segment-a",
                    "segment_ordinal": 0,
                    "instance_id": "segment-a:20:1",
                },
                {
                    "schema_version": 1,
                    "sequence": 4,
                    "server_tick": 5,
                    "replay_tick": -1,
                    "segment_id": "segment-b",
                    "segment_ordinal": 1,
                    "type": "segment_begin",
                    "sha256": "b" * 64,
                    "size_bytes": 200,
                },
                {
                    "schema_version": 1,
                    "sequence": 5,
                    "type": "section_set",
                    "server_tick": 5,
                    "replay_tick": 1,
                    "segment_id": "segment-b",
                    "segment_ordinal": 1,
                    "dimension": DIMENSION,
                    "x": 0,
                    "y": 0,
                    "z": 0,
                    "blob_sha256": section_hash,
                },
                {
                    "schema_version": 1,
                    "sequence": 6,
                    "type": "entity_set",
                    "server_tick": 5,
                    "replay_tick": 1,
                    "segment_id": "segment-b",
                    "segment_ordinal": 1,
                    "instance_id": "segment-b:20:1",
                    "blob_sha256": entity_hash,
                },
                {
                    "schema_version": 1,
                    "sequence": 7,
                    "type": "entity_remove",
                    "server_tick": 7,
                    "replay_tick": 3,
                    "segment_id": "segment-b",
                    "segment_ordinal": 1,
                    "instance_id": "segment-b:20:1",
                },
            ]
            (stream / "frames.jsonl").write_text(
                "".join(json.dumps(row) + "\n" for row in frames), encoding="utf-8"
            )
            (stream / "changes.jsonl").write_text(
                "".join(json.dumps(row) + "\n" for row in changes), encoding="utf-8"
            )
            (job / "scene-job.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "job_id": "job-test",
                        "session_id": IDENTITY.session_id,
                        "subject": {
                            "player_uuid": IDENTITY.player_uuid,
                            "connection_id": IDENTITY.connection_id,
                        },
                        "global_start_tick": 5,
                        "global_end_tick": 7,
                        "scope": "client_visible",
                        "metadata_policy": "full_packet_metadata",
                        "source_replays": [
                            {
                                "segment_id": "segment-a",
                                "segment_ordinal": 0,
                                "path": "sealed/a.zip",
                                "sha256": "a" * 64,
                                "size_bytes": 100,
                            },
                            {
                                "segment_id": "segment-b",
                                "segment_ordinal": 1,
                                "path": "sealed/b.zip",
                                "sha256": "b" * 64,
                                "size_bytes": 200,
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            output = root / "scene.sqlite3"

            info = compact_scene_stream(
                stream,
                output,
                expected_session_id=IDENTITY.session_id,
                expected_player_uuid=IDENTITY.player_uuid,
                expected_connection_id=IDENTITY.connection_id,
                expected_ticks=(5, 7),
            )

            self.assertEqual(info.ticks, (5, 7))
            self.assertEqual(info.source_replays[0]["sha256"], "a" * 64)
            self.assertEqual(info.source_replays[1]["sha256"], "b" * 64)
            with SceneStore(output) as store:
                at_five = store.materialize_crop(
                    "segment-a:50", (0, 0, 0), (2, 2, 2)
                )
                at_seven = store.materialize_crop(
                    "segment-a:52", (0, 0, 0), (2, 2, 2)
                )
                plane = store.slice(5, "y", 1, (1, 1, 1), 1)
            self.assertEqual(at_five.cell(0, 0, 0)[1]["name"], "minecraft:stone")
            self.assertEqual(
                [entity.instance_id for entity in at_five.entities],
                ["segment-a:20:1"],
            )
            self.assertEqual(at_seven.entities, ())
            self.assertEqual((plane.width, plane.height), (3, 3))

    def test_rejects_symlinked_stream_blob(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            stream = root / "stream"
            blobs = stream / "blobs"
            blobs.mkdir(parents=True)
            section = canonical_section_blob((STONE,), _indices())
            import hashlib

            digest = hashlib.sha256(section).hexdigest()
            target = root / "payload.zlib"
            target.write_bytes(zlib.compress(section))
            os.symlink(target, blobs / f"{digest}.zlib")
            (stream / "frames.jsonl").write_text(
                json.dumps(
                    {
                        "server_tick": 1,
                        "dimension": DIMENSION,
                        "subject_position": [0, 0, 0],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (stream / "changes.jsonl").write_text(
                json.dumps(
                    {
                        "type": "section_set",
                        "server_tick": 1,
                        "dimension": DIMENSION,
                        "x": 0,
                        "y": 0,
                        "z": 0,
                        "blob_sha256": digest,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(SceneStoreError, "symlinked"):
                compact_scene_stream(
                    stream,
                    root / "output.sqlite3",
                    expected_session_id=IDENTITY.session_id,
                    expected_player_uuid=IDENTITY.player_uuid,
                    expected_connection_id=IDENTITY.connection_id,
                    expected_ticks=(1,),
                )


if __name__ == "__main__":
    unittest.main()
