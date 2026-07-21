from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from collections.abc import Mapping
from contextlib import closing
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from mc_recorder.dataset_viewer import (
    ArtifactUnavailableError,
    DatasetValidationError,
    DatasetViewer,
    DatasetViewerError,
    SampleNotFoundError,
    opaque_dataset_id,
)
from mc_recorder.render_contract import FULL_CLIENT_PRESENTATION_CONTRACT
from mc_recorder.scene_store import (
    SceneIdentity,
    SceneStoreBuilder,
    validate_scene_attachment_provenance,
    validate_scene_store,
)


_AUTO_SCENE_ATTACHMENT = object()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _plain_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _plain_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain_json(item) for item in value]
    return value


def _missing_modality(reason: str) -> dict[str, object]:
    return {
        "available": False,
        "valid": False,
        "reference": None,
        "reason": reason,
    }


def _structured_hud_result() -> dict[str, object]:
    return {
        "schema_version": 1,
        "type": "mc-recorder-structured-hud-v1",
        "format": "jsonl",
        "sha256": "a" * 64,
        "size_bytes": 123,
        "records": 1,
        "start_server_tick": 1,
        "end_server_tick": 1,
        "dataset_id": "b" * 32,
        "dataset_manifest_sha256": "c" * 64,
        "samples_sha256": "d" * 64,
        "session_id": "session-test",
        "player_uuid": "00000000-0000-4000-8000-000000000001",
        "connection_id": "00000000-0000-4000-8000-000000000002",
    }


def _full_client_attachment() -> dict[str, object]:
    hud = _structured_hud_result()
    return {
        "no_gui": False,
        "presentation_contract": FULL_CLIENT_PRESENTATION_CONTRACT,
        "structured_hud": hud,
        "session_id": hud["session_id"],
        "player_uuid": hud["player_uuid"],
        "connection_id": hud["connection_id"],
        "global_start_tick": 1,
        "global_end_tick": 1,
    }


def _sample(
    tick: int,
    *,
    player: str = "player-a",
    player_name: str = "Player A",
    connection: str = "connection-a",
    transition_valid: bool = True,
    rgb: dict[str, object] | None = None,
    scene: dict[str, object] | None = None,
) -> dict[str, object]:
    reasons = [] if transition_valid else ["synthetic_invalid_transition"]
    return {
        "schema_version": 2,
        "sample_key": {
            "session_id": "session-test",
            "server_tick": tick,
            "player_uuid": player,
            "connection_id": connection,
        },
        "session_id": "session-test",
        "epoch_index": 0,
        "server_tick": tick,
        "player_uuid": player,
        "connection_id": connection,
        "state": {
            "player_uuid": player,
            "player_name": player_name,
            "connection_id": connection,
            "dimension": "minecraft:overworld",
            "position": {"x": tick + 0.25, "y": 64.0, "z": -tick - 0.5},
        },
        "action": {
            "ordered_packets": [
                {
                    "action_type": "camera_or_position",
                    "apply_sequence": tick,
                }
            ],
            "reconstructed_control": None,
        },
        "next_state": {"player_uuid": player},
        "next_server_tick": tick + 1,
        "peers": {"state": [], "next_state": []},
        "modalities": {
            "rgb": rgb if rgb is not None else _missing_modality("no RGB"),
            "scene": scene
            if scene is not None
            else {
                "available": False,
                "valid": False,
                "coverage_complete": False,
                "reference": None,
                "frame_id": None,
                "reason": "scene_not_attached",
            },
        },
        "transition_valid": transition_valid,
        "transition_invalid_reasons": reasons,
        "source": {},
        "source_manifest_sha256": "0" * 64,
    }


def _write_dataset(
    exports: Path,
    samples: list[dict[str, object]],
    *,
    name: str = "session-test.dataset",
    owner: str = "mc-recorder",
    format_name: str = "mc-recorder-jsonl-v2",
    frame_attachments: list[dict[str, object]] | None = None,
    scene_store: bytes | None = None,
    scene_attachment: object = _AUTO_SCENE_ATTACHMENT,
) -> Path:
    directory = exports / name
    directory.mkdir(parents=True, exist_ok=True)
    streams = {
        "samples.jsonl": b"".join(
            json.dumps(row, sort_keys=True, separators=(",", ":")).encode() + b"\n"
            for row in samples
        ),
        "states.jsonl": b"",
        "actions.jsonl": b"",
        "modalities.jsonl": b"",
    }
    for file_name, content in streams.items():
        (directory / file_name).write_bytes(content)
    manifest = {
        "schema_version": 2,
        "owner": owner,
        "format": format_name,
        "created_at": "2026-07-21T00:00:00+00:00",
        "session_id": "session-test",
        "source_manifest_sha256": "0" * 64,
        "source": {
            "sealed_epochs": 1,
            "active_epochs_skipped": 0,
        },
        "selection": {
            "players": [],
            "from_tick": None,
            "to_tick": None,
            "frame_attachments": frame_attachments or [],
            "scene_attachment": None,
        },
        "timeline": {"tick_rate_hz": 20, "sample_rate_hz": 20},
        "modalities": {
            "samples": {
                "available": True,
                "records": len(samples),
                "file": "samples.jsonl",
            },
            "state": {
                "available": True,
                "records": len(samples),
                "file": "states.jsonl",
            },
            "actions": {
                "available": True,
                "records": len(samples),
                "file": "actions.jsonl",
            },
            "rgb": {
                "availability": "per-sample",
                "records_attached": sum(
                    row["modalities"]["rgb"].get("available") is True for row in samples
                ),
                "index": "modalities.jsonl",
            },
            "scene": {
                "availability": "per-sample",
                "records_attached": sum(
                    row["modalities"]["scene"].get("available") is True
                    for row in samples
                ),
                "index": "modalities.jsonl",
                "store": "scene/scene-v1.sqlite3" if scene_store is not None else None,
            },
        },
        "files": {
            file_name: {"size_bytes": len(content), "sha256": _sha256(content)}
            for file_name, content in streams.items()
        },
    }
    if scene_store is not None:
        scene_directory = directory / "scene"
        scene_directory.mkdir(exist_ok=True)
        scene_path = scene_directory / "scene-v1.sqlite3"
        scene_path.write_bytes(scene_store)
        manifest["files"]["scene/scene-v1.sqlite3"] = {
            "size_bytes": len(scene_store),
            "sha256": _sha256(scene_store),
        }
        if scene_attachment is _AUTO_SCENE_ATTACHMENT:
            info = validate_scene_store(scene_path)
            extraction = validate_scene_attachment_provenance(info)
            scene_attachment = {
                "format": "mc-recorder-scene-store-v1",
                "path": "/source/source-scene.sqlite3",
                "sha256": info.sha256,
                "size_bytes": info.size_bytes,
                "session_id": info.identity.session_id,
                "player_uuid": info.identity.player_uuid,
                "connection_id": info.identity.connection_id,
                "global_start_tick": info.start_tick,
                "global_end_tick": info.end_tick,
                "frame_count": info.frame_count,
                "scope": extraction.scope,
                "metadata_policy": extraction.metadata_policy,
                "result": _plain_json(extraction.result),
                "sensitive": info.sensitive,
                "source_replays": _plain_json(info.source_replays),
            }
    if scene_attachment is _AUTO_SCENE_ATTACHMENT:
        scene_attachment = None
    manifest["selection"]["scene_attachment"] = scene_attachment
    (directory / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return directory


def _scene_store_bytes(
    root: Path, *, tick: int = 7, authenticated: bool = True
) -> bytes:
    output = root / "source-scene.sqlite3"
    identity = SceneIdentity("session-test", "player-a", "connection-a")
    sources = (
        {
            "segment_id": "segment-viewer",
            "segment_ordinal": 0,
            "path": "/sealed/viewer-replay.zip",
            "sha256": "ab" * 32,
            "size_bytes": 123,
            "format": "flashback",
        },
    )
    result = {
        "schema_version": 1,
        "result_type": "mc-recorder-scene-extraction-result-v1",
        "status": "complete",
        "job_id": "job-viewer-test",
        "session_id": identity.session_id,
        "player_uuid": identity.player_uuid,
        "connection_id": identity.connection_id,
        "global_start_tick": tick,
        "global_end_tick": tick,
        "scope": "client_visible",
        "metadata_policy": "full_packet_metadata",
        "source_replays": list(sources),
        "subject_poses": {
            "format": "mc-recorder-subject-poses-v1",
            "path": "/verified/viewer-job/subject-poses.jsonl",
            "sha256": "12" * 32,
            "size_bytes": 100,
            "record_count": 1,
            "first_tick": tick,
            "last_tick": tick,
            "source_epochs": [{
                "epoch_index": 0,
                "events_sha256": "34" * 32,
                "events_size_bytes": 1000,
                "record_count": 20,
            }],
        },
        "stream": {
            "format": "mc-recorder-scene-stream-v1",
            "path": "/verified/viewer-test-stream",
            "frames_index": "frames.jsonl",
            "changes_index": "changes.jsonl",
            "blobs_directory": "blobs",
            "frame_count": 1,
            "change_count": 1,
            "blob_count": 1,
            "blob_bytes": 1,
            "frames_sha256": "cd" * 32,
            "frames_size_bytes": 1,
            "changes_sha256": "ef" * 32,
            "changes_size_bytes": 1,
        },
        "ignored_packet_counts": {},
        "covered_tick_count": 1,
    }
    builder = SceneStoreBuilder(
        identity,
        start_tick=tick,
        end_tick=tick,
        source_replays=sources if authenticated else (),
        provenance=(
            {
                "scope": "client_visible",
                "metadata_policy": "full_packet_metadata",
                "result": result,
            }
            if authenticated
            else {}
        ),
        sensitive=True,
    )
    try:
        builder.set_section(
            tick,
            "minecraft:overworld",
            (0, 4, 0),
            ({"name": "minecraft:stone"},),
            (0,) * 4096,
        )
        builder.set_entity(
            tick,
            "pig-1",
            {
                "dimension": "minecraft:overworld",
                "type_id": "minecraft:pig",
                "network_id": 12,
                "uuid": "00000000-0000-4000-8000-000000000012",
                "position": [7.5, 64.0, -0.5],
                "velocity": [0.0, 0.0, 0.0],
                "rotation": [0.0, 0.0],
                "aabb": [7.1, 63.5, -0.9, 7.9, 64.5, -0.1],
                "custom_name": "Viewer Pig",
            },
        )
        builder.set_block_entity(
            tick,
            "minecraft:overworld",
            (8, 64, 0),
            {
                "type_id": "minecraft:chest",
                "custom_name": "Viewer Chest",
            },
        )
        builder.add_frame(
            tick,
            frame_id=f"scene-frame-{tick}",
            replay_tick=tick,
            dimension="minecraft:overworld",
            subject_position=(tick + 0.25, 64.0, -tick - 0.5),
            coverage_complete=True,
        )
        builder.publish(output, expected_ticks=(tick,))
    finally:
        builder.close()
    return output.read_bytes()


class DatasetCatalogTest(unittest.TestCase):
    def test_catalogs_only_exact_contained_valid_dataset_directories(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            exports = root / "exports"
            exports.mkdir()
            _write_dataset(exports, [_sample(1)])

            (exports / "ordinary-directory").mkdir()
            nested = exports / "nested"
            nested.mkdir()
            _write_dataset(nested, [_sample(1)], name="not-direct.dataset")

            bad_owner = _write_dataset(
                exports, [_sample(1)], name="bad-owner.dataset", owner="someone-else"
            )
            _write_dataset(
                exports,
                [_sample(1)],
                name="bad-format.dataset",
                format_name="not-mc-recorder",
            )
            bad_schema = _write_dataset(
                exports, [_sample(1)], name="bad-schema.dataset"
            )
            bad_schema_manifest = json.loads(
                (bad_schema / "manifest.json").read_text(encoding="utf-8")
            )
            bad_schema_manifest["schema_version"] = 1
            (bad_schema / "manifest.json").write_text(
                json.dumps(bad_schema_manifest), encoding="utf-8"
            )
            bad_files = _write_dataset(exports, [_sample(1)], name="bad-files.dataset")
            bad_files_manifest = json.loads(
                (bad_files / "manifest.json").read_text(encoding="utf-8")
            )
            bad_files_manifest["files"]["other.jsonl"] = {
                "size_bytes": 0,
                "sha256": _sha256(b""),
            }
            (bad_files / "manifest.json").write_text(
                json.dumps(bad_files_manifest), encoding="utf-8"
            )
            extra = _write_dataset(exports, [_sample(1)], name="extra.dataset")
            (extra / "unexpected.txt").write_text("not part of v2", encoding="utf-8")
            bad_hash = _write_dataset(exports, [_sample(1)], name="bad-hash.dataset")
            (bad_hash / "samples.jsonl").write_bytes(b"changed after manifest\n")
            try:
                os.symlink(
                    bad_owner, exports / "linked.dataset", target_is_directory=True
                )
            except OSError:
                pass

            viewer = DatasetViewer(exports, root / "runtime")
            catalog = viewer.catalog()

            self.assertEqual(1, len(catalog.datasets))
            summary = catalog.datasets[0]
            self.assertEqual("session-test", summary.session_id)
            self.assertRegex(summary.dataset_id, r"^[0-9a-f]{32}$")
            self.assertNotIn("session-test.dataset", summary.dataset_id)
            rejected = {issue.name: issue.message for issue in catalog.rejected}
            self.assertIn("bad-owner.dataset", rejected)
            self.assertIn("bad-format.dataset", rejected)
            self.assertIn("bad-schema.dataset", rejected)
            self.assertIn("bad-files.dataset", rejected)
            self.assertIn("extra.dataset", rejected)
            self.assertIn("bad-hash.dataset", rejected)
            self.assertNotIn("not-direct.dataset", rejected)

    def test_bounds_manifest_read_before_parsing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            exports = root / "exports"
            exports.mkdir()
            dataset = _write_dataset(exports, [_sample(1)])
            manifest = (dataset / "manifest.json").resolve()
            viewer = DatasetViewer(
                exports,
                root / "runtime",
                max_manifest_bytes=32,
            )
            bounded_handle = mock.MagicMock()
            bounded_handle.__enter__.return_value.read.return_value = b"x" * 33
            original_open = Path.open

            def open_path(path: Path, *args, **kwargs):
                if path == manifest:
                    return bounded_handle
                return original_open(path, *args, **kwargs)

            with mock.patch.object(
                type(manifest),
                "open",
                autospec=True,
                side_effect=open_path,
            ):
                catalog = viewer.catalog()

            self.assertEqual((), catalog.datasets)
            self.assertIn("exceeds 32 bytes", catalog.rejected[0].message)
            bounded_handle.__enter__.return_value.read.assert_called_once_with(33)

    def test_rejects_symlinked_required_stream(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            exports = root / "exports"
            exports.mkdir()
            dataset = _write_dataset(exports, [_sample(1)])
            samples = dataset / "samples.jsonl"
            target = root / "real-samples"
            samples.rename(target)
            try:
                samples.symlink_to(target)
            except OSError:
                self.skipTest("symlinks are unavailable")

            catalog = DatasetViewer(exports, root / "runtime").catalog()

            self.assertEqual((), catalog.datasets)
            self.assertIn("symlink", catalog.rejected[0].message)


class DatasetIndexTest(unittest.TestCase):
    def test_indexes_summaries_filters_pages_and_lazy_detail(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            exports = root / "exports"
            exports.mkdir()
            samples = [
                _sample(10),
                _sample(
                    10,
                    player="player-b",
                    player_name="Player B",
                    connection="connection-b",
                ),
                _sample(11, transition_valid=False),
                _sample(12),
            ]
            _write_dataset(exports, samples)
            viewer = DatasetViewer(exports, root / "runtime")

            summary = viewer.list_datasets()[0]
            self.assertEqual(4, summary.sample_count)
            self.assertEqual(2, summary.player_count)
            self.assertEqual(2, summary.connection_count)
            self.assertEqual((10, 12), (summary.first_tick, summary.last_tick))
            self.assertTrue(viewer.index_path.is_file())
            self.assertTrue(
                viewer.index_path.is_relative_to((root / "runtime").resolve())
            )

            metadata = viewer.get_dataset_metadata(summary.dataset_id)
            self.assertEqual(4, metadata.sample_count)
            self.assertEqual(1, metadata.source_sealed_epochs)
            self.assertIsNone(metadata.rgb_presentation)
            self.assertEqual(
                {"actions.jsonl", "modalities.jsonl", "samples.jsonl", "states.jsonl"},
                set(metadata.files),
            )

            connections = viewer.list_player_connections(summary.dataset_id)
            self.assertEqual(2, len(connections))
            primary = next(
                item for item in connections if item.player_uuid == "player-a"
            )
            self.assertEqual(3, primary.sample_count)
            self.assertEqual(2, primary.valid_transitions)
            self.assertEqual(0, primary.scene_samples)

            first_page = viewer.list_sample_summaries(summary.dataset_id, limit=2)
            self.assertEqual(4, first_page.total)
            self.assertEqual(2, len(first_page.items))
            self.assertIsNotNone(first_page.next_cursor)
            second_page = viewer.list_sample_summaries(
                summary.dataset_id, limit=2, cursor=first_page.next_cursor
            )
            self.assertEqual(2, len(second_page.items))
            self.assertIsNone(second_page.next_cursor)
            without_scene = viewer.list_sample_summaries(
                summary.dataset_id, scene_available=False
            )
            self.assertEqual(4, without_scene.total)

            invalid = viewer.list_sample_summaries(
                summary.dataset_id,
                player_uuid="player-a",
                from_tick=11,
                to_tick=11,
                transition_valid=False,
            )
            self.assertEqual(1, invalid.total)
            self.assertEqual(
                ("synthetic_invalid_transition",),
                invalid.items[0].transition_invalid_reasons,
            )
            self.assertEqual((11.25, 64.0, -11.5), invalid.items[0].position)

            trajectory = viewer.get_trajectory(summary.dataset_id, max_points=3)
            self.assertEqual(4, trajectory.total_points)
            self.assertEqual(3, trajectory.returned_points)
            self.assertTrue(trajectory.downsampled)
            self.assertEqual(0, trajectory.omitted_tracks)
            self.assertEqual(
                (10.25, 12.25),
                (trajectory.bounds.min_x, trajectory.bounds.max_x),
            )
            primary_track = next(
                track for track in trajectory.tracks if track.player_uuid == "player-a"
            )
            self.assertEqual(3, primary_track.point_count)
            self.assertEqual(
                [10, 12], [point.server_tick for point in primary_track.points]
            )
            self.assertEqual(
                [False, False],
                [point.continuous_from_previous for point in primary_track.points],
            )
            self.assertAlmostEqual(
                2**0.5, primary_track.horizontal_distance_blocks
            )

            bounded_trajectory = viewer.get_trajectory(
                summary.dataset_id, max_points=1
            )
            self.assertEqual(1, bounded_trajectory.returned_points)
            self.assertEqual(1, bounded_trajectory.omitted_tracks)
            self.assertEqual(1, len(bounded_trajectory.tracks))

            invalid_trajectory = viewer.get_trajectory(
                summary.dataset_id,
                player_uuid="player-a",
                from_tick=11,
                to_tick=11,
                transition_valid=False,
            )
            self.assertEqual(1, invalid_trajectory.total_points)
            self.assertEqual(
                11, invalid_trajectory.tracks[0].points[0].server_tick
            )

            detail = viewer.get_sample_detail(
                summary.dataset_id, invalid.items[0].sample_id
            )
            self.assertEqual(11, detail.record["server_tick"])
            self.assertNotIn("reference", detail.record["modalities"]["rgb"])
            self.assertIsNone(detail.record["modalities"]["rgb"]["artifact_id"])
            self.assertNotIn("reference", detail.record["modalities"]["scene"])
            self.assertIsNone(detail.record["modalities"]["scene"]["artifact_id"])
            with self.assertRaises(ArtifactUnavailableError):
                viewer.resolve_rgb_artifact(
                    summary.dataset_id, invalid.items[0].sample_id
                )
            with self.assertRaises(ArtifactUnavailableError):
                viewer.get_scene_slice(
                    summary.dataset_id, invalid.items[0].sample_id, axis="y"
                )
            with self.assertRaisesRegex(DatasetViewerError, "axis"):
                viewer.get_scene_slice(
                    summary.dataset_id,
                    invalid.items[0].sample_id,
                    axis="north",
                )
            with self.assertRaisesRegex(DatasetViewerError, "radius"):
                viewer.get_scene_slice(
                    summary.dataset_id,
                    invalid.items[0].sample_id,
                    axis="y",
                    radius=65,
                )

    def test_reports_verified_rgb_presentation_provenance(self) -> None:
        rgb = {"available": True, "valid": True}
        cases = (
            (
                "full.dataset",
                [_full_client_attachment()],
                "full_client",
            ),
            (
                "legacy-gui.dataset",
                [{"no_gui": False}],
                "legacy_gui_unsynchronized",
            ),
            (
                "unbound-current-gui.dataset",
                [
                    {
                        "no_gui": False,
                        "presentation_contract": FULL_CLIENT_PRESENTATION_CONTRACT,
                    }
                ],
                "legacy_gui_unsynchronized",
            ),
            (
                "wrong-gui.dataset",
                [{"no_gui": False, "presentation_contract": "wrong"}],
                "legacy_gui_unsynchronized",
            ),
            ("hud-free.dataset", [{"no_gui": True}], "hud_free"),
            ("legacy.dataset", [{"result_sha256": "0" * 64}], "hud_free"),
            (
                "mixed.dataset",
                [
                    _full_client_attachment(),
                    {"no_gui": True},
                ],
                "mixed",
            ),
            (
                "mixed-legacy-gui.dataset",
                [
                    {"no_gui": False},
                    {"no_gui": True},
                ],
                "mixed_legacy_gui_unsynchronized",
            ),
        )
        for name, attachments, expected in cases:
            with self.subTest(expected=expected):
                with tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    exports = root / "exports"
                    exports.mkdir()
                    for attachment in attachments:
                        if not isinstance(attachment, dict):
                            continue
                        structured_hud = attachment.get("structured_hud")
                        if isinstance(structured_hud, dict):
                            structured_hud["dataset_id"] = opaque_dataset_id(
                                exports, name
                            )
                    _write_dataset(
                        exports,
                        [_sample(1, rgb=rgb)],
                        name=name,
                        frame_attachments=attachments,
                    )
                    viewer = DatasetViewer(exports, root / "runtime")
                    dataset_id = viewer.list_datasets()[0].dataset_id
                    self.assertEqual(
                        expected,
                        viewer.get_dataset_metadata(dataset_id).rgb_presentation,
                    )

    def test_rebuilds_offsets_when_valid_export_fingerprint_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            exports = root / "exports"
            exports.mkdir()
            _write_dataset(exports, [_sample(1), _sample(2)])
            viewer = DatasetViewer(exports, root / "runtime")
            dataset_id = viewer.list_datasets()[0].dataset_id
            first = viewer.list_sample_summaries(dataset_id)
            old_sample_id = first.items[0].sample_id

            _write_dataset(exports, [_sample(1), _sample(2), _sample(3)])
            refreshed = viewer.list_sample_summaries(dataset_id)

            self.assertEqual(3, refreshed.total)
            self.assertNotEqual(old_sample_id, refreshed.items[0].sample_id)
            with self.assertRaises(SampleNotFoundError):
                viewer.get_sample_detail(dataset_id, old_sample_id)
            with closing(sqlite3.connect(viewer.index_path)) as database:
                indexed_count = database.execute(
                    "SELECT COUNT(*) FROM samples WHERE dataset_id = ?", (dataset_id,)
                ).fetchone()[0]
            self.assertEqual(3, indexed_count)

    def test_rejects_same_size_tampering_when_mtime_is_restored(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            exports = root / "exports"
            exports.mkdir()
            dataset = _write_dataset(exports, [_sample(1)])
            viewer = DatasetViewer(exports, root / "runtime")
            dataset_id = viewer.list_datasets()[0].dataset_id
            sample_id = viewer.list_sample_summaries(dataset_id).items[0].sample_id
            samples = dataset / "samples.jsonl"
            original_stat = samples.stat()
            original = samples.read_bytes()
            tampered = original.replace(b"Player A", b"Hacker A")
            self.assertEqual(len(original), len(tampered))
            samples.write_bytes(tampered)
            os.utime(
                samples,
                ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns),
            )

            with self.assertRaisesRegex(DatasetValidationError, "SHA-256"):
                viewer.get_sample_detail(dataset_id, sample_id)

    def test_bounds_tick_filters_to_sqlite_integer_range(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            exports = root / "exports"
            exports.mkdir()
            _write_dataset(exports, [_sample(1)])
            viewer = DatasetViewer(exports, root / "runtime")
            dataset_id = viewer.list_datasets()[0].dataset_id

            with self.assertRaisesRegex(DatasetViewerError, "signed 64-bit"):
                viewer.list_sample_summaries(dataset_id, from_tick=2**63)
            with self.assertRaisesRegex(DatasetViewerError, "max_points"):
                viewer.get_trajectory(dataset_id, max_points=10_001)

    def test_rejects_out_of_range_ticks_and_non_finite_positions_per_dataset(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            exports = root / "exports"
            exports.mkdir()
            _write_dataset(exports, [_sample(2**63)], name="bad-tick.dataset")
            non_finite = _sample(1)
            non_finite["state"]["position"]["x"] = float("nan")
            _write_dataset(exports, [non_finite], name="bad-float.dataset")
            huge_integer = _sample(2)
            huge_integer["state"]["position"]["x"] = 10**400
            _write_dataset(exports, [huge_integer], name="bad-coordinate.dataset")

            catalog = DatasetViewer(exports, root / "runtime").catalog()

            self.assertEqual((), catalog.datasets)
            rejected = {issue.name: issue.message for issue in catalog.rejected}
            self.assertIn("signed 64-bit", rejected["bad-tick.dataset"])
            self.assertIn("invalid JSON", rejected["bad-float.dataset"])
            self.assertIn("finite numeric bounds", rejected["bad-coordinate.dataset"])


class DatasetArtifactTest(unittest.TestCase):
    def test_reads_random_access_scene_slice_with_entities(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            exports = root / "exports"
            exports.mkdir()
            scene = {
                "available": True,
                "valid": True,
                "coverage_complete": True,
                "reference": "scene/scene-v1.sqlite3",
                "frame_id": "scene-frame-7",
                "reason": None,
            }
            _write_dataset(
                exports,
                [_sample(7, scene=scene)],
                scene_store=_scene_store_bytes(root),
            )
            viewer = DatasetViewer(exports, root / "runtime")
            summary = viewer.list_datasets()[0]
            page = viewer.list_sample_summaries(
                summary.dataset_id, scene_available=True
            )
            sample_id = page.items[0].sample_id

            detail = viewer.get_sample_detail(summary.dataset_id, sample_id)
            plane = viewer.get_scene_slice(
                summary.dataset_id,
                sample_id,
                axis="y",
                radius=8,
            )

            self.assertEqual(1, summary.scene_samples)
            self.assertEqual(1, page.total)
            self.assertTrue(page.items[0].scene_available)
            self.assertNotIn("reference", detail.record["modalities"]["scene"])
            self.assertEqual(
                sample_id, detail.record["modalities"]["scene"]["artifact_id"]
            )
            self.assertEqual((64, 17, 17), (plane.coordinate, plane.width, plane.height))
            self.assertGreater(sum(cell.covered for cell in plane.cells), 0)
            covered = next(cell for cell in plane.cells if cell.covered)
            self.assertEqual("minecraft:stone", covered.block_state["name"])
            self.assertEqual("minecraft:pig", plane.entities[0].type_id)
            self.assertEqual("minecraft:chest", plane.block_entities[0].type_id)
            self.assertFalse(plane.coverage_complete)

    def test_rejects_scene_store_without_authenticated_extraction(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            exports = root / "exports"
            exports.mkdir()
            scene = {
                "available": True,
                "valid": True,
                "coverage_complete": True,
                "reference": "scene/scene-v1.sqlite3",
                "frame_id": "scene-frame-7",
                "reason": None,
            }
            _write_dataset(
                exports,
                [_sample(7, scene=scene)],
                scene_store=_scene_store_bytes(root, authenticated=False),
                scene_attachment={},
            )

            catalog = DatasetViewer(exports, root / "runtime").catalog()

            self.assertEqual((), catalog.datasets)
            self.assertIn(
                "authenticated extraction provenance", catalog.rejected[0].message
            )

    def test_scene_attachment_presence_exactly_tracks_the_contained_store(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            exports = root / "exports"
            exports.mkdir()
            scene = {
                "available": True,
                "valid": True,
                "coverage_complete": True,
                "reference": "scene/scene-v1.sqlite3",
                "frame_id": "scene-frame-7",
                "reason": None,
            }
            _write_dataset(
                exports,
                [_sample(7, scene=scene)],
                name="null-attachment.dataset",
                scene_store=_scene_store_bytes(root),
                scene_attachment=None,
            )
            _write_dataset(
                exports,
                [_sample(8)],
                name="orphan-attachment.dataset",
                scene_attachment={},
            )

            catalog = DatasetViewer(exports, root / "runtime").catalog()

            self.assertEqual((), catalog.datasets)
            rejected = {issue.name: issue.message for issue in catalog.rejected}
            self.assertIn("scene_attachment is required", rejected["null-attachment.dataset"])
            self.assertIn(
                "requires a contained scene store",
                rejected["orphan-attachment.dataset"],
            )

    def test_scene_attachment_exactly_binds_authenticated_store_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            exports = root / "exports"
            exports.mkdir()
            scene = {
                "available": True,
                "valid": True,
                "coverage_complete": True,
                "reference": "scene/scene-v1.sqlite3",
                "frame_id": "scene-frame-7",
                "reason": None,
            }
            mutations = {
                "format": "other-scene-format",
                "path": "",
                "sha256": "0" * 64,
                "size_bytes": 0,
                "session_id": "other-session",
                "player_uuid": "other-player",
                "connection_id": "other-connection",
                "global_start_tick": 6,
                "global_end_tick": 8,
                "frame_count": 2,
                "scope": "privileged",
                "metadata_policy": "redacted",
                "result": None,
                "sensitive": False,
                "source_replays": [],
            }
            scene_bytes = _scene_store_bytes(root)
            for field, replacement in mutations.items():
                name = f"bad-{field.replace('_', '-')}.dataset"
                directory = _write_dataset(
                    exports,
                    [_sample(7, scene=scene)],
                    name=name,
                    scene_store=scene_bytes,
                )
                manifest_path = directory / "manifest.json"
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                manifest["selection"]["scene_attachment"][field] = replacement
                manifest_path.write_text(
                    json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )

            catalog = DatasetViewer(exports, root / "runtime").catalog()

            self.assertEqual((), catalog.datasets)
            self.assertEqual(set(mutations), {
                issue.name.removeprefix("bad-").removesuffix(".dataset").replace("-", "_")
                for issue in catalog.rejected
            })

    def test_scene_attachment_path_is_opaque_provenance_text(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            exports = root / "exports"
            exports.mkdir()
            scene = {
                "available": True,
                "valid": True,
                "coverage_complete": True,
                "reference": "scene/scene-v1.sqlite3",
                "frame_id": "scene-frame-7",
                "reason": None,
            }
            directory = _write_dataset(
                exports,
                [_sample(7, scene=scene)],
                scene_store=_scene_store_bytes(root),
            )
            manifest_path = directory / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["selection"]["scene_attachment"]["path"] = (
                "historical source path, not a viewer input"
            )
            manifest_path.write_text(
                json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )

            catalog = DatasetViewer(exports, root / "runtime").catalog()

            self.assertEqual(1, len(catalog.datasets))
            self.assertEqual((), catalog.rejected)

    def test_resolves_only_contained_hash_verified_rgb(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            exports = root / "exports"
            exports.mkdir()
            frame = exports / "render-jobs" / "job" / "frame.png"
            frame.parent.mkdir(parents=True)
            png = b"\x89PNG\r\n\x1a\nsynthetic-frame"
            frame.write_bytes(png)
            rgb = {
                "available": True,
                "valid": True,
                "reference": str(frame),
                "artifact_bytes": len(png),
                "artifact_sha256": _sha256(png),
                "width": 1,
                "height": 1,
            }
            _write_dataset(exports, [_sample(1, rgb=rgb)])
            viewer = DatasetViewer(exports, root / "runtime")
            dataset_id = viewer.list_datasets()[0].dataset_id
            sample_id = viewer.list_sample_summaries(dataset_id).items[0].sample_id

            artifact = viewer.resolve_rgb_artifact(dataset_id, sample_id)

            self.assertEqual(frame.resolve(), artifact.path)
            self.assertEqual("image/png", artifact.media_type)
            detail = viewer.get_sample_detail(dataset_id, sample_id)
            self.assertNotIn("reference", detail.record["modalities"]["rgb"])
            self.assertEqual(
                sample_id, detail.record["modalities"]["rgb"]["artifact_id"]
            )

            frame.write_bytes(png + b"tampered")
            with self.assertRaises(DatasetValidationError):
                viewer.resolve_rgb_artifact(dataset_id, sample_id)

    def test_rejects_external_and_symlinked_rgb_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            exports = root / "exports"
            exports.mkdir()
            outside = root / "outside.png"
            png = b"\x89PNG\r\n\x1a\nexternal"
            outside.write_bytes(png)
            rgb = {
                "available": True,
                "valid": True,
                "reference": str(outside),
                "artifact_bytes": len(png),
                "artifact_sha256": _sha256(png),
            }
            _write_dataset(exports, [_sample(1, rgb=rgb)])
            viewer = DatasetViewer(exports, root / "runtime")
            dataset_id = viewer.list_datasets()[0].dataset_id
            sample_id = viewer.list_sample_summaries(dataset_id).items[0].sample_id
            with self.assertRaisesRegex(DatasetValidationError, "escapes"):
                viewer.resolve_rgb_artifact(dataset_id, sample_id)

            real = exports / "real.png"
            linked = exports / "linked.png"
            real.write_bytes(png)
            try:
                linked.symlink_to(real.name)
            except OSError:
                self.skipTest("symlinks are unavailable")
            rgb["reference"] = str(linked)
            _write_dataset(exports, [_sample(1, rgb=rgb)])
            dataset_id = viewer.list_datasets()[0].dataset_id
            sample_id = viewer.list_sample_summaries(dataset_id).items[0].sample_id
            with self.assertRaisesRegex(DatasetValidationError, "symlink"):
                viewer.resolve_rgb_artifact(dataset_id, sample_id)

    def test_rejects_scene_reference_without_manifest_owned_store(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            exports = root / "exports"
            exports.mkdir()
            scene = {
                "available": True,
                "valid": True,
                "coverage_complete": True,
                "reference": "scene/scene-v1.sqlite3",
                "frame_id": "scene-frame-1",
                "reason": None,
            }
            _write_dataset(exports, [_sample(1, scene=scene)])

            catalog = DatasetViewer(exports, root / "runtime").catalog()

            self.assertEqual((), catalog.datasets)
            self.assertIn("absent scene store", catalog.rejected[0].message)

    def test_rejects_scene_frame_id_for_a_different_tick(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            exports = root / "exports"
            exports.mkdir()
            scene = {
                "available": True,
                "valid": True,
                "coverage_complete": True,
                "reference": "scene/scene-v1.sqlite3",
                "frame_id": "scene-frame-7",
                "reason": None,
            }
            _write_dataset(
                exports,
                [_sample(8, scene=scene)],
                scene_store=_scene_store_bytes(root, tick=7),
            )

            catalog = DatasetViewer(exports, root / "runtime").catalog()

            self.assertEqual((), catalog.datasets)
            self.assertIn("resolves to tick 7, not sample tick 8", catalog.rejected[0].message)


if __name__ == "__main__":
    unittest.main()
