from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import unittest
import uuid
import warnings
import zipfile
import zlib
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from minerec.errors import RecorderError
from minerec.processing.artifacts import catalog as artifact_catalog
from minerec.processing.artifacts.catalog import ArtifactCatalog

SESSION = "20260723T000000.000Z-deadbeef"
PLAYER = "00000000-0000-4000-8000-000000000001"
CONNECTION = "00000000-0000-4000-8000-000000000002"
SEGMENT = "00000000-0000-4000-8000-000000000003"


def _write_epoch(
    session: Path,
    index: int,
    *,
    state: str,
    first_tick: int,
    last_tick: int,
) -> None:
    epoch = session / "epochs" / f"epoch-{index}"
    epoch.mkdir(parents=True)
    if state == "open":
        (epoch / "events.jsonl.inprogress").write_text("{}\n", encoding="utf-8")
        return
    if state == "incomplete":
        (epoch / "events.jsonl").write_text("{}\n", encoding="utf-8")
        return

    events = b'{"record_type":"tick"}\n'
    (epoch / "events.jsonl").write_bytes(events)
    (epoch / "manifest.json").write_text(
        json.dumps(
            {
                "sealed": True,
                "record_count": 1,
                "events_bytes": len(events),
                "events_sha256": hashlib.sha256(events).hexdigest(),
                "first_server_tick": first_tick,
                "last_server_tick": last_tick,
            }
        ),
        encoding="utf-8",
    )


def _write_archive(
    path: Path,
    *,
    identity: dict[str, object] | None = None,
    include_identity: bool = True,
) -> None:
    if identity is None:
        identity = {
            "schema_version": 3,
            "session_id": SESSION,
            "segment_id": SEGMENT,
            "segment_ordinal": 0,
            "player_uuid": PLAYER,
            "connection_id": CONNECTION,
            "hotbar_snapshot_contract": "item_stack_copy_v1",
            "flashback_capture_contract": "client_visible_scene_v1",
        }
    path.parent.mkdir(parents=True, exist_ok=True)
    arcade_metadata = {"mc_recorder": identity} if include_identity else {}
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("metadata.json", json.dumps({"uuid": str(uuid.uuid4())}))
        archive.writestr(
            "arcade_replay_meta.json",
            json.dumps(arcade_metadata),
        )
        archive.writestr("chunks/c0.flashback", b"replay")


class ArtifactCatalogCaptureTest(unittest.TestCase):
    def test_maps_internal_episode_states_to_public_capture_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            captures = root / "captures"
            replays = root / "replays"
            complete = captures / "session-complete"
            opened = captures / "session-open"
            incomplete = captures / "session-incomplete"
            _write_epoch(
                complete,
                0,
                state="complete",
                first_tick=10,
                last_tick=19,
            )
            _write_epoch(
                opened,
                0,
                state="open",
                first_tick=20,
                last_tick=29,
            )
            _write_epoch(
                incomplete,
                0,
                state="incomplete",
                first_tick=30,
                last_tick=39,
            )

            result = ArtifactCatalog(captures, replays).scan()
            artifacts = {artifact.session_id: artifact for artifact in result.capture_sessions}

            self.assertIsInstance(result.capture_sessions, tuple)
            self.assertEqual("complete", artifacts["session-complete"].state)
            self.assertEqual(1, artifacts["session-complete"].published_epoch_count)
            self.assertEqual(0, artifacts["session-complete"].unpublished_epoch_count)
            self.assertEqual("open", artifacts["session-open"].state)
            self.assertEqual(0, artifacts["session-open"].published_epoch_count)
            self.assertEqual(1, artifacts["session-open"].unpublished_epoch_count)
            self.assertEqual("incomplete", artifacts["session-incomplete"].state)
            self.assertEqual("session-complete", artifacts["session-complete"].relative_path)
            self.assertEqual(10, artifacts["session-complete"].first_tick)
            self.assertEqual(19, artifacts["session-complete"].last_tick)
            self.assertNotIn("sealed", artifacts["session-complete"].as_json())
            self.assertNotIn(str(root), json.dumps(result.as_json()))


class ArtifactCatalogReplayTest(unittest.TestCase):
    def test_discovers_valid_flashback_archive_with_stable_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            replay = root / "replays" / "players" / PLAYER / "segment.mcpr"
            _write_archive(replay)

            first = ArtifactCatalog(root / "captures", root / "replays").scan()
            second = ArtifactCatalog(root / "captures", root / "replays").scan()

            self.assertIsInstance(first.replay_archives, tuple)
            self.assertEqual(1, len(first.replay_archives))
            artifact = first.replay_archives[0]
            self.assertRegex(artifact.artifact_id, r"^[0-9a-f]{32}$")
            self.assertEqual(artifact.artifact_id, second.replay_archives[0].artifact_id)
            self.assertEqual(f"players/{PLAYER}/segment.mcpr", artifact.relative_path)
            self.assertEqual(replay.stat().st_size, artifact.size_bytes)
            self.assertRegex(artifact.sha256, r"^[0-9a-f]{64}$")
            self.assertEqual(SESSION, artifact.session_id)
            self.assertEqual(SEGMENT, artifact.segment_id)
            self.assertEqual(0, artifact.segment_ordinal)
            self.assertEqual(PLAYER, artifact.player_uuid)
            self.assertEqual(CONNECTION, artifact.connection_id)
            self.assertEqual(
                "client_visible_scene_v1",
                artifact.flashback_capture_contract,
            )
            self.assertNotIn(str(root), json.dumps(first.as_json()))

    def test_reports_archive_missing_exact_recorder_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            replay = root / "replays" / "missing-identity.zip"
            _write_archive(replay, include_identity=False)

            result = ArtifactCatalog(root / "captures", root / "replays").scan()

            self.assertEqual((), result.replay_archives)
            self.assertEqual(1, len(result.issues))
            self.assertEqual("missing-identity.zip", result.issues[0].relative_path)
            self.assertIn("mc_recorder", result.issues[0].message)
            self.assertNotIn(str(root), json.dumps(result.as_json()))

    def test_accepts_supported_legacy_schema_contracts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            replays = root / "replays"
            schema_one_segment = "00000000-0000-4000-8000-000000000011"
            schema_two_segment = "00000000-0000-4000-8000-000000000012"
            _write_archive(
                replays / "schema-one.zip",
                identity={
                    "schema_version": 1,
                    "session_id": SESSION,
                    "segment_id": schema_one_segment,
                    "segment_ordinal": 1,
                    "player_uuid": PLAYER,
                },
            )
            _write_archive(
                replays / "schema-two.mcpr",
                identity={
                    "schema_version": 2,
                    "session_id": SESSION,
                    "segment_id": schema_two_segment,
                    "segment_ordinal": 2,
                    "player_uuid": PLAYER,
                    "connection_id": CONNECTION,
                    "hotbar_snapshot_contract": "item_stack_copy_v1",
                },
            )

            result = ArtifactCatalog(root / "captures", replays).scan()

            self.assertEqual(
                [schema_one_segment, schema_two_segment],
                [artifact.segment_id for artifact in result.replay_archives],
            )
            self.assertIsNone(result.replay_archives[0].connection_id)
            self.assertTrue(all(artifact.flashback_capture_contract is None for artifact in result.replay_archives))

    def test_rejects_invalid_identity_fields_and_capture_contracts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            replays = root / "replays"
            invalid_identities = (
                {"schema_version": 4},
                {
                    "schema_version": 1,
                    "session_id": SESSION,
                    "segment_id": "not-a-uuid",
                    "segment_ordinal": 0,
                    "player_uuid": PLAYER,
                },
                {
                    "schema_version": 1,
                    "session_id": SESSION,
                    "segment_id": SEGMENT,
                    "segment_ordinal": -1,
                    "player_uuid": PLAYER,
                },
                {
                    "schema_version": 2,
                    "session_id": SESSION,
                    "segment_id": SEGMENT,
                    "segment_ordinal": 0,
                    "player_uuid": PLAYER,
                },
                {
                    "schema_version": 3,
                    "session_id": SESSION,
                    "segment_id": SEGMENT,
                    "segment_ordinal": 0,
                    "player_uuid": PLAYER,
                    "hotbar_snapshot_contract": "item_stack_copy_v1",
                },
            )
            for index, identity in enumerate(invalid_identities):
                _write_archive(
                    replays / f"invalid-identity-{index}.zip",
                    identity=identity,
                )

            result = ArtifactCatalog(root / "captures", replays).scan()

            self.assertEqual((), result.replay_archives)
            self.assertEqual(len(invalid_identities), len(result.issues))

    def test_enforces_flashback_structure_and_metadata_read_bound(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            replays = root / "replays"
            replays.mkdir()
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                with zipfile.ZipFile(replays / "duplicate-metadata.zip", "w") as archive:
                    archive.writestr("metadata.json", "{}")
                    archive.writestr("metadata.json", "{}")
                    archive.writestr("arcade_replay_meta.json", "{}")
                    archive.writestr("c0.flashback", b"replay")
            with zipfile.ZipFile(replays / "missing-flashback.zip", "w") as archive:
                archive.writestr("metadata.json", "{}")
                archive.writestr("arcade_replay_meta.json", "{}")
            with zipfile.ZipFile(replays / "oversized-metadata.mcpr", "w") as archive:
                archive.writestr("metadata.json", "{}")
                archive.writestr(
                    "arcade_replay_meta.json",
                    b" " * (1024 * 1024 + 1),
                )
                archive.writestr("c0.flashback", b"replay")

            result = ArtifactCatalog(root / "captures", replays).scan()

            self.assertEqual((), result.replay_archives)
            self.assertEqual(3, len(result.issues))
            self.assertTrue(any("too large" in issue.message for issue in result.issues))

    def test_rejects_symlinked_archive_without_following_it(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            outside = root / "outside.zip"
            _write_archive(outside)
            replays = root / "replays"
            replays.mkdir()
            (replays / "linked.zip").symlink_to(outside)

            result = ArtifactCatalog(root / "captures", replays).scan()

            self.assertEqual((), result.replay_archives)
            self.assertEqual(1, len(result.issues))
            self.assertEqual("linked.zip", result.issues[0].relative_path)
            self.assertIn("symlink", result.issues[0].message)

    def test_rejects_symlinked_intermediate_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            outside = root / "outside"
            _write_archive(outside / "linked.zip")
            replays = root / "replays"
            replays.mkdir()
            (replays / "linked-directory").symlink_to(outside, target_is_directory=True)

            result = ArtifactCatalog(root / "captures", replays).scan()

            self.assertEqual((), result.replay_archives)
            self.assertEqual(1, len(result.issues))
            self.assertEqual("linked-directory", result.issues[0].relative_path)
            self.assertIn("symlink", result.issues[0].message)

    def test_descriptor_traversal_rejects_candidate_outside_replay_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            replays = root / "replays"
            replays.mkdir()
            outside = root / "outside.zip"
            _write_archive(outside)
            root_descriptor = os.open(
                replays,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            )
            try:
                with self.assertRaisesRegex(RecorderError, "outside"):
                    artifact_catalog._open_replay_candidate(
                        root_descriptor,
                        "../outside.zip",
                    )
            finally:
                os.close(root_descriptor)

    def test_reports_non_directory_replay_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            replays = root / "replays"
            replays.write_text("not a directory", encoding="utf-8")

            result = ArtifactCatalog(root / "captures", replays).scan()

            self.assertEqual((), result.replay_archives)
            self.assertEqual(1, len(result.issues))
            self.assertEqual("replay", result.issues[0].artifact_type)
            self.assertEqual(".", result.issues[0].relative_path)
            self.assertNotIn(str(root), result.issues[0].message)

    def test_stable_digest_rejects_file_mutation_between_stat_reads(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            replay = Path(temporary) / "mutating.zip"
            _write_archive(replay)
            file_descriptor = os.open(replay, os.O_RDONLY | os.O_NOFOLLOW)
            before = os.fstat(file_descriptor)
            after = mock.Mock(
                st_dev=before.st_dev,
                st_ino=before.st_ino,
                st_size=before.st_size + 1,
                st_mtime_ns=before.st_mtime_ns + 1,
            )
            try:
                with (
                    mock.patch.object(os, "fstat", side_effect=(before, after)),
                    self.assertRaisesRegex(RecorderError, "changed"),
                ):
                    artifact_catalog._stable_digest(file_descriptor)
            finally:
                os.close(file_descriptor)

    def test_binds_archive_identity_and_digest_to_opened_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            replays = root / "replays"
            replay = replays / "replaced.zip"
            replacement = root / "replacement.zip"
            _write_archive(replay)
            expected_sha256 = hashlib.sha256(replay.read_bytes()).hexdigest()
            _write_archive(
                replacement,
                identity={
                    "schema_version": 3,
                    "session_id": SESSION,
                    "segment_id": "00000000-0000-4000-8000-000000000099",
                    "segment_ordinal": 99,
                    "player_uuid": PLAYER,
                    "connection_id": CONNECTION,
                    "hotbar_snapshot_contract": "item_stack_copy_v1",
                    "flashback_capture_contract": "client_visible_scene_v1",
                },
            )
            archive_identity = artifact_catalog._archive_identity

            def replace_after_read(source: object) -> object:
                identity = archive_identity(source)
                replacement.replace(replay)
                return identity

            with mock.patch.object(
                artifact_catalog,
                "_archive_identity",
                side_effect=replace_after_read,
            ):
                result = ArtifactCatalog(root / "captures", replays).scan()

            self.assertEqual(1, len(result.replay_archives))
            self.assertEqual(expected_sha256, result.replay_archives[0].sha256)
            self.assertEqual(SEGMENT, result.replay_archives[0].segment_id)
            self.assertEqual((), result.issues)

    def test_rejects_duplicate_flashback_payload_name(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            replay = root / "replays" / "duplicate-payload.zip"
            _write_archive(replay)
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                with zipfile.ZipFile(replay, "a") as archive:
                    archive.writestr("chunks/c0.flashback", b"replacement")

            result = ArtifactCatalog(root / "captures", root / "replays").scan()

            self.assertEqual((), result.replay_archives)
            self.assertEqual(1, len(result.issues))
            self.assertIn("duplicate", result.issues[0].message)

    def test_malformed_compression_does_not_hide_valid_sibling(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            replays = root / "replays"
            _write_archive(replays / "bad.zip")
            _write_archive(replays / "good.zip")
            archive_open = zipfile.ZipFile.open
            failed = False

            def fail_first_metadata_read(
                archive: zipfile.ZipFile,
                member: object,
                *args: object,
                **kwargs: object,
            ) -> object:
                nonlocal failed
                filename = member.filename if isinstance(member, zipfile.ZipInfo) else member
                if filename == "arcade_replay_meta.json" and not failed:
                    failed = True
                    raise zlib.error("malformed compressed stream")
                return archive_open(archive, member, *args, **kwargs)

            with mock.patch.object(
                zipfile.ZipFile,
                "open",
                autospec=True,
                side_effect=fail_first_metadata_read,
            ):
                result = ArtifactCatalog(root / "captures", replays).scan()

            self.assertEqual(["good.zip"], [artifact.relative_path for artifact in result.replay_archives])
            self.assertEqual(["bad.zip"], [issue.relative_path for issue in result.issues])

    def test_bounds_replay_candidates_and_reports_truncation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            replays = root / "replays"
            for name in ("a.zip", "b.zip", "c.zip"):
                _write_archive(replays / name)

            with mock.patch.object(artifact_catalog, "MAX_REPLAY_CANDIDATES", 2, create=True):
                result = ArtifactCatalog(root / "captures", replays).scan()

            self.assertEqual(
                ["a.zip", "b.zip"],
                [artifact.relative_path for artifact in result.replay_archives],
            )
            self.assertEqual(1, len(result.issues))
            self.assertIn("candidate limit", result.issues[0].message)

    def test_rejects_archive_over_zip_entry_budget(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            replay = root / "replays" / "too-many-entries.zip"
            _write_archive(replay)
            with zipfile.ZipFile(replay, "a") as archive:
                archive.writestr("extra.txt", "extra")

            with mock.patch.object(artifact_catalog, "MAX_ZIP_ENTRIES", 3, create=True):
                result = ArtifactCatalog(root / "captures", root / "replays").scan()

            self.assertEqual((), result.replay_archives)
            self.assertEqual(1, len(result.issues))
            self.assertIn("entry limit", result.issues[0].message)

    def test_stops_archive_inspection_when_issue_budget_is_full(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            replays = root / "replays"
            for index in range(5):
                _write_archive(replays / f"invalid-{index}.zip", include_identity=False)
            archive_identity = artifact_catalog._archive_identity

            with (
                mock.patch.object(artifact_catalog, "MAX_ARTIFACT_ISSUES", 2),
                mock.patch.object(
                    artifact_catalog,
                    "_archive_identity",
                    wraps=archive_identity,
                ) as inspect_identity,
            ):
                result = ArtifactCatalog(root / "captures", replays).scan()

            self.assertEqual((), result.replay_archives)
            self.assertEqual(2, len(result.issues))
            self.assertIn("truncated", result.issues[-1].message)
            self.assertEqual(1, inspect_identity.call_count)

    def test_invalid_sibling_does_not_hide_valid_archive(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            replays = root / "replays"
            _write_archive(replays / "valid.zip")
            (replays / "invalid.mcpr").write_bytes(b"not a zip")

            result = ArtifactCatalog(root / "captures", replays).scan()

            self.assertEqual(["valid.zip"], [artifact.relative_path for artifact in result.replay_archives])
            self.assertEqual(["invalid.mcpr"], [issue.relative_path for issue in result.issues])

    def test_bounds_issues_from_invalid_replay_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            replays = root / "replays"
            replays.mkdir()
            for index in range(120):
                (replays / f"invalid-{index:03}.zip").write_bytes(b"bad")

            result = ArtifactCatalog(root / "captures", replays).scan()

            self.assertGreater(len(result.issues), 0)
            self.assertLessEqual(len(result.issues), 100)


if __name__ == "__main__":
    unittest.main()
