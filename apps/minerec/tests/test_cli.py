from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from mc_recorder.cli import _parser


class CliTest(unittest.TestCase):
    def test_parser_uses_minerec_program_name(self) -> None:
        self.assertEqual("minerec", _parser().prog)


if __name__ == "__main__":
    unittest.main()
