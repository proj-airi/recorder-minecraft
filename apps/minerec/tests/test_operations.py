from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from minerec.errors import RecorderError
from minerec.operations import operation_lock


class OperationLockTest(unittest.TestCase):
    def test_rejects_concurrent_operation_and_releases_after_exit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Path(temporary)
            with operation_lock(runtime, "first"):
                owner = json.loads((runtime / "operation.lock").read_text(encoding="utf-8"))
                self.assertEqual("first", owner["operation"])
                with self.assertRaisesRegex(RecorderError, "already running"):
                    with operation_lock(runtime, "second"):
                        self.fail("lock should not be acquired")
            with operation_lock(runtime, "third"):
                pass


if __name__ == "__main__":
    unittest.main()
