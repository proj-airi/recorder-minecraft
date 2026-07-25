from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from minerec.processing.actions import extract_actions
from play_fixture import completed_capture, event


class ActionExtractionTest(unittest.TestCase):
    def test_writes_only_the_selected_connection_to_an_explicit_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            records = [
                event("control_state", 10, 1, forward=True),
                event("packet_apply", 10, 2, packet={"packet_type": "minecraft:test"}),
            ]
            metadata, events = completed_capture(root / "play", records)
            output = root / "play" / "actions.jsonl"

            result = extract_actions(metadata, events, output)

            rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(2, result.record_count)
            self.assertEqual(["control_state", "packet_apply"], [row["source_record_type"] for row in rows])
            self.assertNotIn("events_sha256", rows[0])


if __name__ == "__main__":
    unittest.main()
