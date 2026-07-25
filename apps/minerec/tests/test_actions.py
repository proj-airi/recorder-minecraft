from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from minerec.processing.actions import extract_actions


class ActionExtractionTest(unittest.TestCase):
    def test_writes_only_the_selected_connection_to_an_explicit_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            session = root / "intermediate-session"
            epoch = session / "epochs" / "epoch-000000"
            epoch.mkdir(parents=True)
            records = [
                self._record("control_state", 10, 1, "11111111-1111-4111-8111-111111111111"),
                self._record("packet_apply", 10, 2, "11111111-1111-4111-8111-111111111111"),
                self._record("packet_apply", 10, 3, "44444444-4444-4444-8444-444444444444"),
            ]
            data = b"".join(json.dumps(record, sort_keys=True, separators=(",", ":")).encode() + b"\n" for record in records)
            (epoch / "events.jsonl").write_bytes(data)
            (epoch / "manifest.json").write_text(
                json.dumps(
                    {
                        "sealed": True,
                        "record_count": len(records),
                        "events_bytes": len(data),
                        "events_sha256": hashlib.sha256(data).hexdigest(),
                    }
                ),
                encoding="utf-8",
            )
            (session / "manifest.json").write_text(
                json.dumps({"session_id": "session-test"}),
                encoding="utf-8",
            )
            output = root / "play" / "actions.jsonl"

            result = extract_actions(
                session,
                output,
                player_uuid="22222222-2222-4222-8222-222222222222",
                connection_id="11111111-1111-4111-8111-111111111111",
            )

            rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(2, result.record_count)
            self.assertEqual(["control_state", "packet_apply"], [row["source_record_type"] for row in rows])
            self.assertNotIn("events_sha256", rows[0])

    @staticmethod
    def _record(record_type: str, tick: int, sequence: int, connection_id: str) -> dict[str, object]:
        return {
            "schema_version": 1,
            "record_type": record_type,
            "session_id": "session-test",
            "epoch_index": 0,
            "server_tick": tick,
            "sequence": sequence,
            "recorded_at_ns": sequence,
            "recorded_at_unix_ms": sequence,
            "player_uuid": "22222222-2222-4222-8222-222222222222",
            "player_name": "RecorderPlayer",
            "entity_id": 1,
            "connection_id": connection_id,
            "connection_start_server_tick": 10,
            "packet": {"packet_type": "minecraft:test"},
        }


if __name__ == "__main__":
    unittest.main()
