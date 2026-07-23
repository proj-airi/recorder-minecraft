from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from minerec.config import initialize, load_config
from minerec.errors import RecorderError
from minerec.processing.artifacts.catalog import (
    ArtifactCatalogResult,
    ReplayArchiveArtifact,
)
from minerec.processing.dataset.viewer import PlayerConnectionSummary
from minerec.render.control.contract import FULL_CLIENT_PRESENTATION_CONTRACT
from minerec.serve.dashboard.service import DashboardService

DATASET = "c" * 32
SESSION = "20260721T000000.000Z-deadbeef"
PLAYER = "00000000-0000-4000-8000-000000000001"
CONNECTION = "00000000-0000-4000-8000-000000000002"
SEGMENT = "00000000-0000-4000-8000-000000000003"


def _connection() -> PlayerConnectionSummary:
    return PlayerConnectionSummary(
        player_uuid=PLAYER,
        player_name="Player",
        connection_id=CONNECTION,
        state_count=11,
        first_tick=10,
        last_tick=20,
        valid_transitions=10,
        rgb_states=0,
        scene_states=11,
    )


def _replay() -> ReplayArchiveArtifact:
    return ReplayArchiveArtifact(
        artifact_id="d" * 32,
        relative_path="segment.zip",
        size_bytes=100,
        sha256="e" * 64,
        session_id=SESSION,
        segment_id=SEGMENT,
        segment_ordinal=0,
        player_uuid=PLAYER,
        connection_id=CONNECTION,
        flashback_capture_contract="client_visible_scene_v1",
    )


class DashboardServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.config = load_config(initialize(root / "recorder.toml", accept_eula=True))
        self.service = DashboardService(self.config)

    def tearDown(self) -> None:
        self.service.close()
        self.temporary.cleanup()

    def test_status_only_reports_dashboard_and_render_pipeline(self) -> None:
        status = self.service.status()
        self.assertEqual("ready", status["dashboard"]["state"])
        self.assertEqual(0, status["render"]["active_jobs"])
        self.assertNotIn("server", status)
        self.assertNotIn("capture", status)

    def test_artifacts_combines_filesystem_and_dataset_catalogs(self) -> None:
        with mock.patch.object(
            self.service.artifact_catalog,
            "scan",
            return_value=ArtifactCatalogResult((), (_replay(),), ()),
        ):
            value = self.service.artifacts()
        self.assertEqual(SEGMENT, value["replay_archives"][0]["segment_id"])
        self.assertIn("datasets", value)
        self.assertNotIn("recordings", value)

    def test_render_job_is_derived_from_dataset_connection_and_replay(self) -> None:
        metadata = SimpleNamespace(
            session_id=SESSION,
            sample_count=11,
            rgb_samples=0,
        )
        with (
            mock.patch.object(
                self.service.dataset_viewer,
                "get_dataset_metadata",
                return_value=metadata,
            ),
            mock.patch.object(
                self.service.dataset_viewer,
                "list_player_connections",
                return_value=(_connection(),),
            ),
            mock.patch.object(
                self.service.artifact_catalog,
                "scan",
                return_value=ArtifactCatalogResult((), (_replay(),), ()),
            ),
        ):
            job = self.service.create_render_job(DATASET)

        self.assertEqual(DATASET, job["dataset_id"])
        self.assertNotIn("recording_id", job)
        self.assertEqual(10, job["payload"]["start_tick"])
        self.assertEqual(20, job["payload"]["end_tick"])
        self.assertEqual(
            FULL_CLIENT_PRESENTATION_CONTRACT,
            job["payload"]["render"]["presentation_contract"],
        )

    def test_render_requires_an_exact_saved_replay(self) -> None:
        metadata = SimpleNamespace(
            session_id=SESSION,
            sample_count=11,
            rgb_samples=0,
        )
        with (
            mock.patch.object(
                self.service.dataset_viewer,
                "get_dataset_metadata",
                return_value=metadata,
            ),
            mock.patch.object(
                self.service.dataset_viewer,
                "list_player_connections",
                return_value=(_connection(),),
            ),
            mock.patch.object(
                self.service.artifact_catalog,
                "scan",
                return_value=ArtifactCatalogResult((), (), ()),
            ),
            self.assertRaisesRegex(RecorderError, "no matching saved Flashback"),
        ):
            self.service.create_render_job(DATASET)

    def test_artifact_render_request_starts_with_dataset_preparation(self) -> None:
        replay = _replay()
        with mock.patch.object(
            self.service.artifact_catalog,
            "scan",
            return_value=ArtifactCatalogResult((), (replay,), ()),
        ):
            job = self.service.create_artifact_render_request(
                replay.artifact_id,
                width=1280,
                height=720,
            )

        self.assertEqual("preparing_dataset", job["state"])
        self.assertIsNone(job["dataset_id"])
        self.assertEqual(replay.artifact_id, job["source_artifact_id"])
        self.assertEqual(SESSION, job["payload"]["session_id"])
        self.assertEqual(PLAYER, job["payload"]["player_uuid"])
        self.assertEqual(CONNECTION, job["payload"]["connection_id"])
        self.assertEqual(1280, job["payload"]["render"]["width"])
        self.assertEqual(
            FULL_CLIENT_PRESENTATION_CONTRACT,
            job["payload"]["render"]["presentation_contract"],
        )

    def test_artifact_render_request_rejects_incomplete_catalog(self) -> None:
        replay = _replay()
        with (
            mock.patch.object(
                self.service.artifact_catalog,
                "scan",
                return_value=ArtifactCatalogResult(
                    (),
                    (replay,),
                    (),
                    truncated=True,
                ),
            ),
            self.assertRaisesRegex(RecorderError, "catalog is incomplete"),
        ):
            self.service.create_artifact_render_request(replay.artifact_id)


if __name__ == "__main__":
    unittest.main()
