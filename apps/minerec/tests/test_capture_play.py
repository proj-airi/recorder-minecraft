from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from minerec.errors import RecorderError
from minerec.processing.capture import load_capture_metadata, scan_capture_events
from play_fixture import completed_capture, event


class CompletedCaptureTest(unittest.TestCase):
    def test_reads_one_completed_connection_stream(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            metadata_path, events = completed_capture(
                Path(temporary) / "play",
                [event("player_join", 10, 1), event("player_leave", 11, 2)],
            )
            metadata = load_capture_metadata(metadata_path)
            records: list[dict[str, object]] = []
            source = scan_capture_events(events, metadata, records.append)
            self.assertEqual((10, 11), (metadata.start_tick, metadata.end_tick))
            self.assertEqual(2, source.record_count)
            self.assertEqual([1, 2], [record["sequence"] for record in records])

    def test_rejects_capture_without_end_marker(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            metadata_path, _events = completed_capture(
                Path(temporary) / "play",
                [event("player_join", 10, 1)],
            )
            value = json.loads(metadata_path.read_text(encoding="utf-8"))
            value["connection"]["end_server_tick"] = None
            metadata_path.write_text(json.dumps(value) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(RecorderError, "incomplete"):
                load_capture_metadata(metadata_path)

    def test_rejects_nonincreasing_connection_sequence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            metadata_path, events = completed_capture(
                Path(temporary) / "play",
                [event("player_join", 10, 2), event("player_leave", 11, 1)],
            )
            metadata = load_capture_metadata(metadata_path)
            with self.assertRaisesRegex(RecorderError, "strictly increasing"):
                scan_capture_events(events, metadata, lambda _record: None)


if __name__ == "__main__":
    unittest.main()
