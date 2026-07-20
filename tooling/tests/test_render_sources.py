from __future__ import annotations

import json
import sys
import tempfile
import unittest
import uuid
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from mc_recorder.errors import RecorderError
from mc_recorder.render_sources import resolve_replay_segments


SESSION = "20260721T000000.000Z-deadbeef"
PLAYER = "00000000-0000-4000-8000-000000000001"
CONNECTION = "00000000-0000-4000-8000-000000000002"
SEGMENT = "00000000-0000-4000-8000-000000000003"


def _write_archive(path: Path, *, segment: str = SEGMENT) -> None:
    metadata = {
        "uuid": str(uuid.uuid4()),
        "mc_recorder": {
            "schema_version": 1,
            "session_id": SESSION,
            "segment_id": segment,
            "segment_ordinal": 0,
            "player_uuid": PLAYER,
            "connection_id": CONNECTION,
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("metadata.json", json.dumps(metadata))
        archive.writestr("c0.flashback", b"replay")


def _write_ledger(control: Path, output: str, **overrides: object) -> None:
    row = {
        "segment_id": SEGMENT,
        "segment_ordinal": 0,
        "player_uuid": PLAYER,
        "player_name": "Player",
        "connection_id": CONNECTION,
        "replay_format": "flashback",
        "state": "saved",
        "output": output,
    }
    row.update(overrides)
    sessions = control / "sessions"
    sessions.mkdir(parents=True)
    (sessions / f"{SESSION}.replay-segments.json").write_text(
        json.dumps({"schema_version": 1, "session_id": SESSION, "segments": [row]}),
        encoding="utf-8",
    )


class ReplaySegmentResolutionTest(unittest.TestCase):
    def test_maps_container_paths_and_returns_a_stable_integrity_envelope(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            replay = root / "replays" / "players" / PLAYER / "segment.zip"
            _write_archive(replay)
            _write_ledger(root / "control", f"/replays/players/{PLAYER}/segment.zip")

            sources = resolve_replay_segments(
                control_root=root / "control",
                replays_root=root / "replays",
                session_id=SESSION,
                player_uuid=PLAYER,
                connection_id=CONNECTION,
            )

            self.assertEqual(1, len(sources))
            self.assertEqual(replay.resolve(), sources[0].path)
            self.assertEqual(replay.stat().st_size, sources[0].size_bytes)
            self.assertRegex(sources[0].sha256, r"^[0-9a-f]{64}$")

    def test_rejects_ledger_paths_outside_the_replay_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            outside = root / "outside.zip"
            _write_archive(outside)
            _write_ledger(root / "control", str(outside))

            with self.assertRaisesRegex(RecorderError, "escapes"):
                resolve_replay_segments(
                    control_root=root / "control",
                    replays_root=root / "replays",
                    session_id=SESSION,
                    player_uuid=PLAYER,
                    connection_id=CONNECTION,
                )

    def test_rejects_archive_identity_mismatch_even_when_the_ledger_matches(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            replay = root / "replays" / "players" / PLAYER / "segment.zip"
            _write_archive(replay, segment="00000000-0000-4000-8000-000000000099")
            _write_ledger(root / "control", str(replay))

            with self.assertRaisesRegex(RecorderError, "identity does not match"):
                resolve_replay_segments(
                    control_root=root / "control",
                    replays_root=root / "replays",
                    session_id=SESSION,
                    player_uuid=PLAYER,
                    connection_id=CONNECTION,
                )

    def test_reports_a_recording_segment_as_not_ready(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _write_ledger(root / "control", "/replays/pending.zip", state="recording")

            with self.assertRaisesRegex(RecorderError, "still being saved"):
                resolve_replay_segments(
                    control_root=root / "control",
                    replays_root=root / "replays",
                    session_id=SESSION,
                    player_uuid=PLAYER,
                    connection_id=CONNECTION,
                )


if __name__ == "__main__":
    unittest.main()
