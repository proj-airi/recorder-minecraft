from __future__ import annotations

import base64
import gzip
import hashlib
import json
import os
import sqlite3
import sys
import tempfile
import unittest
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


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


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
    voxels: dict[str, object] | None = None,
) -> dict[str, object]:
    reasons = [] if transition_valid else ["synthetic_invalid_transition"]
    return {
        "schema_version": 1,
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
            "voxels": voxels if voxels is not None else _missing_modality("no voxels"),
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
    format_name: str = "mc-recorder-jsonl-v1",
    frame_attachments: list[dict[str, object]] | None = None,
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
        "schema_version": 1,
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
            "voxel_attachments": [],
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
            "voxels": {
                "availability": "per-sample",
                "records_attached": sum(
                    row["modalities"]["voxels"].get("available") is True
                    for row in samples
                ),
                "index": "modalities.jsonl",
            },
        },
        "files": {
            file_name: {"size_bytes": len(content), "sha256": _sha256(content)}
            for file_name, content in streams.items()
        },
    }
    (directory / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return directory


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
            bad_schema_manifest["schema_version"] = 2
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
            (extra / "unexpected.txt").write_text("not part of v1", encoding="utf-8")
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

            first_page = viewer.list_sample_summaries(summary.dataset_id, limit=2)
            self.assertEqual(4, first_page.total)
            self.assertEqual(2, len(first_page.items))
            self.assertIsNotNone(first_page.next_cursor)
            second_page = viewer.list_sample_summaries(
                summary.dataset_id, limit=2, cursor=first_page.next_cursor
            )
            self.assertEqual(2, len(second_page.items))
            self.assertIsNone(second_page.next_cursor)

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
            with self.assertRaises(ArtifactUnavailableError):
                viewer.resolve_rgb_artifact(
                    summary.dataset_id, invalid.items[0].sample_id
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

    def test_decodes_bounded_voxel_slice_and_preserves_unknown_cells(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            exports = root / "exports"
            exports.mkdir()
            voxel_path = exports / "render-jobs" / "job" / "voxel.json.gz"
            voxel_path.parent.mkdir(parents=True)
            indices = b"".join(value.to_bytes(2, "little") for value in (0, 1, 1, 0))
            snapshot = {
                "schema_version": 1,
                "format": "mc-recorder-voxel-palette-v1",
                "session_id": "session-test",
                "player_uuid": "player-a",
                "connection_id": "connection-a",
                "server_tick": 7,
                "replay_tick": 7,
                "dimension": "minecraft:overworld",
                "center": {"x": 10, "y": 20, "z": 30},
                "origin": {"x": 10, "y": 20, "z": 30},
                "shape": {"x": 2, "y": 1, "z": 2},
                "linear_order": "x_fastest_then_z_then_y",
                "index_dtype": "uint16",
                "index_byte_order": "little_endian",
                "indices_base64": base64.b64encode(indices).decode(),
                "coverage_bitset_base64": base64.b64encode(b"\x05").decode(),
                "coverage_bit_order": "lsb0",
                "covered_cells": 2,
                "total_cells": 4,
                "coverage_complete": False,
                "palette": ["minecraft:air", "minecraft:stone"],
                "block_entities_included": False,
            }
            compressed = gzip.compress(
                json.dumps(snapshot, separators=(",", ":")).encode()
            )
            voxel_path.write_bytes(compressed)
            voxels = {
                "available": True,
                "valid": True,
                "reference": str(voxel_path),
                "coverage_mask_reference": str(voxel_path),
                "artifact_bytes": len(compressed),
                "artifact_sha256": _sha256(compressed),
                "origin": snapshot["origin"],
                "shape": snapshot["shape"],
                "covered_cells": 2,
                "total_cells": 4,
                "coverage_complete": False,
                "dimension": "minecraft:overworld",
            }
            _write_dataset(exports, [_sample(7, voxels=voxels)])
            viewer = DatasetViewer(exports, root / "runtime")
            dataset_id = viewer.list_datasets()[0].dataset_id
            sample_id = viewer.list_sample_summaries(dataset_id).items[0].sample_id

            plane = viewer.get_voxel_slice(dataset_id, sample_id, axis="y", index=0)

            self.assertEqual((2, 1, 2), plane.shape)
            self.assertEqual(("z", "x"), (plane.row_axis, plane.column_axis))
            self.assertEqual("minecraft:air", plane.cells[0][0].block_state)
            self.assertTrue(plane.cells[0][0].covered)
            self.assertFalse(plane.cells[0][1].covered)
            self.assertIsNone(plane.cells[0][1].block_state)
            self.assertEqual("minecraft:stone", plane.cells[1][0].block_state)
            self.assertFalse(plane.coverage_complete)
            with self.assertRaisesRegex(Exception, "outside"):
                viewer.get_voxel_slice(dataset_id, sample_id, axis="y", index=1)

            original_resolver = viewer._verified_artifact

            def resolve_then_tamper(*args, **kwargs):
                artifact = original_resolver(*args, **kwargs)
                voxel_path.write_bytes(compressed + b"tampered")
                return artifact

            with mock.patch.object(
                viewer,
                "_verified_artifact",
                side_effect=resolve_then_tamper,
            ):
                with self.assertRaisesRegex(DatasetValidationError, "changed after validation"):
                    viewer.get_voxel_slice(dataset_id, sample_id, axis="y", index=0)

    def test_stops_voxel_gzip_expansion_at_configured_limit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            exports = root / "exports"
            exports.mkdir()
            voxel_path = exports / "voxel.json.gz"
            payload = b"{" + b" " * 256 + b"}"
            compressed = gzip.compress(payload)
            voxel_path.write_bytes(compressed)
            voxels = {
                "available": True,
                "valid": True,
                "reference": str(voxel_path),
                "coverage_mask_reference": str(voxel_path),
                "artifact_bytes": len(compressed),
                "artifact_sha256": _sha256(compressed),
                "origin": {"x": 0, "y": 0, "z": 0},
                "shape": {"x": 1, "y": 1, "z": 1},
                "covered_cells": 0,
                "total_cells": 1,
                "coverage_complete": False,
                "dimension": "minecraft:overworld",
            }
            _write_dataset(exports, [_sample(1, voxels=voxels)])
            viewer = DatasetViewer(exports, root / "runtime", max_voxel_json_bytes=64)
            dataset_id = viewer.list_datasets()[0].dataset_id
            sample_id = viewer.list_sample_summaries(dataset_id).items[0].sample_id

            with self.assertRaisesRegex(DatasetValidationError, "expands beyond"):
                viewer.get_voxel_slice(dataset_id, sample_id, axis="y", index=0)


if __name__ == "__main__":
    unittest.main()
