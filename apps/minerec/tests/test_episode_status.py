from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from minerec.processing.capture.episodes import inspect_episode


def _active_episode(root: Path, *, terminal_status: str | None = None, clean: bool = False) -> Path:
    episode = root / "captures" / "session-a"
    epoch = episode / "epochs" / "epoch-000000"
    epoch.mkdir(parents=True)
    (episode / "manifest.json").write_text(
        json.dumps({"session_id": "session-a"}),
        encoding="utf-8",
    )
    (epoch / "events.jsonl.inprogress").write_text('{"partial":true}\n', encoding="utf-8")
    if terminal_status is not None:
        (episode / "session_end.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "session_id": "session-a",
                    "status": terminal_status,
                    "clean_shutdown": clean,
                }
            ),
            encoding="utf-8",
        )
    return episode


class EpisodeStatusTest(unittest.TestCase):
    def test_live_inprogress_epoch_remains_active_without_terminal_marker(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            episode = _active_episode(root)
            info = inspect_episode(episode)
            assert info is not None
            self.assertEqual("active", info.status)

    def test_aborted_inprogress_epoch_reports_terminal_incomplete(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            episode = _active_episode(root, terminal_status="incomplete", clean=False)
            info = inspect_episode(episode)
            assert info is not None
            self.assertEqual("incomplete", info.status)
            self.assertEqual(1, info.active_epoch_count)

    def test_explicit_terminal_failure_reports_failed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            episode = _active_episode(Path(temporary), terminal_status="failed", clean=False)
            info = inspect_episode(episode)
            assert info is not None
            self.assertEqual("failed", info.status)

    def test_inconsistent_clean_terminal_marker_with_unsealed_epoch_is_incomplete(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            episode = _active_episode(Path(temporary), terminal_status="complete", clean=True)
            info = inspect_episode(episode)
            assert info is not None
            self.assertEqual("incomplete", info.status)


if __name__ == "__main__":
    unittest.main()
