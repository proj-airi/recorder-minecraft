from __future__ import annotations

import hashlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from minerec import cli

PLAYER = "00000000-0000-4000-8000-000000000001"
CONNECTION = "00000000-0000-4000-8000-000000000002"


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
                        "/input/events",
                        "--player",
                        PLAYER,
                        "--connection",
                        CONNECTION,
                        "--replay",
                        "/input/replay.zip",
                        "--output",
                        "/output/renders",
                        "--prepare-only",
                    ]
                ),
            )
        self.assertEqual(Path("/input/events"), prepare.call_args.args[0])
        self.assertEqual(Path("/input/replay.zip"), prepare.call_args.args[1])
        self.assertEqual(Path("/output/renders"), prepare.call_args.args[2])

    def test_scene_forwards_each_explicit_replay(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            events = root / "events"
            epoch = events / "epochs" / "epoch-000000"
            epoch.mkdir(parents=True)
            data = b"{}\n"
            (epoch / "events.jsonl").write_bytes(data)
            (epoch / "manifest.json").write_text(
                json.dumps({"sealed": True, "record_count": 1, "events_bytes": len(data), "events_sha256": hashlib.sha256(data).hexdigest()}),
                encoding="utf-8",
            )
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
                        str(events),
                        "--player",
                        PLAYER,
                        "--connection",
                        CONNECTION,
                        "--replay",
                        str(root / "one.zip"),
                        "--replay",
                        str(root / "two.zip"),
                        "--output",
                        str(root / "scene.sqlite3"),
                        "--prepare-only",
                    ]
                )
            self.assertEqual(0, code)
            self.assertEqual([root / "one.zip", root / "two.zip"], prepare.call_args.args[2])
            self.assertEqual((epoch.resolve(),), prepare.call_args.kwargs["pinned_epoch_paths"])


if __name__ == "__main__":
    unittest.main()
