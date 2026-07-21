from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from mc_recorder.errors import RecorderError
from mc_recorder.render_contract import FULL_CLIENT_PRESENTATION_CONTRACT
from mc_recorder.render_job import (
    OWNER,
    RENDER_JOB_TYPE,
    RenderJobResult,
    _owned_render_directory,
    launch_render_job,
    prepare_render_job,
    resolve_replay,
)


PLAYER_UUID = "12345678-1234-5678-1234-567812345678"
CONNECTION_UUID = "87654321-4321-4678-9234-567812345678"


class ReplayResolutionTest(unittest.TestCase):
    def test_direct_prepared_gui_job_does_not_claim_dataset_bound_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            episode = root / "episode"
            episode.mkdir()
            (episode / "manifest.json").write_text("{}", encoding="utf-8")
            replay = root / "replay.zip"
            replay.write_bytes(b"replay")
            validation = SimpleNamespace(valid=True, sealed_epochs=1, session_id="session")
            connection = "22222222-2222-2222-2222-222222222222"
            patches = (
                mock.patch("mc_recorder.render_job.validate_episode", return_value=validation),
                mock.patch(
                    "mc_recorder.render_job._select_connection",
                    return_value=(connection, 10, 20),
                ),
                mock.patch("mc_recorder.render_job._detect_replay_format", return_value="flashback"),
                mock.patch(
                    "mc_recorder.render_job._stable_file_digest",
                    return_value=(hashlib.sha256(b"replay").hexdigest(), 6),
                ),
                mock.patch("mc_recorder.render_job.sha256_file", return_value="a" * 64),
            )
            for patch in patches:
                patch.start()
                self.addCleanup(patch.stop)

            gui = prepare_render_job(
                episode,
                replay,
                root / "gui-job",
                player_uuid=PLAYER_UUID,
                connection_id=connection,
                width=640,
                height=360,
                fps=20,
                first_tick=10,
                last_tick=20,
                force=False,
                no_gui=False,
            )
            no_gui = prepare_render_job(
                episode,
                replay,
                root / "no-gui-job",
                player_uuid=PLAYER_UUID,
                connection_id=connection,
                width=640,
                height=360,
                fps=20,
                first_tick=10,
                last_tick=20,
                force=False,
                no_gui=True,
            )

            gui_manifest = json.loads(gui.manifest.read_text(encoding="utf-8"))
            no_gui_manifest = json.loads(no_gui.manifest.read_text(encoding="utf-8"))
            self.assertNotIn("presentation_contract", gui_manifest)
            self.assertNotIn("structured_hud", gui_manifest)
            self.assertNotIn("presentation_contract", no_gui_manifest)

    def test_rejects_invalid_player_before_building_a_replay_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(RecorderError, "invalid player UUID"):
                resolve_replay(Path(temporary), "../players", None)

    def test_rejects_an_explicit_replay_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "recording.zip"
            target.write_bytes(b"not needed for path resolution")
            link = root / "linked.zip"
            link.symlink_to(target)

            with self.assertRaisesRegex(RecorderError, "symlinked"):
                resolve_replay(root, PLAYER_UUID, link)

    def test_owned_render_directory_rejects_unrecognized_nested_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            job = Path(temporary) / "job"
            frames = job / "frames"
            frames.mkdir(parents=True)
            (job / "render-job.json").write_text(
                (
                    "{"
                    f'"owner":"{OWNER}",'
                    f'"job_type":"{RENDER_JOB_TYPE}",'
                    f'"output":"{frames.resolve()}",'
                    f'"result":"{(job / "result.json").resolve()}"'
                    "}"
                ),
                encoding="utf-8",
            )
            (frames / "frame_000001.png").write_bytes(b"owned")
            (frames / "voxels").mkdir()
            (frames / "voxels" / "voxel_000000000001.json.gz").write_bytes(b"owned")
            self.assertTrue(_owned_render_directory(job))

            (frames / "personal-notes.txt").write_text("do not delete", encoding="utf-8")
            self.assertFalse(_owned_render_directory(job))

    @mock.patch("mc_recorder.render_job.subprocess.run")
    def test_launcher_accepts_atomic_no_coverage_only_for_intersection_jobs(
        self, run: mock.Mock
    ) -> None:
        run.return_value = SimpleNamespace(returncode=0)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "renderer-mod"
            project.mkdir()
            (root / "gradlew").write_text("wrapper", encoding="utf-8")
            replay = root / "replay.zip"
            replay.write_bytes(b"replay")
            digest = hashlib.sha256(b"replay").hexdigest()
            directory = root / "job"
            directory.mkdir()
            manifest = directory / "render-job.json"
            manifest.write_text(
                json.dumps(
                    {
                        "timeline": {"range_policy": "intersection"},
                        "source_replay": {"sha256": digest, "size_bytes": 6},
                    }
                ),
                encoding="utf-8",
            )
            (directory / "result.json").write_text(
                json.dumps(
                    {
                        "status": "no_coverage",
                        "replay_sha256": digest,
                        "replay_bytes": 6,
                    }
                ),
                encoding="utf-8",
            )
            config = SimpleNamespace(
                mods=SimpleNamespace(renderer_project=project),
                paths=SimpleNamespace(base=root, runtime=root / "runtime"),
            )

            result = launch_render_job(
                config,
                RenderJobResult(directory, manifest, replay, "connection"),
            )

            self.assertEqual("no_coverage", result["status"])

            manifest_value = json.loads(manifest.read_text())
            manifest_value["no_gui"] = False
            manifest.write_text(json.dumps(manifest_value), encoding="utf-8")
            with self.assertRaisesRegex(RecorderError, "no_gui does not match"):
                launch_render_job(
                    config,
                    RenderJobResult(directory, manifest, replay, "connection"),
                )

    @mock.patch("mc_recorder.render_job.subprocess.run")
    def test_launcher_validates_gui_presentation_contract(self, run: mock.Mock) -> None:
        run.return_value = SimpleNamespace(returncode=0)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "renderer-mod"
            project.mkdir()
            (root / "gradlew").write_text("wrapper", encoding="utf-8")
            replay = root / "replay.zip"
            replay.write_bytes(b"replay")
            digest = hashlib.sha256(b"replay").hexdigest()
            directory = root / "job"
            directory.mkdir()
            hud = directory / "hud-states.jsonl"
            hud.write_bytes(b'{"server_tick":10}\n{"server_tick":11}\n')
            hud_digest = hashlib.sha256(hud.read_bytes()).hexdigest()
            hud_result = {
                "schema_version": 1,
                "type": "mc-recorder-structured-hud-v1",
                "format": "jsonl",
                "sha256": hud_digest,
                "size_bytes": hud.stat().st_size,
                "records": 2,
                "start_server_tick": 10,
                "end_server_tick": 11,
                "dataset_id": "a" * 32,
                "dataset_manifest_sha256": "b" * 64,
                "samples_sha256": "c" * 64,
                "session_id": "session-a",
                "player_uuid": PLAYER_UUID,
                "connection_id": CONNECTION_UUID,
            }
            manifest = directory / "render-job.json"
            manifest_value = {
                "no_gui": False,
                "presentation_contract": FULL_CLIENT_PRESENTATION_CONTRACT,
                "session_id": "session-a",
                "player_uuid": PLAYER_UUID,
                "connection_id": CONNECTION_UUID,
                "global_start_tick": 10,
                "global_end_tick": 11,
                "structured_hud": {
                    **hud_result,
                    "path": str(hud.resolve()),
                },
                "source_replay": {"sha256": digest, "size_bytes": 6},
            }
            manifest.write_text(json.dumps(manifest_value), encoding="utf-8")
            result_path = directory / "result.json"
            result_value = {
                "status": "complete",
                "no_gui": False,
                "presentation_contract": FULL_CLIENT_PRESENTATION_CONTRACT,
                "structured_hud": dict(hud_result),
                "replay_sha256": digest,
                "replay_bytes": 6,
            }
            result_path.write_text(json.dumps(result_value), encoding="utf-8")
            config = SimpleNamespace(
                mods=SimpleNamespace(renderer_project=project),
                paths=SimpleNamespace(base=root, runtime=root / "runtime"),
            )
            job = RenderJobResult(directory, manifest, replay, CONNECTION_UUID)

            result = launch_render_job(config, job)
            self.assertEqual(
                FULL_CLIENT_PRESENTATION_CONTRACT,
                result["presentation_contract"],
            )

            result_value["presentation_contract"] = "direct_camera_v0"
            result_path.write_text(json.dumps(result_value), encoding="utf-8")
            with self.assertRaisesRegex(RecorderError, "presentation_contract does not match"):
                launch_render_job(config, job)

            result_value["presentation_contract"] = FULL_CLIENT_PRESENTATION_CONTRACT
            result_path.write_text(json.dumps(result_value), encoding="utf-8")
            hud.write_bytes(b"tampered")
            with self.assertRaisesRegex(RecorderError, "failed its integrity envelope"):
                launch_render_job(config, job)
            hud.write_bytes(b'{"server_tick":10}\n{"server_tick":11}\n')

            result_value["structured_hud"]["sha256"] = "0" * 64
            result_path.write_text(json.dumps(result_value), encoding="utf-8")
            with self.assertRaisesRegex(RecorderError, "structured_hud does not match"):
                launch_render_job(config, job)
            result_value["structured_hud"]["sha256"] = hud_digest
            result_path.write_text(json.dumps(result_value), encoding="utf-8")

            manifest_value["presentation_contract"] = "direct_camera_v0"
            manifest.write_text(json.dumps(manifest_value), encoding="utf-8")
            with self.assertRaisesRegex(RecorderError, "presentation_contract is not supported"):
                launch_render_job(config, job)

    @mock.patch("mc_recorder.render_job.subprocess.run")
    def test_launcher_preserves_legacy_unmarked_gui_results(self, run: mock.Mock) -> None:
        run.return_value = SimpleNamespace(returncode=0)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "renderer-mod"
            project.mkdir()
            (root / "gradlew").write_text("wrapper", encoding="utf-8")
            replay = root / "replay.zip"
            replay.write_bytes(b"replay")
            digest = hashlib.sha256(b"replay").hexdigest()
            directory = root / "job"
            directory.mkdir()
            manifest = directory / "render-job.json"
            manifest.write_text(
                json.dumps(
                    {
                        "no_gui": False,
                        "source_replay": {"sha256": digest, "size_bytes": 6},
                    }
                ),
                encoding="utf-8",
            )
            (directory / "result.json").write_text(
                json.dumps(
                    {
                        "status": "complete",
                        "no_gui": False,
                        "replay_sha256": digest,
                        "replay_bytes": 6,
                    }
                ),
                encoding="utf-8",
            )
            config = SimpleNamespace(
                mods=SimpleNamespace(renderer_project=project),
                paths=SimpleNamespace(base=root, runtime=root / "runtime"),
            )

            result = launch_render_job(
                config,
                RenderJobResult(directory, manifest, replay, "connection"),
            )

            self.assertEqual("complete", result["status"])


if __name__ == "__main__":
    unittest.main()
