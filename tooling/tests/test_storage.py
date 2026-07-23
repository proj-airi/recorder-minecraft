from __future__ import annotations

import fcntl
import hashlib
import json
import os
import sys
import tempfile
import time
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from mc_recorder.processing.capture.episodes import directory_size
from mc_recorder.processing.capture.storage import enforce_quota, pin_sealed_epochs


def _epoch(root: Path, session: str, index: int, size: int, *, active: bool = False) -> Path:
    path = root / session / "epochs" / f"epoch-{index:06d}"
    path.mkdir(parents=True)
    filename = "events.jsonl.inprogress" if active else "events.jsonl"
    content = b"x" * size
    (path / filename).write_bytes(content)
    manifest = {"end_tick": index, "ended_at_ns": index + 1}
    if not active:
        manifest.update(
            {
                "sealed": True,
                "record_count": 1,
                "events_bytes": len(content),
                "events_sha256": hashlib.sha256(content).hexdigest(),
            }
        )
    (path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return path


class StorageTest(unittest.TestCase):
    def test_evicts_oldest_whole_sealed_epoch_but_never_active_epoch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "captures"
            oldest = _epoch(root, "session-a", 0, 1200)
            newest = _epoch(root, "session-a", 1, 1200)
            active = _epoch(root, "session-b", 2, 1600, active=True)
            before = directory_size(root)

            report = enforce_quota(
                root,
                quota_bytes=before - 1,
                warn_percent=90,
                evict_oldest=True,
            )

            self.assertEqual("evicted", report.status)
            self.assertFalse(oldest.exists())
            self.assertTrue(newest.exists())
            self.assertTrue(active.exists())
            self.assertEqual(("epoch-000000",), tuple(item.epoch for item in report.evicted))

    def test_full_storage_warns_when_only_active_data_remains(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "captures"
            active = _epoch(root, "session-a", 0, 2000, active=True)
            report = enforce_quota(root, quota_bytes=100, warn_percent=80, evict_oldest=True)
            self.assertEqual("full", report.status)
            self.assertTrue(active.exists())
            self.assertEqual((), report.evicted)

    def test_never_evicts_epoch_with_failed_integrity_envelope(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "captures"
            corrupt = _epoch(root, "session-a", 0, 2000)
            manifest_path = corrupt / "manifest.json"
            manifest = json.loads(manifest_path.read_text())
            manifest["events_sha256"] = "0" * 64
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            report = enforce_quota(root, quota_bytes=100, warn_percent=80, evict_oldest=True)

            self.assertEqual("full", report.status)
            self.assertTrue(corrupt.exists())
            self.assertEqual((), report.evicted)

    def test_accounts_for_replays_and_evicts_only_stable_completed_archives(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            captures = base / "captures"
            replays = base / "replays"
            captures.mkdir()
            player = replays / "players" / "player-a"
            player.mkdir(parents=True)
            completed = player / "old.zip"
            with zipfile.ZipFile(completed, "w") as archive:
                archive.writestr("metadata.json", "x" * 1500)
                archive.writestr("c0.flashback", "complete")
            old = time.time() - 600
            os.utime(completed, (old, old))
            recent = player / "recent.mcpr"
            with zipfile.ZipFile(recent, "w") as archive:
                archive.writestr("metaData.json", "recent")
                archive.writestr("recording.tmcpr", "complete")
            partial = player / "partial.zip"
            partial.write_bytes(b"PK\x03\x04incomplete")
            temporary_file = player / "writing.zip.tmp"
            temporary_file.write_bytes(b"active")
            active_directory = player / "active.zip"
            active_directory.mkdir()

            before = directory_size(captures) + directory_size(replays)
            report = enforce_quota(
                captures,
                replays_root=replays,
                quota_bytes=before - 1,
                warn_percent=1,
                evict_oldest=True,
                replay_stable_seconds=60,
            )

            self.assertGreaterEqual(report.before_bytes, before)
            self.assertFalse(completed.exists())
            self.assertTrue(recent.exists())
            self.assertTrue(partial.exists())
            self.assertTrue(temporary_file.exists())
            self.assertTrue(active_directory.is_dir())
            self.assertEqual(("replay_archive",), tuple(item.source_kind for item in report.evicted))
            self.assertEqual("players/player-a/old.zip", report.evicted[0].source_path)

    def test_does_not_evict_a_replay_pinned_by_scene_extraction(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            captures = base / "captures"
            replays = base / "replays"
            captures.mkdir()
            player = replays / "players" / "player-a"
            player.mkdir(parents=True)
            replay = player / "pinned.zip"
            with zipfile.ZipFile(replay, "w") as archive:
                archive.writestr("metadata.json", "x" * 1500)
                archive.writestr("c0.flashback", "complete")
            old = time.time() - 600
            os.utime(replay, (old, old))

            with replay.open("rb") as handle:
                fcntl.flock(handle.fileno(), fcntl.LOCK_SH)
                report = enforce_quota(
                    captures,
                    replays_root=replays,
                    quota_bytes=1,
                    warn_percent=80,
                    evict_oldest=True,
                    replay_stable_seconds=60,
                )

            self.assertEqual("full", report.status)
            self.assertTrue(replay.is_file())
            self.assertEqual((), report.evicted)

    def test_does_not_evict_an_epoch_pinned_by_dataset_generation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "captures"
            epoch = _epoch(root, "session-a", 0, 2000)

            with pin_sealed_epochs(root / "session-a") as pinned:
                self.assertEqual((epoch.resolve(),), pinned)
                report = enforce_quota(
                    root,
                    quota_bytes=1,
                    warn_percent=80,
                    evict_oldest=True,
                )

            self.assertEqual("full", report.status)
            self.assertTrue(epoch.is_dir())
            self.assertEqual((), report.evicted)

    def test_replay_bytes_trigger_warning_when_eviction_is_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            captures = base / "captures"
            replays = base / "replays"
            captures.mkdir()
            replays.mkdir()
            (replays / "active.tmp").write_bytes(b"x" * 1000)

            report = enforce_quota(
                captures,
                replays_root=replays,
                quota_bytes=500,
                warn_percent=80,
                evict_oldest=False,
            )

            self.assertEqual("full", report.status)
            self.assertGreaterEqual(report.before_bytes, 1000)
            self.assertIn("capture and replay storage", report.message)

    def test_never_evicts_an_unrelated_zip_from_replay_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            captures = base / "captures"
            replays = base / "replays"
            captures.mkdir()
            replays.mkdir()
            unrelated = replays / "notes.zip"
            with zipfile.ZipFile(unrelated, "w") as archive:
                archive.writestr("notes.txt", "not a ServerReplay recording")
            old = time.time() - 600
            os.utime(unrelated, (old, old))

            report = enforce_quota(
                captures,
                replays_root=replays,
                quota_bytes=1,
                warn_percent=80,
                evict_oldest=True,
                replay_stable_seconds=60,
            )

            self.assertEqual("full", report.status)
            self.assertTrue(unrelated.is_file())
            self.assertEqual((), report.evicted)


if __name__ == "__main__":
    unittest.main()
