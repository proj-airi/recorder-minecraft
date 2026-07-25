from __future__ import annotations

import io
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from minerec import cli


class ProcessorCliTest(unittest.TestCase):
    def test_render_forwards_explicit_files_and_output(self) -> None:
        config = SimpleNamespace(paths=SimpleNamespace(runtime=Path("/runtime")))
        prepared = SimpleNamespace(manifest=Path("/renders/render-job.json"))
        with (
            mock.patch.object(cli, "load_config", return_value=config),
            mock.patch.object(cli, "operation_lock"),
            mock.patch.object(cli, "prepare_render_job", return_value=prepared) as prepare,
        ):
            self.assertEqual(
                0,
                cli.run(
                    [
                        "render",
                        "--metadata",
                        "/input/metadata.json",
                        "--events",
                        "/input/capture/events.jsonl",
                        "--replay",
                        "/input/capture/replay.zip",
                        "--output",
                        "/output/renders",
                        "--prepare-only",
                    ]
                ),
            )
        self.assertEqual(Path("/input/metadata.json"), prepare.call_args.args[0])
        self.assertEqual(Path("/input/capture/events.jsonl"), prepare.call_args.args[1])
        self.assertEqual(Path("/input/capture/replay.zip"), prepare.call_args.args[2])
        self.assertEqual(Path("/output/renders"), prepare.call_args.args[3])

    def test_scene_forwards_explicit_capture_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = SimpleNamespace(paths=SimpleNamespace(runtime=root / "runtime"))
            prepared = SimpleNamespace(manifest=root / "job" / "scene-job.json")
            output = io.StringIO()
            with (
                mock.patch.object(cli, "load_config", return_value=config),
                mock.patch.object(cli, "operation_lock"),
                mock.patch.object(cli, "cleanup_stale_scene_jobs"),
                mock.patch.object(cli, "prepare_scene_job", return_value=prepared) as prepare,
                mock.patch.object(cli.sys, "stdout", output),
            ):
                code = cli.run(
                    [
                        "scene",
                        "extract",
                        "--metadata",
                        str(root / "metadata.json"),
                        "--events",
                        str(root / "capture" / "events.jsonl"),
                        "--replay",
                        str(root / "capture" / "replay.zip"),
                        "--output",
                        str(root / "scene.sqlite3"),
                        "--prepare-only",
                    ]
                )
            self.assertEqual(0, code)
            self.assertEqual(root / "metadata.json", prepare.call_args.args[1])
            self.assertEqual(root / "capture" / "events.jsonl", prepare.call_args.args[2])
            self.assertEqual(root / "capture" / "replay.zip", prepare.call_args.args[3])


if __name__ == "__main__":
    unittest.main()
