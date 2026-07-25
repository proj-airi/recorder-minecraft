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

from minerec.errors import RecorderError
from minerec.processing.render.job import OWNER, RENDER_JOB_TYPE, RenderJobResult, _owned_render_directory, launch_render_job, prepare_render_job
from play_fixture import CONNECTION, PLAYER, completed_capture, event


class RenderJobTest(unittest.TestCase):
    def test_processor_uses_only_explicit_inputs_and_creates_fpv_frames(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            metadata, events = completed_capture(
                root / "play",
                [event("player_state", 10, 1), event("player_state", 11, 2)],
            )
            replay = root / "replay.zip"
            replay.write_bytes(b"replay")
            output = root / "renders"
            with (
                mock.patch(
                    "minerec.processing.render.job.verified_replay_source",
                    return_value=SimpleNamespace(
                        path=replay.resolve(),
                        player_uuid=PLAYER,
                        connection_id=CONNECTION,
                        replay_id="00000000-0000-4000-8000-000000000003",
                        replay_format="flashback",
                        sha256="b" * 64,
                        size_bytes=6,
                    ),
                ),
            ):
                job = prepare_render_job(
                    metadata,
                    events,
                    replay,
                    output,
                    width=640,
                    height=360,
                    fps=20,
                    first_tick=10,
                    last_tick=11,
                    force=False,
                )
            manifest = json.loads(job.manifest.read_text(encoding="utf-8"))
            self.assertEqual((output / "fpv_frames").resolve(), Path(manifest["output"]))
            self.assertTrue((output / "fpv_frames").is_dir())
            self.assertNotIn("dataset", json.dumps(manifest))
            self.assertEqual("intersection", manifest["timeline"]["range_policy"])
            self.assertEqual(str(metadata.resolve()), manifest["capture"]["metadata"])

    def test_force_replaces_only_an_owned_render_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "renders"
            frames = output / "fpv_frames"
            frames.mkdir(parents=True)
            (output / "render-job.json").write_text(
                json.dumps(
                    {
                        "owner": OWNER,
                        "job_type": RENDER_JOB_TYPE,
                        "output": str(frames.resolve()),
                        "result": str((output / "result.json").resolve()),
                    }
                ),
                encoding="utf-8",
            )
            self.assertTrue(_owned_render_directory(output))
            (frames / "notes.txt").write_text("keep", encoding="utf-8")
            self.assertFalse(_owned_render_directory(output))

    @mock.patch("minerec.processing.render.job.subprocess.run")
    def test_launcher_checks_explicit_replay_has_not_changed(self, run: mock.Mock) -> None:
        run.return_value = SimpleNamespace(returncode=0)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "renderer"
            project.mkdir()
            replay = root / "replay.zip"
            replay.write_bytes(b"replay")
            digest = hashlib.sha256(b"replay").hexdigest()
            output = root / "renders"
            output.mkdir()
            manifest = output / "render-job.json"
            manifest.write_text(json.dumps({"no_gui": False, "source_replay": {"sha256": digest, "size_bytes": 6}}), encoding="utf-8")
            (output / "result.json").write_text(json.dumps({"status": "complete", "no_gui": False, "replay_sha256": digest, "replay_bytes": 6}), encoding="utf-8")
            config = SimpleNamespace(mods=SimpleNamespace(renderer_project=project), paths=SimpleNamespace(base=root))
            result = launch_render_job(config, RenderJobResult(output, manifest, replay, CONNECTION))  # ty:ignore[invalid-argument-type]
            self.assertEqual("complete", result["status"])
            replay.write_bytes(b"changed")
            with self.assertRaisesRegex(RecorderError, "integrity"):
                launch_render_job(config, RenderJobResult(output, manifest, replay, CONNECTION))  # ty:ignore[invalid-argument-type]


if __name__ == "__main__":
    unittest.main()
