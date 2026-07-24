from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from minerec.cli import _parser


class CliTest(unittest.TestCase):
    def test_parser_uses_minerec_program_name(self) -> None:
        self.assertEqual("minerec", _parser().prog)

    def test_python_package_is_minerec(self) -> None:
        self.assertIsNotNone(importlib.util.find_spec("minerec"))
        self.assertIsNone(importlib.util.find_spec("mc_recorder"))

    def test_viewer_accepts_an_optional_bundle_without_config(self) -> None:
        empty = _parser().parse_args(["viewer", "--no-open"])
        selected = _parser().parse_args(["viewer", "example.mcplay.zip", "--no-open"])

        self.assertIsNone(empty.bundle)
        self.assertEqual(Path("example.mcplay.zip"), selected.bundle)

    def test_bundle_create_accepts_optional_render_pair(self) -> None:
        args = _parser().parse_args(
            [
                "bundle",
                "create",
                "play.dataset",
                "--fpv",
                "fpv.mp4",
                "--fpv-timeline",
                "fpv.timeline.jsonl",
            ]
        )

        self.assertEqual("create", args.bundle_command)
        self.assertEqual(Path("play.dataset"), args.dataset)
        self.assertEqual(Path("fpv.mp4"), args.fpv)


if __name__ == "__main__":
    unittest.main()
