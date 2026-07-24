from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid
import zipfile
from pathlib import Path
from typing import cast

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from minerec.config import initialize, load_config
from minerec.errors import RecorderError
from minerec.processing.bundle import BundleError, open_bundle
from minerec.processing.bundle.finalizer import finalize_dataset_bundle
from minerec.processing.capture.exporter import (
    canonical_action_jsonl_line,
    canonical_state_jsonl_line,
)
from minerec.processing.scene.store import SceneIdentity, SceneStoreBuilder
from minerec.processing.scene.store_v2 import validate_scene_store_v2
from minerec.serve.viewer.service import ViewerService

PLAYER = "00000000-0000-4000-8000-000000000001"
CONNECTION = "00000000-0000-4000-8000-000000000002"
SEGMENT = "00000000-0000-4000-8000-000000000003"
SESSION = "session-finalizer"
DIMENSION = "minecraft:overworld"


def _write_replay(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    identity = {
        "schema_version": 3,
        "session_id": SESSION,
        "segment_id": SEGMENT,
        "segment_ordinal": 0,
        "player_uuid": PLAYER,
        "connection_id": CONNECTION,
        "hotbar_snapshot_contract": "item_stack_copy_v1",
        "flashback_capture_contract": "client_visible_scene_v1",
    }
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("metadata.json", json.dumps({"uuid": str(uuid.uuid4())}))
        archive.writestr("arcade_replay_meta.json", json.dumps({"mc_recorder": identity}))
        archive.writestr("chunks/c0.flashback", b"exact-original-replay")


def _state(tick: int) -> dict[str, object]:
    x = float(tick - 9)
    return {
        "schema_version": 2,
        "session_id": SESSION,
        "epoch_index": 0,
        "server_tick": tick,
        "sequence": tick,
        "recorded_at_ns": tick * 1_000,
        "recorded_at_unix_ms": 1_753_331_200_000 + (tick - 10) * 50,
        "player_uuid": PLAYER,
        "player_name": "FinalizerPlayer",
        "connection_id": CONNECTION,
        "entity_id": 7,
        "dimension": DIMENSION,
        "position": {"x": x, "y": 64.0, "z": 2.0},
        "velocity": {"x": 0.1, "y": 0.0, "z": 0.0},
        "rotation": {"yaw": 10.0, "pitch": 0.0, "head_yaw": 10.0},
        "alive": True,
        "on_ground": True,
        "pose": "standing",
        "sprinting": False,
        "sneaking": False,
        "swimming": False,
        "fall_flying": False,
        "using_item": False,
        "use_item_remaining_ticks": 0,
        "game_mode": "survival",
        "health": 20.0,
        "max_health": 20.0,
        "absorption": 0.0,
        "armor": 0,
        "air": 300,
        "max_air": 300,
        "food_level": 20,
        "saturation": 5.0,
        "experience_level": 0,
        "experience_progress": 0.0,
        "total_experience": 0,
        "selected_slot": 0,
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
        "inventory": [],
    }


def _entity(tick: int) -> dict[str, object]:
    x = float(tick - 9)
    return {
        "instance_id": "subject",
        "dimension": DIMENSION,
        "type_id": "minecraft:player",
        "network_id": 7,
        "uuid": PLAYER,
        "position": [x, 64.0, 2.0],
        "velocity": [0.1, 0.0, 0.0],
        "rotation": [10.0, 0.0],
        "aabb": [x - 0.3, 64.0, 1.7, x + 0.3, 65.8, 2.3],
    }


def _provenance(
    identity: SceneIdentity,
    sources: tuple[dict[str, object], ...],
) -> dict[str, object]:
    return {
        "scope": "client_visible",
        "metadata_policy": "full_packet_metadata",
        "result": {
            "schema_version": 1,
            "result_type": "mc-recorder-scene-extraction-result-v1",
            "status": "complete",
            "job_id": "finalizer-test",
            "session_id": identity.session_id,
            "player_uuid": identity.player_uuid,
            "connection_id": identity.connection_id,
            "global_start_tick": 10,
            "global_end_tick": 11,
            "scope": "client_visible",
            "metadata_policy": "full_packet_metadata",
            "flashback_capture_contract": "client_visible_scene_v1",
            "source_replays": list(sources),
            "subject_poses": {
                "format": "mc-recorder-subject-poses-v1",
                "path": "/verified/job/subject-poses.jsonl",
                "sha256": "4" * 64,
                "size_bytes": 100,
                "record_count": 2,
                "first_tick": 10,
                "last_tick": 11,
                "source_epochs": [
                    {
                        "epoch_index": 0,
                        "events_sha256": "5" * 64,
                        "events_size_bytes": 1000,
                        "record_count": 20,
                    }
                ],
            },
            "stream": {
                "path": "/verified/stream",
                "format": "mc-recorder-scene-stream-v1",
                "frames_index": "frames.jsonl",
                "frames_size_bytes": 1,
                "frames_sha256": "1" * 64,
                "frame_count": 2,
                "changes_index": "changes.jsonl",
                "changes_size_bytes": 1,
                "changes_sha256": "2" * 64,
                "change_count": 0,
                "blobs_directory": "blobs",
                "blob_count": 0,
                "blob_bytes": 0,
            },
            "ignored_packet_counts": {},
            "covered_tick_count": 2,
        },
    }


def _write_dataset(root: Path, replay: Path) -> Path:
    dataset = root / "connection.dataset"
    scene_directory = dataset / "scene"
    scene_directory.mkdir(parents=True)
    replay_bytes = replay.read_bytes()
    source_replays: tuple[dict[str, object], ...] = (
        {
            "segment_id": SEGMENT,
            "segment_ordinal": 0,
            "path": str(replay.resolve()),
            "sha256": hashlib.sha256(replay_bytes).hexdigest(),
            "size_bytes": len(replay_bytes),
            "format": "flashback",
        },
    )
    identity = SceneIdentity(SESSION, PLAYER, CONNECTION)
    with SceneStoreBuilder(
        identity,
        start_tick=10,
        end_tick=11,
        source_replays=source_replays,
        provenance=_provenance(identity, source_replays),
    ) as builder:
        for tick in (10, 11):
            state = _state(tick)
            position = cast(dict[str, float], state["position"])
            builder.add_frame(
                tick,
                frame_id=f"{SEGMENT}:{tick}",
                replay_tick=100 + tick,
                dimension=DIMENSION,
                subject_position=(position["x"], position["y"], position["z"]),
                coverage_complete=True,
            )
            builder.set_entity(tick, "subject", _entity(tick))
        builder.publish(scene_directory / "scene-v1.sqlite3", expected_ticks=(10, 11))

    def source_event(record_type: str, tick: int, sequence: int, **payload: object) -> dict[str, object]:
        return {
            "schema_version": 1,
            "record_type": record_type,
            "session_id": SESSION,
            "epoch_index": 0,
            "server_tick": tick,
            "sequence": sequence,
            "recorded_at_ns": sequence * 1_000,
            **payload,
        }

    raw_states = []
    for tick, sequence in ((10, 2), (11, 4)):
        record = _state(tick)
        record.update(
            {
                "schema_version": 1,
                "record_type": "player_state",
                "sequence": sequence,
                "recorded_at_ns": sequence * 1_000,
            }
        )
        raw_states.append(record)
    raw_actions = [
        source_event(
            "control_state",
            tick,
            sequence,
            player_uuid=PLAYER,
            connection_id=CONNECTION,
            forward=tick == 11,
        )
        for tick, sequence in ((10, 3), (11, 5))
    ]
    source_records = [
        source_event(
            "player_join",
            10,
            1,
            player_uuid=PLAYER,
            connection_id=CONNECTION,
        ),
        raw_states[0],
        raw_actions[0],
        raw_states[1],
        raw_actions[1],
        source_event(
            "player_leave",
            12,
            6,
            player_uuid=PLAYER,
            connection_id=CONNECTION,
        ),
    ]
    source_bytes = b"".join(json.dumps(record, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n" for record in source_records)
    source_sha256 = hashlib.sha256(source_bytes).hexdigest()
    epoch_manifest_value = {
        "schema_version": 1,
        "session_id": SESSION,
        "epoch_index": 0,
        "sealed": True,
        "rotation_reason": "consumer_snapshot",
        "forced_seal": False,
        "record_count": len(source_records),
        "events_bytes": len(source_bytes),
        "events_sha256": source_sha256,
        "first_server_tick": 10,
        "last_server_tick": 12,
        "first_sequence": 1,
        "last_sequence": 6,
        "record_counts": {
            "control_state": 2,
            "player_join": 1,
            "player_leave": 1,
            "player_state": 2,
        },
    }
    epoch_manifest_bytes = (json.dumps(epoch_manifest_value, indent=2, sort_keys=True) + "\n").encode("utf-8")
    epoch_manifest_sha256 = hashlib.sha256(epoch_manifest_bytes).hexdigest()
    epoch_hashes = {0: (source_sha256, epoch_manifest_sha256)}
    states = b"".join(canonical_state_jsonl_line(record, epoch_hashes) for record in raw_states)
    actions = b"".join(canonical_action_jsonl_line(record, epoch_hashes) for record in raw_actions)

    source_episode = root / "artifacts" / "captures" / SESSION
    source_epoch = source_episode / "epochs" / "epoch-000000"
    source_epoch.mkdir(parents=True)
    source_manifest_bytes = json.dumps({"schema_version": 1, "session_id": SESSION}).encode("utf-8")
    (source_episode / "manifest.json").write_bytes(source_manifest_bytes)
    source_path = source_epoch / "events.jsonl.inprogress"
    source_path.write_bytes(source_bytes)
    source_manifest_sha256 = hashlib.sha256(source_manifest_bytes).hexdigest()
    (dataset / "states.jsonl").write_bytes(states)
    (dataset / "actions.jsonl").write_bytes(actions)
    (dataset / "samples.jsonl").write_bytes(b"{}\n")
    (dataset / "modalities.jsonl").write_bytes(b"{}\n")
    files = {}
    for path in sorted(candidate for candidate in dataset.rglob("*") if candidate.is_file()):
        relative = path.relative_to(dataset).as_posix()
        data = path.read_bytes()
        files[relative] = {"sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data)}
    (dataset / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 2,
                "owner": "mc-recorder",
                "format": "mc-recorder-jsonl-v2",
                "session_id": SESSION,
                "source_manifest_sha256": source_manifest_sha256,
                "source": {
                    "episode": str(source_episode.resolve()),
                    "manifest_sha256": source_manifest_sha256,
                    "sealed_epochs": 1,
                    "active_epochs_skipped": 0,
                    "epochs": [
                        {
                            "epoch_index": 0,
                            "manifest_sha256": epoch_manifest_sha256,
                            "events_sha256": source_sha256,
                            "events_bytes": len(source_bytes),
                            "record_count": len(source_records),
                        }
                    ],
                    "snapshot": {
                        "format": "append_prefix_v1",
                        "source_episode": str(source_episode.resolve()),
                        "session_id": SESSION,
                        "player_uuid": PLAYER,
                        "connection_id": CONNECTION,
                        "selection_start_tick": 10,
                        "selection_end_tick": 12,
                        "segments": [
                            {
                                "epoch_index": 0,
                                "kind": "active_prefix",
                                "source": "epochs/epoch-000000/events.jsonl.inprogress",
                                "observed_bytes": len(source_bytes),
                                "bytes": len(source_bytes),
                                "sha256": source_sha256,
                                "record_count": len(source_records),
                                "first_sequence": 1,
                                "last_sequence": 6,
                                "first_server_tick": 10,
                                "last_server_tick": 12,
                            }
                        ],
                    },
                },
                "selection": {
                    "players": [PLAYER],
                    "connections": [CONNECTION],
                    "from_tick": 10,
                    "to_tick": 12,
                    "frame_attachments": [],
                    "scene_attachment": {"verified": True},
                },
                "timeline": {"tick_rate_hz": 20, "sample_rate_hz": 20},
                "modalities": {
                    "state": {"available": True, "records": 2, "file": "states.jsonl"},
                    "actions": {"available": True, "records": 2, "file": "actions.jsonl"},
                    "scene": {
                        "availability": "per-sample",
                        "records_attached": 2,
                        "index": "modalities.jsonl",
                        "store": "scene/scene-v1.sqlite3",
                    },
                },
                "files": files,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return dataset


class PlayBundleFinalizerTest(unittest.TestCase):
    def test_finalizes_dataset_and_preserves_exact_replay_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config_path = initialize(root / "recorder.toml")
            config = load_config(config_path)
            replay = config.paths.replays / "players" / PLAYER / "segment.zip"
            _write_replay(replay)
            dataset = _write_dataset(root, replay)
            dataset_manifest = json.loads((dataset / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(12, dataset_manifest["selection"]["to_tick"])

            first = finalize_dataset_bundle(config, dataset)
            second = finalize_dataset_bundle(config, dataset)

            self.assertFalse(first.reused)
            self.assertTrue(second.reused)
            self.assertEqual(first.path, second.path)
            self.assertEqual("v1", first.path.relative_to(config.paths.bundles).parts[0])
            with open_bundle(first.path, scene_validator=validate_scene_store_v2) as opened:
                self.assertIsNone(opened.fpv_path)
                self.assertEqual(11, opened.metadata["tick_range"]["end"])
                self.assertEqual(replay.read_bytes(), opened.replay_descriptors[0].archive_path.read_bytes())
                self.assertIn("unopened_container_contents_unknown", opened.metadata["known_modality_gaps"])
                scene_info = validate_scene_store_v2(opened.scene_path)
                self.assertEqual((10, 11), scene_info.ticks)
                self.assertEqual(
                    f"replays/000000--{SEGMENT}.zip",
                    scene_info.source_replays[0]["path"],
                )
                self.assertEqual(0, scene_info.source_replays[0]["segment_ordinal"])

            viewer = ViewerService()
            try:
                staged = viewer.stage_bundle(first.path)
                staged_import_id = staged["staged_import_id"]
                with self.assertRaisesRegex(RecorderError, "no play bundle"):
                    viewer.summary()
                self.assertEqual(
                    first.bundle_id,
                    viewer.summary(staged_import_id=staged_import_id)["bundle_id"],
                )
                self.assertEqual(
                    10,
                    viewer.tick(10, staged_import_id=staged_import_id)["state"]["tick"],
                )
                summary = viewer.commit_staged(staged_import_id)
                self.assertEqual(first.bundle_id, summary["bundle_id"])
                self.assertEqual(10, viewer.tick(10)["state"]["tick"])
                self.assertEqual(2, len(viewer.actions(from_tick=10, to_tick=11, limit=10)["actions"]))
                trajectory = viewer.trajectory(max_points=2)
                self.assertEqual(2, trajectory["total_points"])
                self.assertEqual(2, trajectory["returned_points"])
                self.assertEqual(9, len(viewer.scene_slice(tick=10, dimension=DIMENSION, y=64, radius=1)["cells"]))
                self.assertEqual(1, len(viewer.replays()["replays"]))
                discarded = viewer.stage_bundle(first.path)
                viewer.discard_staged(discarded["staged_import_id"])
                self.assertEqual(first.bundle_id, viewer.summary()["bundle_id"])
                with self.assertRaisesRegex(RecorderError, "no FPV"):
                    with viewer.render_lease(first.bundle_id):
                        pass
            finally:
                viewer.close()

    def test_rejects_dataset_without_wall_clock_state_time(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = load_config(initialize(root / "recorder.toml"))
            replay = config.paths.replays / "segment.zip"
            _write_replay(replay)
            dataset = _write_dataset(root, replay)
            states_path = dataset / "states.jsonl"
            rows = [json.loads(line) for line in states_path.read_text(encoding="utf-8").splitlines()]
            rows[0].pop("recorded_at_unix_ms")
            states_path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
            manifest = json.loads((dataset / "manifest.json").read_text(encoding="utf-8"))
            data = states_path.read_bytes()
            manifest["files"]["states.jsonl"] = {
                "sha256": hashlib.sha256(data).hexdigest(),
                "size_bytes": len(data),
            }
            (dataset / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

            with self.assertRaisesRegex(RecorderError, "recorded_at_unix_ms"):
                finalize_dataset_bundle(config, dataset)

    def test_rejects_dataset_without_closed_connection_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = load_config(initialize(root / "recorder.toml"))
            replay = config.paths.replays / "segment.zip"
            _write_replay(replay)
            dataset = _write_dataset(root, replay)
            manifest_path = dataset / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["source"].pop("snapshot")
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            with self.assertRaisesRegex(RecorderError, "closed-connection snapshot"):
                finalize_dataset_bundle(config, dataset)

    def test_rejects_partial_or_active_dataset_selection(self) -> None:
        for case in ("partial", "active"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                config = load_config(initialize(root / "recorder.toml"))
                replay = config.paths.replays / "segment.zip"
                _write_replay(replay)
                dataset = _write_dataset(root, replay)
                manifest_path = dataset / "manifest.json"
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                if case == "partial":
                    manifest["selection"]["from_tick"] = 11
                    expected = "full closed connection envelope"
                else:
                    manifest["source"]["active_epochs_skipped"] = 1
                    expected = "closed snapshot"
                manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

                with self.assertRaisesRegex(RecorderError, expected):
                    finalize_dataset_bundle(config, dataset)

    def test_rejects_fabricated_actions_borrowing_real_source_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = load_config(initialize(root / "recorder.toml"))
            replay = config.paths.replays / "segment.zip"
            _write_replay(replay)
            dataset = _write_dataset(root, replay)
            actions_path = dataset / "actions.jsonl"
            rows = [json.loads(line) for line in actions_path.read_text(encoding="utf-8").splitlines()]
            rows[0]["payload"]["forward"] = not rows[0]["payload"]["forward"]
            actions_path.write_text(
                "".join(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n" for row in rows),
                encoding="utf-8",
            )
            manifest_path = dataset / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            actions_bytes = actions_path.read_bytes()
            manifest["files"]["actions.jsonl"] = {
                "sha256": hashlib.sha256(actions_bytes).hexdigest(),
                "size_bytes": len(actions_bytes),
            }
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            with self.assertRaisesRegex(RecorderError, "exactly reconstruct"):
                finalize_dataset_bundle(config, dataset)

    def test_rejects_a_tampered_capture_snapshot_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = load_config(initialize(root / "recorder.toml"))
            replay = config.paths.replays / "segment.zip"
            _write_replay(replay)
            dataset = _write_dataset(root, replay)
            manifest = json.loads((dataset / "manifest.json").read_text(encoding="utf-8"))
            source_episode = Path(manifest["source"]["snapshot"]["source_episode"])
            source_path = source_episode / manifest["source"]["snapshot"]["segments"][0]["source"]
            tampered = source_path.read_bytes().replace(b"player_join", b"player_joim", 1)
            self.assertEqual(source_path.stat().st_size, len(tampered))
            source_path.write_bytes(tampered)

            with self.assertRaisesRegex(RecorderError, "retained prefix hash"):
                finalize_dataset_bundle(config, dataset)

    def test_attaches_verified_fpv_as_a_new_immutable_revision(self) -> None:
        ffmpeg = shutil.which("ffmpeg")
        ffprobe = shutil.which("ffprobe")
        if ffmpeg is None or ffprobe is None:
            self.skipTest("FFmpeg is unavailable outside the Pixi environment")

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = load_config(initialize(root / "recorder.toml"))
            replay = config.paths.replays / "players" / PLAYER / "segment.zip"
            _write_replay(replay)
            dataset = _write_dataset(root, replay)
            without_render = finalize_dataset_bundle(config, dataset)

            video = root / "fpv.mp4"
            subprocess.run(
                (
                    ffmpeg,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=c=black:s=16x16:r=20:d=0.1",
                    "-frames:v",
                    "2",
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    "-r",
                    "20",
                    "-movflags",
                    "+faststart",
                    "-an",
                    "-y",
                    str(video),
                ),
                check=True,
                capture_output=True,
                timeout=30,
            )
            timeline = root / "fpv.timeline.jsonl"
            timeline.write_bytes(
                b"".join(
                    json.dumps(
                        {
                            "frame_index": frame,
                            "pts": frame,
                            "server_tick": 10 + frame,
                            "replay_tick": 110 + frame,
                            "scene_frame": f"{SEGMENT}:{10 + frame}",
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                    + b"\n"
                    for frame in range(2)
                )
            )

            mismatched_timeline = root / "mismatched-fpv.timeline.jsonl"
            timeline_rows = [json.loads(line) for line in timeline.read_text(encoding="utf-8").splitlines()]
            timeline_rows[0]["scene_frame"] = "wrong-scene-frame"
            mismatched_timeline.write_text(
                "".join(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n" for row in timeline_rows),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(BundleError, "exactly match its Scene V2 frame alignment"):
                finalize_dataset_bundle(
                    config,
                    dataset,
                    fpv_path=video,
                    fpv_timeline_path=mismatched_timeline,
                )

            rendered = finalize_dataset_bundle(
                config,
                dataset,
                fpv_path=video,
                fpv_timeline_path=timeline,
            )

            self.assertNotEqual(without_render.bundle_id, rendered.bundle_id)
            with open_bundle(
                rendered.path,
                scene_validator=validate_scene_store_v2,
            ) as opened:
                self.assertIsNotNone(opened.fpv_path)
                assert opened.fpv_path is not None
                self.assertEqual(video.read_bytes(), opened.fpv_path.read_bytes())
                render_descriptor = opened.metadata["render"]
                self.assertEqual(2, render_descriptor["video"]["frame_count"])

            viewer = ViewerService()
            try:
                viewer.import_bundle(without_render.path)
                staged = viewer.stage_bundle(rendered.path)
                staged_import_id = staged["staged_import_id"]
                self.assertEqual(without_render.bundle_id, viewer.summary()["bundle_id"])
                self.assertEqual(
                    rendered.bundle_id,
                    viewer.summary(staged_import_id=staged_import_id)["bundle_id"],
                )
                summary = viewer.commit_staged(staged_import_id)
                self.assertEqual(rendered.bundle_id, summary["bundle_id"])
                self.assertEqual(render_descriptor, summary["metadata"]["render"])

                with viewer.render_lease(rendered.bundle_id) as leased_media:
                    self.assertTrue(leased_media.is_file())
                    replacement = viewer.stage_bundle(without_render.path)
                    viewer.commit_staged(replacement["staged_import_id"])
                    self.assertTrue(leased_media.is_file())
                self.assertFalse(leased_media.exists())
            finally:
                viewer.close()


if __name__ == "__main__":
    unittest.main()
