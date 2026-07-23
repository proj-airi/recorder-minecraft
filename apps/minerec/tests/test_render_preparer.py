from __future__ import annotations

import tempfile
import unittest
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest import mock

from minerec.config import RecorderConfig
from minerec.processing.artifacts.catalog import (
    ArtifactCatalogResult,
    ReplayArchiveArtifact,
)
from minerec.processing.capture.snapshot import CaptureSnapshot
from minerec.render.control.preparer import RenderPreparer

SESSION = "20260724T000000.000Z-deadbeef"
PLAYER = "00000000-0000-4000-8000-000000000001"
CONNECTION = "00000000-0000-4000-8000-000000000002"
ARTIFACT_ID = "a" * 32


def _artifact() -> ReplayArchiveArtifact:
    return ReplayArchiveArtifact(
        artifact_id=ARTIFACT_ID,
        relative_path="server/replay.zip",
        size_bytes=100,
        sha256="b" * 64,
        session_id=SESSION,
        segment_id="00000000-0000-4000-8000-000000000003",
        segment_ordinal=0,
        player_uuid=PLAYER,
        connection_id=CONNECTION,
        flashback_capture_contract="client_visible_scene_v1",
    )


def _claim() -> dict[str, Any]:
    return {
        "job": {
            "id": "00000000-0000-4000-8000-000000000004",
            "payload": {
                "source_artifact_id": ARTIFACT_ID,
                "session_id": SESSION,
                "player_uuid": PLAYER,
                "connection_id": CONNECTION,
                "render": {
                    "width": 640,
                    "height": 360,
                    "fps": 20,
                    "no_gui": False,
                },
            },
        },
        "lease_token": "lease-token",
    }


class RenderPreparerTest(unittest.TestCase):
    def test_snapshots_exports_verifies_and_binds_one_artifact_request(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = cast(
                RecorderConfig,
                SimpleNamespace(
                    paths=SimpleNamespace(
                        captures=root / "captures",
                        replays=root / "replays",
                        exports=root / "exports",
                        runtime=root / "runtime",
                    )
                ),
            )
            (config.paths.captures / SESSION).mkdir(parents=True)
            queue = mock.Mock()
            queue.claim_preparation.return_value = _claim()
            queue.bind_dataset.return_value = {"state": "queued"}
            catalog = mock.Mock()
            catalog.scan.return_value = ArtifactCatalogResult(
                capture_sessions=(),
                replay_archives=(_artifact(),),
                issues=(),
            )
            viewer = mock.Mock()
            viewer.get_dataset_metadata.return_value = SimpleNamespace(
                session_id=SESSION,
                selected_from_tick=9,
                selected_to_tick=41,
            )
            viewer.list_player_connections.return_value = (
                SimpleNamespace(
                    player_uuid=PLAYER,
                    connection_id=CONNECTION,
                    first_tick=10,
                    last_tick=40,
                ),
            )
            snapshot = CaptureSnapshot(
                episode=root / "snapshot" / SESSION,
                source_episode=config.paths.captures / SESSION,
                session_id=SESSION,
                player_uuid=PLAYER,
                connection_id=CONNECTION,
                selection_start_tick=9,
                selection_end_tick=41,
                provenance={"format": "append_prefix_v1"},
            )

            @contextmanager
            def snapshot_connection(
                *_args: object,
                **_kwargs: object,
            ) -> Iterator[CaptureSnapshot]:
                yield snapshot

            def export_episode(_episode: Path, output: Path, **kwargs: object) -> None:
                output.mkdir(parents=True)
                self.assertEqual([PLAYER], kwargs["players"])
                self.assertEqual([CONNECTION], kwargs["connections"])
                self.assertEqual(9, kwargs["first_tick"])
                self.assertEqual(41, kwargs["last_tick"])
                self.assertEqual(snapshot.provenance, kwargs["source_snapshot"])

            preparer = RenderPreparer(
                config,
                preparer_id="preparer-test",
                queue=queue,
                artifact_catalog=catalog,
                dataset_viewer=viewer,
                snapshot=snapshot_connection,
                exporter=export_episode,
            )

            result = preparer.run_once()

            self.assertEqual({"state": "queued"}, result)
            expected_output = config.paths.exports / f"{SESSION}-{PLAYER}-{CONNECTION}.dataset"
            dataset_id = preparer.dataset_id(expected_output.name)
            viewer.get_dataset_metadata.assert_called_once_with(dataset_id)
            queue.bind_dataset.assert_called_once_with(
                _claim()["job"]["id"],
                "lease-token",
                dataset_id=dataset_id,
                start_tick=10,
                end_tick=40,
                selection_start_tick=9,
                selection_end_tick=41,
            )
            queue.fail_preparation.assert_not_called()

    def test_rejects_artifact_identity_mismatch_and_fails_the_request(self) -> None:
        config = cast(
            RecorderConfig,
            SimpleNamespace(
                paths=SimpleNamespace(
                    captures=Path("/captures"),
                    replays=Path("/replays"),
                    exports=Path("/exports"),
                    runtime=Path("/runtime"),
                )
            ),
        )
        queue = mock.Mock()
        queue.claim_preparation.return_value = _claim()
        queue.fail_preparation.return_value = {"state": "failed"}
        catalog = mock.Mock()
        mismatched = _artifact()
        catalog.scan.return_value = ArtifactCatalogResult(
            capture_sessions=(),
            replay_archives=(
                ReplayArchiveArtifact(
                    artifact_id=mismatched.artifact_id,
                    relative_path=mismatched.relative_path,
                    size_bytes=mismatched.size_bytes,
                    sha256=mismatched.sha256,
                    session_id=mismatched.session_id,
                    segment_id=mismatched.segment_id,
                    segment_ordinal=mismatched.segment_ordinal,
                    player_uuid=mismatched.player_uuid,
                    connection_id="00000000-0000-4000-8000-000000000099",
                    flashback_capture_contract=(mismatched.flashback_capture_contract),
                ),
            ),
            issues=(),
        )
        preparer = RenderPreparer(
            config,
            queue=queue,
            artifact_catalog=catalog,
            dataset_viewer=mock.Mock(),
        )

        result = preparer.run_once()

        self.assertEqual({"state": "failed"}, result)
        error = queue.fail_preparation.call_args.args[2]
        self.assertIsInstance(error, str)
        self.assertIn("identity", error)
        queue.bind_dataset.assert_not_called()

    def test_returns_none_when_no_preparation_is_waiting(self) -> None:
        config = cast(
            RecorderConfig,
            SimpleNamespace(
                paths=SimpleNamespace(
                    captures=Path("/captures"),
                    replays=Path("/replays"),
                    exports=Path("/exports"),
                    runtime=Path("/runtime"),
                )
            ),
        )
        queue = mock.Mock()
        queue.claim_preparation.return_value = None
        preparer = RenderPreparer(
            config,
            queue=queue,
            artifact_catalog=mock.Mock(),
            dataset_viewer=mock.Mock(),
        )

        self.assertIsNone(preparer.run_once())
