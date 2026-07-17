from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from mc_recorder.episodes import inspect_epoch


def _epoch(root: Path, content: bytes = b'{"record":1}\n') -> Path:
    path = root / "epoch-000000"
    path.mkdir()
    (path / "events.jsonl").write_bytes(content)
    (path / "manifest.json").write_text(
        json.dumps(
            {
                "sealed": True,
                "record_count": 1,
                "events_bytes": len(content),
                "events_sha256": hashlib.sha256(content).hexdigest(),
            }
        ),
        encoding="utf-8",
    )
    return path


class EpisodeIntegrityTest(unittest.TestCase):
    def test_requires_complete_matching_integrity_envelope(self) -> None:
        mutations = (
            ("sealed", False),
            ("record_count", 2),
            ("events_bytes", 1),
            ("events_sha256", "0" * 64),
        )
        for field, value in mutations:
            with self.subTest(field=field), tempfile.TemporaryDirectory() as temporary:
                epoch = _epoch(Path(temporary))
                manifest_path = epoch / "manifest.json"
                manifest = json.loads(manifest_path.read_text())
                manifest[field] = value
                manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
                info = inspect_epoch(epoch)
                assert info is not None
                self.assertEqual("incomplete", info.status)

    def test_accepts_verified_seal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            info = inspect_epoch(_epoch(Path(temporary)))
            assert info is not None
            self.assertEqual("sealed", info.status)
            self.assertEqual(1, info.event_count)

    def test_inprogress_file_wins_over_other_seal_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            epoch = _epoch(Path(temporary))
            (epoch / "events.jsonl.inprogress").write_text("active", encoding="utf-8")
            info = inspect_epoch(epoch)
            assert info is not None
            self.assertEqual("active", info.status)


if __name__ == "__main__":
    unittest.main()
