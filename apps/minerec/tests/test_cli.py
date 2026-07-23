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


if __name__ == "__main__":
    unittest.main()
