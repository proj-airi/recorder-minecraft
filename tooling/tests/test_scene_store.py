from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
import tempfile
import unittest
import zlib
from contextlib import closing
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

import mc_recorder.scene_store as scene_store_module
from mc_recorder.scene_integrity import (
    SceneStreamIntegrity,
    VerifiedSceneStream,
    freeze_json_value,
)
from mc_recorder.scene_store import (
    _BLOCK_ENTITY_BOX_SQL,
    _ENTITY_BOX_SQL,
    DEFAULT_MAX_CROP_CELLS,
    SceneIdentity,
    SceneSlice,
    SceneSliceCell,
    SceneStore,
    SceneStoreBuilder,
    SceneStoreError,
    SceneStoreValidationError,
    canonical_json_blob,
    canonical_section_blob,
    compact_scene_stream,
    scene_slice_to_json,
    validate_scene_attachment_provenance,
    validate_scene_store,
)

IDENTITY = SceneIdentity("session-a", "player-a", "connection-a")
DIMENSION = "minecraft:overworld"
AIR = {"name": "minecraft:air", "properties": {}}
STONE = {"name": "minecraft:stone", "properties": {}}
DIRT = {"name": "minecraft:dirt", "properties": {}}


def _sources(count: int = 1) -> tuple[dict[str, object], ...]:
    return tuple(
        {
            "segment_id": f"segment-{index}",
            "segment_ordinal": index,
            "path": f"/sealed/segment-{index}.zip",
            "sha256": f"{index + 1:02x}" * 32,
            "size_bytes": 100 + index,
            "format": "flashback",
        }
        for index in range(count)
    )


def _result(
    identity: SceneIdentity,
    ticks: tuple[int, ...],
    sources: tuple[dict[str, object], ...],
    stream: Path,
    integrity: SceneStreamIntegrity,
    *,
    job_id: str = "job-test",
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "result_type": "mc-recorder-scene-extraction-result-v1",
        "status": "complete",
        "job_id": job_id,
        "session_id": identity.session_id,
        "player_uuid": identity.player_uuid,
        "connection_id": identity.connection_id,
        "global_start_tick": ticks[0],
        "global_end_tick": ticks[-1],
        "scope": "client_visible",
        "metadata_policy": "full_packet_metadata",
        "flashback_capture_contract": "client_visible_scene_v1",
        "source_replays": list(sources),
        "subject_poses": {
            "format": "mc-recorder-subject-poses-v1",
            "path": "/verified/job/subject-poses.jsonl",
            "sha256": "4" * 64,
            "size_bytes": 100,
            "record_count": len(ticks),
            "first_tick": ticks[0],
            "last_tick": ticks[-1],
            "source_epochs": [
                {
                    "epoch_index": 0,
                    "events_sha256": "5" * 64,
                    "events_size_bytes": 1000,
                    "record_count": 20,
                }
            ],
        },
        "stream": {"path": str(stream.resolve()), **integrity.as_dict()},
        "ignored_packet_counts": {},
        "covered_tick_count": len(ticks),
    }


def _provenance(
    identity: SceneIdentity,
    ticks: tuple[int, ...],
    sources: tuple[dict[str, object], ...],
) -> dict[str, object]:
    integrity = SceneStreamIntegrity(
        format="mc-recorder-scene-stream-v1",
        frames_index="frames.jsonl",
        frames_size_bytes=1,
        frames_sha256="1" * 64,
        frame_count=len(ticks),
        changes_index="changes.jsonl",
        changes_size_bytes=1,
        changes_sha256="2" * 64,
        change_count=0,
        blobs_directory="blobs",
        blob_count=0,
        blob_bytes=0,
    )
    return {
        "scope": "client_visible",
        "metadata_policy": "full_packet_metadata",
        "result": _result(identity, ticks, sources, Path("/verified/stream"), integrity),
    }


def _verified_stream(
    stream: Path,
    ticks: tuple[int, ...],
    sources: tuple[dict[str, object], ...],
    *,
    job_id: str = "job-test",
) -> VerifiedSceneStream:
    frames = (stream / "frames.jsonl").read_bytes()
    changes = (stream / "changes.jsonl").read_bytes()
    blob_entries = list((stream / "blobs").iterdir())
    integrity = SceneStreamIntegrity(
        format="mc-recorder-scene-stream-v1",
        frames_index="frames.jsonl",
        frames_size_bytes=len(frames),
        frames_sha256=hashlib.sha256(frames).hexdigest(),
        frame_count=len(frames.splitlines()),
        changes_index="changes.jsonl",
        changes_size_bytes=len(changes),
        changes_sha256=hashlib.sha256(changes).hexdigest(),
        change_count=len(changes.splitlines()),
        blobs_directory="blobs",
        blob_count=len(blob_entries),
        blob_bytes=sum(entry.stat().st_size for entry in blob_entries),
    )
    result = _result(IDENTITY, ticks, sources, stream, integrity, job_id=job_id)
    result_bytes = json.dumps(result, sort_keys=True, separators=(",", ":")).encode()
    return VerifiedSceneStream(
        job_id=job_id,
        stream_path=stream,
        result=freeze_json_value(result),
        result_sha256=hashlib.sha256(result_bytes).hexdigest(),
        integrity=integrity,
    )


def _placeholder_verified_stream(stream: Path, ticks: tuple[int, ...]) -> VerifiedSceneStream:
    integrity = SceneStreamIntegrity(
        format="mc-recorder-scene-stream-v1",
        frames_index="frames.jsonl",
        frames_size_bytes=0,
        frames_sha256=hashlib.sha256(b"").hexdigest(),
        frame_count=0,
        changes_index="changes.jsonl",
        changes_size_bytes=0,
        changes_sha256=hashlib.sha256(b"").hexdigest(),
        change_count=0,
        blobs_directory="blobs",
        blob_count=0,
        blob_bytes=0,
    )
    sources = _sources()
    result = _result(IDENTITY, ticks, sources, stream, integrity)
    return VerifiedSceneStream(
        job_id="job-test",
        stream_path=stream,
        result=freeze_json_value(result),
        result_sha256="3" * 64,
        integrity=integrity,
    )


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
            sources = _sources()
            with SceneStoreBuilder(
                IDENTITY,
                start_tick=1,
                end_tick=2,
                source_replays=sources,
                provenance=_provenance(IDENTITY, (1, 2), sources),
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
            self.assertEqual(info.source_replays[0]["path"], "/sealed/segment-0.zip")
            self.assertEqual(
                validate_scene_attachment_provenance(info).scope,
                "client_visible",
            )
            with closing(sqlite3.connect(path)) as connection:
                section_blobs = connection.execute(
                    "SELECT COUNT(*) FROM blobs WHERE kind = 'section'"
                ).fetchone()[0]
                version_hashes = connection.execute(
                    "SELECT DISTINCT blob_sha256 FROM section_versions"
                ).fetchall()
            self.assertEqual(section_blobs, 1)
            self.assertEqual(len(version_hashes), 1)

    def test_rejects_full_packet_metadata_store_without_sensitive_marker(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "scene.sqlite3"
            sources = _sources()
            with SceneStoreBuilder(
                IDENTITY,
                start_tick=1,
                end_tick=1,
                source_replays=sources,
                provenance=_provenance(IDENTITY, (1,), sources),
                sensitive=False,
            ) as builder:
                _add_frames(builder, (1,))
                with self.assertRaisesRegex(
                    SceneStoreValidationError,
                    "full_packet_metadata scene stores must be marked sensitive",
                ):
                    builder.publish(path, expected_ticks=(1,))

            self.assertFalse(path.exists())

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
        with tempfile.TemporaryDirectory():
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
            self.assertEqual(
                as_json["palette"][as_json["cells"][4]["palette_index"]]["name"],
                "minecraft:stone",
            )
            self.assertNotIn("block_state", as_json["cells"][4])
            self.assertEqual(as_json["entities"][0]["payload"]["type_id"], "minecraft:pig")

    def test_slice_json_deduplicates_repeated_large_block_states(self) -> None:
        diameter = 129
        block_state = {
            "name": "minecraft:test_block",
            "properties": {"adversarial_padding": "x" * (256 * 1024)},
        }
        value = SceneSlice(
            tick=1,
            dimension=DIMENSION,
            axis="y",
            coordinate=64,
            row_axis="z",
            column_axis="x",
            row_origin=0,
            column_origin=0,
            width=diameter,
            height=diameter,
            cells=tuple(
                SceneSliceCell(
                    (index % diameter, 64, index // diameter),
                    True,
                    block_state,
                )
                for index in range(diameter * diameter)
            ),
            entities=(),
            block_entities=(),
            coverage_complete=True,
        )

        with mock.patch.object(
            scene_store_module,
            "_canonical_json_bytes",
            wraps=scene_store_module._canonical_json_bytes,
        ) as canonical_json:
            payload = scene_slice_to_json(value)

        self.assertEqual(1, canonical_json.call_count)
        self.assertEqual([block_state], payload["palette"])
        self.assertTrue(
            all(cell["palette_index"] == 0 for cell in payload["cells"])
        )
        self.assertTrue(
            all("block_state" not in cell for cell in payload["cells"])
        )
        encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.assertLess(len(encoded), 2 * 1024 * 1024)

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
                with self.assertRaisesRegex(SceneStoreError, "must be between"):
                    store.slice(1, "y", 10**100, (0, 64, 0), 1)
                with self.assertRaisesRegex(SceneStoreError, "must be between"):
                    store.slice(1, "y", 64, (10**100, 64, 0), 1)
                with self.assertRaisesRegex(SceneStoreError, "signed 64-bit"):
                    store.materialize_crop(
                        1,
                        (scene_store_module.SQLITE_INTEGER_MAX, 0, 0),
                        (1, 1, 1),
                    )
                with self.assertRaisesRegex(SceneStoreError, "not found"):
                    store.frame(2)

    def test_rejects_excessive_scene_object_counts_before_materialization(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "scene.sqlite3"
            with SceneStoreBuilder(
                IDENTITY, start_tick=1, end_tick=1
            ) as builder:
                _add_frames(builder, (1,))
                for index in range(2):
                    builder.set_entity(
                        1,
                        f"entity-{index}",
                        {
                            "dimension": DIMENSION,
                            "type_id": "minecraft:pig",
                            "network_id": index,
                            "position": [index + 0.5, 64.0, 0.5],
                            "aabb": [index, 64.0, 0.0, index + 1, 65.0, 1.0],
                        },
                    )
                    builder.set_block_entity(
                        1,
                        DIMENSION,
                        (index, 64, 0),
                        {"type_id": "minecraft:chest"},
                    )
                builder.publish(path, expected_ticks=(1,))

            with SceneStore(path) as store:
                with mock.patch.object(scene_store_module, "MAX_CROP_ENTITIES", 1):
                    with self.assertRaisesRegex(SceneStoreError, "entity count"):
                        store.slice(1, "y", 64, (1, 64, 0), 2)
                with mock.patch.object(
                    scene_store_module, "MAX_CROP_BLOCK_ENTITIES", 1
                ):
                    with self.assertRaisesRegex(SceneStoreError, "block-entity count"):
                        store.slice(1, "y", 64, (1, 64, 0), 2)

    def test_rejects_oversized_scene_object_payloads_before_materialization(self) -> None:
        oversized = "x" * (scene_store_module.MAX_CROP_OBJECT_PAYLOAD_BYTES + 1)
        for kind in ("entity", "block-entity"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temporary:
                path = Path(temporary) / "scene.sqlite3"
                with SceneStoreBuilder(
                    IDENTITY, start_tick=1, end_tick=1
                ) as builder:
                    _add_frames(builder, (1,))
                    if kind == "entity":
                        builder.set_entity(
                            1,
                            "oversized-entity",
                            {
                                "dimension": DIMENSION,
                                "type_id": "minecraft:pig",
                                "network_id": 1,
                                "position": [0.5, 64.0, 0.5],
                                "aabb": [0.0, 64.0, 0.0, 1.0, 65.0, 1.0],
                                "metadata": {"padding": oversized},
                            },
                        )
                    else:
                        builder.set_block_entity(
                            1,
                            DIMENSION,
                            (0, 64, 0),
                            {
                                "type_id": "minecraft:chest",
                                "metadata": {"padding": oversized},
                            },
                        )
                    builder.publish(path, expected_ticks=(1,))

                with SceneStore(path) as store:
                    with self.assertRaisesRegex(SceneStoreError, "per-object limit"):
                        store.slice(1, "y", 64, (0, 64, 0), 1)


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
            with closing(sqlite3.connect(path)) as connection:
                connection.execute(
                    "UPDATE blobs SET zlib_data = ? WHERE kind = 'section'", (b"broken",)
                )
                connection.execute(
                    "UPDATE blobs SET compressed_size = 6 WHERE kind = 'section'"
                )
                connection.commit()
            with self.assertRaisesRegex(SceneStoreValidationError, "zlib"):
                validate_scene_store(path)

    def test_rejects_persisted_interval_overlap(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = self._store(Path(temporary))
            with closing(sqlite3.connect(path)) as connection:
                digest = connection.execute(
                    "SELECT blob_sha256 FROM section_versions LIMIT 1"
                ).fetchone()[0]
                connection.execute(
                    "INSERT INTO section_versions VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (DIMENSION, 0, 0, 0, 2, 3, digest),
                )
                connection.commit()
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

    def test_attachment_rejects_store_without_extraction_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            info = validate_scene_store(self._store(Path(temporary)))
            self.assertIsNone(info.extraction)
            with self.assertRaisesRegex(
                SceneStoreValidationError, "authenticated extraction provenance"
            ):
                validate_scene_attachment_provenance(info)

    def test_rejects_persisted_result_frame_count_that_exceeds_store(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "scene.sqlite3"
            sources = _sources()
            with SceneStoreBuilder(
                IDENTITY,
                start_tick=1,
                end_tick=2,
                source_replays=sources,
                provenance=_provenance(IDENTITY, (1, 2), sources),
            ) as builder:
                _add_frames(builder, (1, 2))
                builder.publish(path, expected_ticks=(1, 2))
            with closing(sqlite3.connect(path)) as connection:
                provenance = json.loads(
                    connection.execute(
                        "SELECT provenance_json FROM scene_meta"
                    ).fetchone()[0]
                )
                provenance["result"]["stream"]["frame_count"] = 3
                connection.execute(
                    "UPDATE scene_meta SET provenance_json = ?",
                    (json.dumps(provenance, sort_keys=True, separators=(",", ":")),),
                )
                connection.commit()

            with self.assertRaisesRegex(
                SceneStoreValidationError, "frame_count does not match"
            ):
                validate_scene_store(path)

    def test_rejects_persisted_subject_pose_provenance_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "scene.sqlite3"
            sources = _sources()
            with SceneStoreBuilder(
                IDENTITY,
                start_tick=1,
                end_tick=2,
                source_replays=sources,
                provenance=_provenance(IDENTITY, (1, 2), sources),
            ) as builder:
                _add_frames(builder, (1, 2))
                builder.publish(path, expected_ticks=(1, 2))
            with closing(sqlite3.connect(path)) as connection:
                provenance = json.loads(
                    connection.execute(
                        "SELECT provenance_json FROM scene_meta"
                    ).fetchone()[0]
                )
                provenance["result"]["subject_poses"]["record_count"] = 1
                connection.execute(
                    "UPDATE scene_meta SET provenance_json = ?",
                    (json.dumps(provenance, sort_keys=True, separators=(",", ":")),),
                )
                connection.commit()

            with self.assertRaisesRegex(SceneStoreValidationError, "subject_poses coverage"):
                validate_scene_store(path)

    def test_rejects_persisted_capture_contract_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "scene.sqlite3"
            sources = _sources()
            with SceneStoreBuilder(
                IDENTITY,
                start_tick=1,
                end_tick=2,
                source_replays=sources,
                provenance=_provenance(IDENTITY, (1, 2), sources),
            ) as builder:
                _add_frames(builder, (1, 2))
                builder.publish(path, expected_ticks=(1, 2))
            with closing(sqlite3.connect(path)) as connection:
                provenance = json.loads(
                    connection.execute(
                        "SELECT provenance_json FROM scene_meta"
                    ).fetchone()[0]
                )
                provenance["result"]["flashback_capture_contract"] = "legacy"
                connection.execute(
                    "UPDATE scene_meta SET provenance_json = ?",
                    (json.dumps(provenance, sort_keys=True, separators=(",", ":")),),
                )
                connection.commit()

            with self.assertRaisesRegex(SceneStoreValidationError, "capture contract"):
                validate_scene_store(path)


class SceneStoreLongHistoryTest(unittest.TestCase):
    def _store(self, root: Path, interval_count: int = 6_000) -> Path:
        path = root / "long-scene.sqlite3"
        last_tick = interval_count - 1
        with SceneStoreBuilder(
            IDENTITY, start_tick=0, end_tick=last_tick
        ) as builder:
            _add_frames(builder, (0, last_tick))
            builder.set_entity(
                0,
                "history-0",
                {
                    "dimension": DIMENSION,
                    "type_id": "minecraft:pig",
                    "network_id": 77,
                    "position": [0.5, 64.0, 0.5],
                    "aabb": [0.1, 64.0, 0.1, 0.9, 64.9, 0.9],
                },
            )
            builder.set_block_entity(
                0,
                DIMENSION,
                (0, 64, 0),
                {"type_id": "minecraft:chest"},
            )
            builder.remove_entity(1, "history-0")
            builder.remove_block_entity(1, DIMENSION, (0, 64, 0))
            builder.publish(path, expected_ticks=(0, last_tick))

        with closing(sqlite3.connect(path)) as connection:
            entity_digest = connection.execute(
                "SELECT blob_sha256 FROM entity_versions LIMIT 1"
            ).fetchone()[0]
            block_entity_digest = connection.execute(
                "SELECT blob_sha256 FROM block_entity_versions LIMIT 1"
            ).fetchone()[0]
            connection.executemany(
                "INSERT INTO entity_versions VALUES "
                "(?, 77, ?, 'minecraft:pig', ?, ?, 0.1, 64.0, 0.1, "
                "0.9, 64.9, 0.9, ?)",
                (
                    (f"history-{tick}", DIMENSION, tick, tick + 1, entity_digest)
                    for tick in range(1, interval_count)
                ),
            )
            connection.commit()
            connection.executemany(
                "INSERT INTO block_entity_versions VALUES "
                "(?, 0, 64, 0, 'minecraft:chest', ?, ?, ?)",
                (
                    (DIMENSION, tick, tick + 1, block_entity_digest)
                    for tick in range(1, interval_count)
                ),
            )
            connection.commit()
        return path

    def test_validation_is_bounded_for_long_nonoverlapping_network_history(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = self._store(Path(temporary))
            original_connect = scene_store_module._connect_read_only
            callbacks = 0

            def bounded_connect(candidate: Path) -> sqlite3.Connection:
                connection = original_connect(candidate)

                def progress() -> int:
                    nonlocal callbacks
                    callbacks += 1
                    return int(callbacks > 20_000)

                connection.set_progress_handler(progress, 1_000)
                return connection

            with mock.patch.object(
                scene_store_module, "_connect_read_only", bounded_connect
            ):
                info = validate_scene_store(path)

            self.assertEqual(info.ticks, (0, 5_999))
            self.assertLess(callbacks, 20_000)

    def test_random_access_entity_queries_use_temporal_spatial_indexes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = self._store(Path(temporary))
            query_parameters = (
                5_999,
                5_999,
                0,
                1,
                64,
                65,
                0,
                1,
                DIMENSION,
                5_999,
                5_999,
                0,
                1,
                64,
                65,
                0,
                1,
            )
            with closing(sqlite3.connect(path)) as connection:
                entity_plan = connection.execute(
                    "EXPLAIN QUERY PLAN " + _ENTITY_BOX_SQL, query_parameters
                ).fetchall()
                block_entity_plan = connection.execute(
                    "EXPLAIN QUERY PLAN " + _BLOCK_ENTITY_BOX_SQL,
                    query_parameters,
                ).fetchall()

            for plan in (entity_plan, block_entity_plan):
                access = [
                    row[3]
                    for row in plan
                    if "search" in row[3] or "source" in row[3]
                ]
                self.assertGreaterEqual(len(access), 2, plan)
                self.assertIn("SCAN search VIRTUAL TABLE INDEX", access[0], plan)
                self.assertIn(
                    "SEARCH source USING INTEGER PRIMARY KEY", access[1], plan
                )
                self.assertFalse(
                    any("SCAN source" in detail for detail in access), plan
                )
            with SceneStore(path, validate=False) as store:
                callbacks = 0

                def progress() -> int:
                    nonlocal callbacks
                    callbacks += 1
                    return int(callbacks > 250)

                assert store._connection is not None
                store._connection.set_progress_handler(progress, 100)
                crop = store.materialize_crop(5_999, (0, 64, 0), (1, 1, 1))
                store._connection.set_progress_handler(None, 0)
            self.assertLess(callbacks, 250)
            self.assertEqual(
                [entity.instance_id for entity in crop.entities],
                ["history-5999"],
            )
            self.assertEqual(len(crop.block_entities), 1)


class SceneStreamCompactorTest(unittest.TestCase):
    def test_output_requires_force_and_force_preserves_a_valid_store_on_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            stream = root / "stream"
            stream.mkdir()
            verified = _placeholder_verified_stream(stream, (1,))
            output = root / "scene.sqlite3"
            output.write_bytes(b"not a scene store")
            with self.assertRaisesRegex(SceneStoreError, "pass --force"):
                compact_scene_stream(
                    stream,
                    output,
                    expected_session_id=IDENTITY.session_id,
                    expected_player_uuid=IDENTITY.player_uuid,
                    expected_connection_id=IDENTITY.connection_id,
                    expected_ticks=(1,),
                    verified_stream=verified,
                )
            with self.assertRaisesRegex(SceneStoreError, "validate scene store"):
                compact_scene_stream(
                    stream,
                    output,
                    expected_session_id=IDENTITY.session_id,
                    expected_player_uuid=IDENTITY.player_uuid,
                    expected_connection_id=IDENTITY.connection_id,
                    expected_ticks=(1,),
                    verified_stream=verified,
                    force=True,
                )

            output.unlink()
            with SceneStoreBuilder(IDENTITY, start_tick=1, end_tick=1) as builder:
                _add_frames(builder, (1,))
                builder.publish(output, expected_ticks=(1,))
            original = output.read_bytes()
            with self.assertRaises(SceneStoreError):
                compact_scene_stream(
                    stream,
                    output,
                    expected_session_id=IDENTITY.session_id,
                    expected_player_uuid=IDENTITY.player_uuid,
                    expected_connection_id=IDENTITY.connection_id,
                    expected_ticks=(1,),
                    verified_stream=verified,
                    force=True,
                )
            self.assertEqual(original, output.read_bytes())

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
                    "frame_id": "segment-a:52",
                    "server_tick": 6,
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
                    "server_tick": 6,
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
                    "server_tick": 6,
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
            sources = (
                {
                    "segment_id": "segment-a",
                    "segment_ordinal": 0,
                    "path": "/sealed/a.zip",
                    "sha256": "a" * 64,
                    "size_bytes": 100,
                    "format": "flashback",
                },
                {
                    "segment_id": "segment-b",
                    "segment_ordinal": 1,
                    "path": "/sealed/b.zip",
                    "sha256": "b" * 64,
                    "size_bytes": 200,
                    "format": "flashback",
                },
            )
            verified = _verified_stream(stream, (5, 6), sources)
            output = root / "scene.sqlite3"

            info = compact_scene_stream(
                stream,
                output,
                expected_session_id=IDENTITY.session_id,
                expected_player_uuid=IDENTITY.player_uuid,
                expected_connection_id=IDENTITY.connection_id,
                expected_ticks=(5, 6),
                verified_stream=verified,
            )

            self.assertEqual(info.ticks, (5, 6))
            self.assertEqual(info.source_replays[0]["sha256"], "a" * 64)
            self.assertEqual(info.source_replays[1]["sha256"], "b" * 64)
            self.assertEqual(info.extraction.result, verified.result)
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
            verified = _verified_stream(stream, (1,), _sources())
            with self.assertRaisesRegex(SceneStoreError, "non-symlink"):
                compact_scene_stream(
                    stream,
                    root / "output.sqlite3",
                    expected_session_id=IDENTITY.session_id,
                    expected_player_uuid=IDENTITY.player_uuid,
                    expected_connection_id=IDENTITY.connection_id,
                    expected_ticks=(1,),
                    verified_stream=verified,
                )


if __name__ == "__main__":
    unittest.main()
