from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
import uuid
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from minerec.errors import RecorderError
from minerec.processing.artifacts.catalog import ArtifactCatalog, ArtifactCatalogResult
from minerec.render.control.sources import (
    FLASHBACK_CAPTURE_CONTRACT,
    ReplaySegmentSource,
    resolve_replay_segments,
)

SESSION = "20260721T000000.000Z-deadbeef"
PLAYER = "00000000-0000-4000-8000-000000000001"
CONNECTION = "00000000-0000-4000-8000-000000000002"
SEGMENT = "00000000-0000-4000-8000-000000000003"
OTHER_PLAYER = "00000000-0000-4000-8000-000000000010"
OTHER_CONNECTION = "00000000-0000-4000-8000-000000000011"


def _write_archive(
    path: Path,
    *,
    session_id: str = SESSION,
    segment_id: str = SEGMENT,
    segment_ordinal: int = 0,
    player_uuid: str = PLAYER,
    connection_id: str | None = CONNECTION,
    schema_version: int = 3,
    hotbar_snapshot_contract: str | None = "item_stack_copy_v1",
    flashback_capture_contract: str | None = FLASHBACK_CAPTURE_CONTRACT,
) -> None:
    identity: dict[str, object] = {
        "schema_version": schema_version,
        "session_id": session_id,
        "segment_id": segment_id,
        "segment_ordinal": segment_ordinal,
        "player_uuid": player_uuid,
    }
    if connection_id is not None:
        identity["connection_id"] = connection_id
    if hotbar_snapshot_contract is not None:
        identity["hotbar_snapshot_contract"] = hotbar_snapshot_contract
    if flashback_capture_contract is not None:
        identity["flashback_capture_contract"] = flashback_capture_contract

    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("metadata.json", json.dumps({"uuid": str(uuid.uuid4())}))
        archive.writestr(
            "arcade_replay_meta.json",
            json.dumps({"mc_recorder": identity}),
        )
        archive.writestr("chunks/c0.flashback", b"replay")


def _resolve(replays_root: Path) -> list[ReplaySegmentSource]:
    return resolve_replay_segments(
        replays_root=replays_root,
        session_id=SESSION,
        player_uuid=PLAYER,
        connection_id=CONNECTION,
    )


class ReplaySegmentResolutionTest(unittest.TestCase):
    def test_resolves_verified_archives_in_deterministic_ordinal_order(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            replays = Path(temporary) / "replays"
            newer = replays / "nested" / "newer.mcpr"
            older = replays / "older.zip"
            newer_segment = "00000000-0000-4000-8000-000000000005"
            _write_archive(
                newer,
                segment_id=newer_segment,
                segment_ordinal=1,
            )
            _write_archive(older, segment_ordinal=0)

            sources = _resolve(replays)

            self.assertEqual([0, 1], [source.segment_ordinal for source in sources])
            self.assertEqual([SEGMENT, newer_segment], [source.segment_id for source in sources])
            self.assertEqual([PLAYER, PLAYER], [source.player_uuid for source in sources])
            self.assertEqual(
                [CONNECTION, CONNECTION],
                [source.connection_id for source in sources],
            )
            for source, archive in zip(sources, (older, newer), strict=True):
                contents = archive.read_bytes()
                self.assertTrue(source.path.is_absolute())
                self.assertEqual(archive.absolute(), source.path)
                self.assertEqual("flashback", source.replay_format)
                self.assertEqual(hashlib.sha256(contents).hexdigest(), source.sha256)
                self.assertEqual(len(contents), source.size_bytes)
                self.assertEqual(
                    FLASHBACK_CAPTURE_CONTRACT,
                    source.flashback_capture_contract,
                )
                self.assertNotIn("path", source.as_json())

    def test_filters_on_the_exact_canonical_session_player_and_connection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            replays = Path(temporary) / "replays"
            exact = replays / "exact.zip"
            _write_archive(exact)
            _write_archive(
                replays / "other-session.zip",
                session_id=f"{SESSION}-other",
                segment_id="00000000-0000-4000-8000-000000000020",
            )
            _write_archive(
                replays / "other-player.zip",
                player_uuid=OTHER_PLAYER,
                segment_id="00000000-0000-4000-8000-000000000021",
            )
            _write_archive(
                replays / "other-connection.zip",
                connection_id=OTHER_CONNECTION,
                segment_id="00000000-0000-4000-8000-000000000022",
            )

            sources = _resolve(replays)

            self.assertEqual([exact.absolute()], [source.path for source in sources])

    def test_rejects_duplicate_matching_segment_ids(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            replays = Path(temporary) / "replays"
            _write_archive(replays / "first.zip", segment_ordinal=0)
            _write_archive(replays / "second.zip", segment_ordinal=1)

            with self.assertRaisesRegex(RecorderError, "duplicate.*segment"):
                _resolve(replays)

    def test_rejects_duplicate_matching_segment_ordinals(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            replays = Path(temporary) / "replays"
            _write_archive(replays / "first.zip")
            _write_archive(
                replays / "second.zip",
                segment_id="00000000-0000-4000-8000-000000000030",
            )

            with self.assertRaisesRegex(RecorderError, "duplicate.*ordinal"):
                _resolve(replays)

    def test_rejects_requested_identity_with_an_invalid_archive_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            replays = Path(temporary) / "replays"
            _write_archive(
                replays / "invalid-contract.zip",
                flashback_capture_contract="unsupported_scene_contract",
            )

            with self.assertRaisesRegex(RecorderError, "invalid capture contracts"):
                _resolve(replays)

    def test_rejects_requested_identity_with_invalid_archive_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            replays = Path(temporary) / "replays"
            _write_archive(
                replays / "invalid-identity.zip",
                segment_ordinal=-1,
            )

            with self.assertRaisesRegex(RecorderError, "invalid segment_ordinal"):
                _resolve(replays)

    def test_rejects_archive_bytes_changed_after_catalog_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            replays = Path(temporary) / "replays"
            replay = replays / "changed.zip"
            _write_archive(replay)
            original_scan = ArtifactCatalog.scan

            def scan_then_change(catalog: ArtifactCatalog) -> ArtifactCatalogResult:
                result = original_scan(catalog)
                replay.write_bytes(b"changed after catalog validation")
                return result

            with (
                mock.patch.object(
                    ArtifactCatalog,
                    "scan",
                    autospec=True,
                    side_effect=scan_then_change,
                ),
                self.assertRaisesRegex(
                    RecorderError,
                    "changed after catalog validation",
                ),
            ):
                _resolve(replays)

    def test_rejects_archive_without_a_supported_connection_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            replays = Path(temporary) / "replays"
            _write_archive(
                replays / "legacy.zip",
                connection_id=None,
                schema_version=1,
                hotbar_snapshot_contract=None,
                flashback_capture_contract=None,
            )

            with self.assertRaisesRegex(RecorderError, "connection identity"):
                _resolve(replays)

    def test_symlink_to_an_archive_outside_the_replay_root_is_not_resolved(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            replays = root / "replays"
            outside = root / "outside.zip"
            _write_archive(outside)
            replays.mkdir()
            (replays / "linked.zip").symlink_to(outside)

            with self.assertRaisesRegex(RecorderError, "no exact saved replay"):
                _resolve(replays)

    def test_no_exact_match_does_not_select_another_saved_archive(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            replays = Path(temporary) / "replays"
            _write_archive(
                replays / "other.zip",
                connection_id=OTHER_CONNECTION,
            )

            with self.assertRaisesRegex(RecorderError, "no exact saved replay"):
                _resolve(replays)


if __name__ == "__main__":
    unittest.main()
