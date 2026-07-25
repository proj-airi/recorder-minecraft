from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from minerec.errors import RecorderError
from minerec.processing.replays import FLASHBACK_CAPTURE_CONTRACT, resolve_replay_segments

PLAYER = "00000000-0000-4000-8000-000000000001"
CONNECTION = "00000000-0000-4000-8000-000000000002"
SEGMENT = "00000000-0000-4000-8000-000000000003"


def _write_replay(path: Path, *, ordinal: int = 0, segment_id: str = SEGMENT, player: str = PLAYER) -> None:
    metadata = {
        "chunks": {"c0": {}},
        "mc_recorder": {
            "segment_id": segment_id,
            "segment_ordinal": ordinal,
            "player_uuid": player,
            "connection_id": CONNECTION,
            "flashback_capture_contract": FLASHBACK_CAPTURE_CONTRACT,
        },
    }
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("metadata.json", json.dumps(metadata))
        archive.writestr("chunks/c0.flashback", b"replay")


class ReplayInputsTest(unittest.TestCase):
    def test_explicit_inputs_are_validated_and_sorted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = root / "first.zip"
            second = root / "second.zip"
            second_id = "00000000-0000-4000-8000-000000000004"
            _write_replay(first, ordinal=0)
            _write_replay(second, ordinal=1, segment_id=second_id)

            sources = resolve_replay_segments(
                [second, first],
                player_uuid=PLAYER,
                connection_id=CONNECTION,
            )

            self.assertEqual([0, 1], [source.segment_ordinal for source in sources])
            self.assertEqual([first.resolve(), second.resolve()], [source.path for source in sources])
            self.assertEqual(hashlib.sha256(first.read_bytes()).hexdigest(), sources[0].sha256)
            self.assertEqual(first.stat().st_size, sources[0].size_bytes)

    def test_no_directory_discovery_or_identity_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            replay = Path(temporary) / "other-player.zip"
            _write_replay(replay, player="00000000-0000-4000-8000-000000000099")
            with self.assertRaisesRegex(RecorderError, "identity does not match"):
                resolve_replay_segments([replay], player_uuid=PLAYER, connection_id=CONNECTION)

    def test_duplicate_ordinals_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = root / "first.zip"
            second = root / "second.zip"
            _write_replay(first)
            _write_replay(second, segment_id="00000000-0000-4000-8000-000000000004")
            with self.assertRaisesRegex(RecorderError, "repeat"):
                resolve_replay_segments([first, second], player_uuid=PLAYER, connection_id=CONNECTION)

    def test_symlink_input_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            replay = root / "replay.zip"
            _write_replay(replay)
            link = root / "link.zip"
            link.symlink_to(replay)
            with self.assertRaisesRegex(RecorderError, "non-symlinked"):
                resolve_replay_segments([link], player_uuid=PLAYER, connection_id=CONNECTION)


if __name__ == "__main__":
    unittest.main()
