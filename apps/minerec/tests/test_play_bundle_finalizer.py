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

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from minerec.config import initialize, load_config
from minerec.errors import RecorderError
from minerec.processing.bundle import open_bundle
from minerec.processing.bundle.finalizer import finalize_dataset_bundle
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
    source_replays = (
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
            position = state["position"]
            builder.add_frame(
                tick,
                frame_id=f"{SEGMENT}:{tick}",
                replay_tick=100 + tick,
                dimension=DIMENSION,
                subject_position=(position["x"], position["y"], position["z"]),  # type: ignore[index]
                coverage_complete=True,
            )
            builder.set_entity(tick, "subject", _entity(tick))
        builder.publish(scene_directory / "scene-v1.sqlite3", expected_ticks=(10, 11))

    states = b"".join(json.dumps(_state(tick), sort_keys=True, separators=(",", ":")).encode() + b"\n" for tick in (10, 11))
    actions = b"".join(
        json.dumps(
            {
                "schema_version": 2,
                "session_id": SESSION,
                "server_tick": tick,
                "sequence": tick,
                "player_uuid": PLAYER,
                "connection_id": CONNECTION,
                "action_type": "control_state",
                "applied": True,
                "payload": {"forward": tick == 11},
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        + b"\n"
        for tick in (10, 11)
    )
    (dataset / "states.jsonl").write_bytes(states)
    (dataset / "actions.jsonl").write_bytes(actions)
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

            first = finalize_dataset_bundle(config, dataset)
            second = finalize_dataset_bundle(config, dataset)

            self.assertFalse(first.reused)
            self.assertTrue(second.reused)
            self.assertEqual(first.path, second.path)
            self.assertEqual("v1", first.path.relative_to(config.paths.bundles).parts[0])
            with open_bundle(first.path, scene_validator=validate_scene_store_v2) as opened:
                self.assertIsNone(opened.fpv_path)
                self.assertEqual(replay.read_bytes(), opened.replay_descriptors[0].archive_path.read_bytes())
                self.assertIn("unopened_container_contents_unknown", opened.metadata["known_modality_gaps"])
                self.assertEqual((10, 11), validate_scene_store_v2(opened.scene_path).ticks)

            viewer = ViewerService()
            try:
                summary = viewer.import_bundle(first.path)
                self.assertEqual(first.bundle_id, summary["bundle_id"])
                self.assertEqual(10, viewer.tick(10)["state"]["tick"])
                self.assertEqual(2, len(viewer.actions(from_tick=10, to_tick=11, limit=10)["actions"]))
                trajectory = viewer.trajectory(max_points=2)
                self.assertEqual(2, trajectory["total_points"])
                self.assertEqual(2, trajectory["returned_points"])
                self.assertEqual(9, len(viewer.scene_slice(tick=10, dimension=DIMENSION, y=64, radius=1)["cells"]))
                self.assertEqual(1, len(viewer.replays()["replays"]))
                with self.assertRaisesRegex(RecorderError, "no FPV"):
                    viewer.render_path()
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
                            "scene_frame": frame,
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                    + b"\n"
                    for frame in range(2)
                )
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
                summary = viewer.import_bundle(rendered.path)
                self.assertEqual(rendered.bundle_id, summary["bundle_id"])
                self.assertEqual(render_descriptor, summary["metadata"]["render"])
            finally:
                viewer.close()


if __name__ == "__main__":
    unittest.main()
