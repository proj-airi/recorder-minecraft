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
from minerec.processing.replays import FLASHBACK_CAPTURE_CONTRACT, verified_replay_source

PLAYER = "00000000-0000-4000-8000-000000000001"
CONNECTION = "00000000-0000-4000-8000-000000000002"
REPLAY = "00000000-0000-4000-8000-000000000003"


def _write_replay(path: Path, *, replay_id: str = REPLAY, player: str = PLAYER) -> None:
    metadata = {"chunks": {"c0": {}}}
    arcade_metadata = {
        "mc_recorder": {
            "replay_id": replay_id,
            "player_uuid": player,
            "connection_id": CONNECTION,
            "flashback_capture_contract": FLASHBACK_CAPTURE_CONTRACT,
        },
    }
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("metadata.json", json.dumps(metadata))
        archive.writestr("arcade_replay_meta.json", json.dumps(arcade_metadata))
        archive.writestr("chunks/c0.flashback", b"replay")


class ReplayInputsTest(unittest.TestCase):
    def test_explicit_replay_is_validated(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            replay = root / "replay.zip"
            _write_replay(replay)
            source = verified_replay_source(replay, player_uuid=PLAYER, connection_id=CONNECTION)
            self.assertEqual(REPLAY, source.replay_id)
            self.assertEqual(replay.resolve(), source.path)
            self.assertEqual(hashlib.sha256(replay.read_bytes()).hexdigest(), source.sha256)
            self.assertEqual(replay.stat().st_size, source.size_bytes)

    def test_no_directory_discovery_or_identity_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            replay = Path(temporary) / "other-player.zip"
            _write_replay(replay, player="00000000-0000-4000-8000-000000000099")
            with self.assertRaisesRegex(RecorderError, "identity does not match"):
                verified_replay_source(replay, player_uuid=PLAYER, connection_id=CONNECTION)

    def test_symlink_input_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            replay = root / "replay.zip"
            _write_replay(replay)
            link = root / "link.zip"
            link.symlink_to(replay)
            with self.assertRaisesRegex(RecorderError, "non-symlinked"):
                verified_replay_source(link, player_uuid=PLAYER, connection_id=CONNECTION)


if __name__ == "__main__":
    unittest.main()
