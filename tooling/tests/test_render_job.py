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
from mc_recorder.render_job import (
    OWNER,
    RENDER_JOB_TYPE,
    RenderJobResult,
    _owned_render_directory,
    launch_render_job,
    resolve_replay,
)


PLAYER_UUID = "12345678-1234-5678-1234-567812345678"


class ReplayResolutionTest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
