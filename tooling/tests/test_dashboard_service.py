from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from mc_recorder.config import initialize, load_config
from mc_recorder.dashboard_service import DashboardService
from mc_recorder.dataset_viewer import DatasetCatalogResult
from mc_recorder.errors import RecorderError
from mc_recorder.render_contract import FULL_CLIENT_PRESENTATION_CONTRACT


PLAYER = "00000000-0000-4000-8000-000000000001"
ACTIVE = "00000000-0000-4000-8000-000000000002"
ENDED = "00000000-0000-4000-8000-000000000003"


class DashboardServiceTest(unittest.TestCase):
    def test_connection_rows_are_groupable_and_only_terminal_rows_generate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = load_config(initialize(root / "recorder.toml", accept_eula=True))
            session = "20260721T000000.000Z-deadbeef"
            episode = config.paths.captures / session
            (episode / "epochs" / "epoch-000000").mkdir(parents=True)
            (episode / "manifest.json").write_text(json.dumps({"session_id": session}), encoding="utf-8")
            control = config.paths.runtime / "control"
            control.mkdir(parents=True)
            (control / "status.json").write_text(
                json.dumps(
                    {
                        "session_id": session,
                        "state": "recording",
                        "heartbeat_unix_ms": int(time.time() * 1000),
                    }
                ),
                encoding="utf-8",
            )
            (control / "connections.json").write_text(
                json.dumps(
                    {
                        "session_id": session,
                        "connections": [
                            {
                                "player_uuid": PLAYER,
                                "player_name": "Player",
                                "connection_id": ACTIVE,
                                "start_server_tick": 1,
                                "start_sequence": 2,
                            },
                            {
                                "player_uuid": PLAYER,
                                "player_name": "Player",
                                "connection_id": ENDED,
                                "start_server_tick": 5,
                                "end_server_tick": 10,
                                "end_sequence": 99,
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            service = DashboardService(config)
            try:
                rows = service.recordings()
                self.assertEqual(["recording", "waiting_for_seal"], [row["state"] for row in rows])
                active = next(row for row in rows if row["connection_id"] == ACTIVE)
                ended = next(row for row in rows if row["connection_id"] == ENDED)
                self.assertFalse(active["can_generate"])
                self.assertTrue(ended["can_generate"])
                with self.assertRaisesRegex(RecorderError, "active connections"):
                    service.generate_job(active["id"])
            finally:
                service.close()

    def test_recording_row_exposes_rgb_presentation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = load_config(initialize(root / "recorder.toml", accept_eula=True))
            session = "20260721T000000.000Z-deadbeef"
            control = config.paths.runtime / "control"
            control.mkdir(parents=True)
            (control / "connections.json").write_text(
                json.dumps(
                    {
                        "session_id": session,
                        "connections": [
                            {
                                "player_uuid": PLAYER,
                                "player_name": "Player",
                                "connection_id": ENDED,
                                "join_server_tick": 5,
                                "end_server_tick": 10,
                                "end_sequence": 99,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            service = DashboardService(config)
            metadata = mock.Mock(
                sample_count=6,
                rgb_samples=6,
                rgb_presentation="full_client",
                first_tick=5,
                last_tick=10,
            )
            try:
                with (
                    mock.patch.object(
                        service, "_dataset_match", return_value=("matching", None)
                    ),
                    mock.patch.object(
                        service.dataset_viewer,
                        "get_dataset_metadata",
                        return_value=metadata,
                    ),
                ):
                    row = service.recordings()[0]
                self.assertTrue(row["rgb_complete"])
                self.assertTrue(row["rgb_coverage_complete"])
                self.assertEqual("full_client", row["rgb_presentation"])
                self.assertFalse(row["can_replace_legacy_rgb"])
                with mock.patch.object(service, "recordings", return_value=[row]):
                    with self.assertRaisesRegex(RecorderError, "complete RGB coverage"):
                        service.create_render_job(row["id"])
                    with self.assertRaisesRegex(
                        RecorderError, "only allowed for fully covered legacy GUI RGB"
                    ):
                        service.create_render_job(
                            row["id"], replace_legacy_rgb=True
                        )
            finally:
                service.close()

    def test_fully_covered_legacy_gui_rgb_is_explicitly_replaceable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = load_config(initialize(root / "recorder.toml", accept_eula=True))
            session = "20260721T000000.000Z-deadbeef"
            control = config.paths.runtime / "control"
            control.mkdir(parents=True)
            (control / "connections.json").write_text(
                json.dumps(
                    {
                        "session_id": session,
                        "connections": [
                            {
                                "player_uuid": PLAYER,
                                "player_name": "Player",
                                "connection_id": ENDED,
                                "join_server_tick": 5,
                                "end_server_tick": 10,
                                "end_sequence": 99,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            service = DashboardService(config)
            metadata = mock.Mock(
                sample_count=6,
                rgb_samples=6,
                rgb_presentation="legacy_gui_unsynchronized",
                first_tick=5,
                last_tick=10,
            )
            completed = {"state": "complete"}
            try:
                with (
                    mock.patch.object(
                        service, "_dataset_match", return_value=("matching", None)
                    ),
                    mock.patch.object(
                        service.dataset_viewer,
                        "get_dataset_metadata",
                        return_value=metadata,
                    ),
                    mock.patch.object(
                        service.render_queue,
                        "latest_for_recording",
                        return_value=completed,
                    ),
                ):
                    row = service.recordings()[0]
                self.assertTrue(row["rgb_coverage_complete"])
                self.assertFalse(row["rgb_complete"])
                self.assertTrue(row["can_replace_legacy_rgb"])
                self.assertTrue(row["can_render"])

                with mock.patch.object(service, "recordings", return_value=[row]):
                    with self.assertRaisesRegex(
                        RecorderError, "replace_legacy_rgb=true"
                    ):
                        service.create_render_job(row["id"])
                    with self.assertRaisesRegex(RecorderError, "full-client"):
                        service.create_render_job(
                            row["id"], no_gui=True, replace_legacy_rgb=True
                        )
                    job = service.create_render_job(
                        row["id"], replace_legacy_rgb=True
                    )
                self.assertEqual("queued", job["state"])
                self.assertEqual(
                    FULL_CLIENT_PRESENTATION_CONTRACT,
                    job["payload"]["render"]["presentation_contract"],
                )
            finally:
                service.close()

    def test_preserves_terminal_heartbeat_state_after_it_becomes_stale(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = load_config(initialize(root / "recorder.toml", accept_eula=True))
            control = config.paths.runtime / "control"
            control.mkdir(parents=True)
            (control / "status.json").write_text(
                json.dumps(
                    {
                        "session_id": "20260721T000000.000Z-deadbeef",
                        "state": "stopped",
                        "updated_at_unix_ms": 1,
                    }
                ),
                encoding="utf-8",
            )
            service = DashboardService(config)
            try:
                status = service.capture_status()
                self.assertEqual("stopped", status["state"])
                self.assertFalse(status["fresh"])
            finally:
                service.close()

    def test_only_one_dashboard_process_can_recover_and_run_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = load_config(initialize(root / "recorder.toml", accept_eula=True))
            first = DashboardService(config)
            try:
                with self.assertRaisesRegex(RecorderError, "another dashboard process"):
                    DashboardService(config)
            finally:
                first.close()
            replacement = DashboardService(config)
            replacement.close()

    def test_conflicting_output_is_failed_but_matching_verified_output_is_reused(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = load_config(initialize(root / "recorder.toml", accept_eula=True))
            session = "20260721T000000.000Z-deadbeef"
            control = config.paths.runtime / "control"
            control.mkdir(parents=True)
            (control / "connections.json").write_text(
                json.dumps(
                    {
                        "session_id": session,
                        "connections": [
                            {
                                "player_uuid": PLAYER,
                                "player_name": "Player",
                                "connection_id": ENDED,
                                "join_server_tick": 5,
                                "join_sequence": 2,
                                "end_server_tick": 10,
                                "end_sequence": 99,
                                "terminal_reason": "disconnect",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            output = config.paths.exports / f"{session}-{PLAYER}-{ENDED}.dataset"
            output.mkdir(parents=True)
            (output / "manifest.json").write_text(
                json.dumps(
                    {
                        "owner": "mc-recorder",
                        "selection": {"connections": [ENDED]},
                    }
                ),
                encoding="utf-8",
            )

            service = DashboardService(config)
            try:
                row = self._wait_for_recording_state(service, "failed")
                self.assertEqual("failed", row["state"])
                self.assertFalse(row["can_generate"])
                self.assertIn("viewer-valid", row["error"])
            finally:
                service.close()

    def test_hash_enveloped_malformed_dataset_is_not_complete_or_reused(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = load_config(initialize(root / "recorder.toml", accept_eula=True))
            session = "20260721T000000.000Z-deadbeef"
            control = config.paths.runtime / "control"
            control.mkdir(parents=True)
            (control / "connections.json").write_text(
                json.dumps(
                    {
                        "session_id": session,
                        "connections": [
                            {
                                "player_uuid": PLAYER,
                                "player_name": "Player",
                                "connection_id": ENDED,
                                "join_server_tick": 5,
                                "join_sequence": 2,
                                "end_server_tick": 10,
                                "end_sequence": 99,
                                "terminal_reason": "disconnect",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            output = config.paths.exports / f"{session}-{PLAYER}-{ENDED}.dataset"
            output.mkdir(parents=True)
            streams = {
                "samples.jsonl": b"not-json\n",
                "states.jsonl": b"",
                "actions.jsonl": b"",
                "modalities.jsonl": b"",
            }
            for name, data in streams.items():
                (output / name).write_bytes(data)
            (output / "manifest.json").write_text(
                json.dumps(
                    {
                        "schema_version": 2,
                        "owner": "mc-recorder",
                        "format": "mc-recorder-jsonl-v2",
                        "session_id": session,
                        "selection": {
                            "players": [PLAYER],
                            "connections": [ENDED],
                            "from_tick": 5,
                            "to_tick": 10,
                        },
                        "files": {
                            name: {
                                "size_bytes": len(data),
                                "sha256": hashlib.sha256(data).hexdigest(),
                            }
                            for name, data in streams.items()
                        },
                    }
                ),
                encoding="utf-8",
            )

            service = DashboardService(config)
            try:
                row = self._wait_for_recording_state(service, "failed")
                self.assertEqual("failed", row["state"])
                self.assertFalse(row["can_generate"])
                self.assertIn("viewer-valid", row["error"])
                with self.assertRaisesRegex(RecorderError, "not ready"):
                    service.generate_job(row["id"])
            finally:
                service.close()

            for child in output.iterdir():
                child.unlink()
            streams = {
                name: b""
                for name in (
                    "samples.jsonl",
                    "states.jsonl",
                    "actions.jsonl",
                    "modalities.jsonl",
                )
            }
            for name, data in streams.items():
                (output / name).write_bytes(data)
            (output / "manifest.json").write_text(
                json.dumps(
                    {
                        "schema_version": 2,
                        "owner": "mc-recorder",
                        "format": "mc-recorder-jsonl-v2",
                        "session_id": session,
                        "selection": {
                            "players": [PLAYER],
                            "connections": [ENDED],
                            "from_tick": 5,
                            "to_tick": 10,
                        },
                        "files": {
                            name: {
                                "size_bytes": len(data),
                                "sha256": hashlib.sha256(data).hexdigest(),
                            }
                            for name, data in streams.items()
                        },
                    }
                ),
                encoding="utf-8",
            )
            service = DashboardService(config)
            try:
                row = self._wait_for_recording_state(service, "complete")
                self.assertEqual("complete", row["state"])
                self.assertRegex(row["dataset_id"], r"^[0-9a-f]{32}$")
                job = service.generate_job(row["id"])
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline:
                    completed = service.jobs.store.get(job["id"])
                    if completed["state"] not in {"queued", "running"}:
                        break
                    time.sleep(0.01)
                self.assertEqual("complete", completed["state"])
                self.assertTrue(completed["result"]["reused"])
            finally:
                service.close()

    def test_explicit_capture_failure_gates_rows_and_generation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = load_config(initialize(root / "recorder.toml", accept_eula=True))
            session = "20260721T000000.000Z-deadbeef"
            control = config.paths.runtime / "control"
            control.mkdir(parents=True)
            (control / "status.json").write_text(
                json.dumps(
                    {
                        "session_id": session,
                        "state": "failed",
                        "updated_at_unix_ms": int(time.time() * 1000),
                        "failure_reason": "writer disk failure",
                    }
                ),
                encoding="utf-8",
            )
            (control / "connections.json").write_text(
                json.dumps(
                    {
                        "session_id": session,
                        "connections": [
                            {
                                "player_uuid": PLAYER,
                                "player_name": "Player",
                                "connection_id": ACTIVE,
                                "join_server_tick": 1,
                                "join_sequence": 2,
                            },
                            {
                                "player_uuid": PLAYER,
                                "player_name": "Player",
                                "connection_id": ENDED,
                                "join_server_tick": 5,
                                "join_sequence": 10,
                                "end_server_tick": 10,
                                "end_sequence": 99,
                                "terminal_reason": "disconnect",
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )

            service = DashboardService(config)
            try:
                rows = service.recordings()
                self.assertEqual({"failed"}, {row["state"] for row in rows})
                self.assertTrue(all(not row["can_generate"] for row in rows))
                self.assertTrue(all("disk failure" in row["error"] for row in rows))
                ended = next(row for row in rows if row["connection_id"] == ENDED)
                with self.assertRaisesRegex(RecorderError, "not ready"):
                    service.generate_job(ended["id"])
            finally:
                service.close()

    def test_first_dataset_discovery_does_not_block_recording_status(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = load_config(initialize(root / "recorder.toml", accept_eula=True))
            session = "20260721T000000.000Z-deadbeef"
            control = config.paths.runtime / "control"
            control.mkdir(parents=True)
            (control / "connections.json").write_text(
                json.dumps(
                    {
                        "session_id": session,
                        "connections": [
                            {
                                "player_uuid": PLAYER,
                                "player_name": "Player",
                                "connection_id": ENDED,
                                "join_server_tick": 5,
                                "join_sequence": 2,
                                "end_server_tick": 10,
                                "end_sequence": 99,
                                "terminal_reason": "disconnect",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            output = config.paths.exports / f"{session}-{PLAYER}-{ENDED}.dataset"
            output.mkdir(parents=True)
            catalog_started = threading.Event()
            release_catalog = threading.Event()

            def blocked_catalog(_viewer):
                catalog_started.set()
                release_catalog.wait(2)
                return DatasetCatalogResult((), ())

            with mock.patch(
                "mc_recorder.dataset_viewer.DatasetViewer.catalog",
                autospec=True,
                side_effect=blocked_catalog,
            ):
                service = DashboardService(config)
                try:
                    self.assertTrue(catalog_started.wait(1))
                    started = time.monotonic()
                    row = service.recordings()[0]
                    elapsed = time.monotonic() - started
                    self.assertLess(elapsed, 0.5)
                    self.assertEqual("generating", row["state"])
                    self.assertFalse(row["can_generate"])
                finally:
                    release_catalog.set()
                    service.close()

    def test_failed_seal_job_does_not_make_an_interrupted_recording_retriable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = load_config(initialize(root / "recorder.toml", accept_eula=True))
            session = "20260721T000000.000Z-deadbeef"
            control = config.paths.runtime / "control"
            control.mkdir(parents=True)
            (control / "connections.json").write_text(
                json.dumps(
                    {
                        "session_id": session,
                        "connections": [
                            {
                                "player_uuid": PLAYER,
                                "player_name": "Player",
                                "connection_id": ENDED,
                                "join_server_tick": 5,
                                "join_sequence": 2,
                                "end_server_tick": 10,
                                "end_sequence": 99,
                                "terminal_reason": "disconnect",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            service = DashboardService(config)
            try:
                initial = service.recordings()[0]
                self.assertEqual("interrupted", initial["state"])
                job = service.jobs.store.create(
                    "generate_dataset",
                    {"recording_id": initial["id"]},
                )
                service.jobs.store.fail(job["id"], "seal timed out")

                row = service.recordings()[0]
                self.assertEqual("interrupted", row["state"])
                self.assertFalse(row["can_generate"])
                with self.assertRaisesRegex(RecorderError, "not ready"):
                    service.generate_job(row["id"])
            finally:
                service.close()

    def test_render_jobs_are_connection_scoped_bounded_and_lifecycle_independent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = load_config(initialize(root / "recorder.toml", accept_eula=True))
            service = DashboardService(config)
            recording_id = "a" * 24
            row = {
                "id": recording_id,
                "state": "complete",
                "session_id": "20260721T000000.000Z-deadbeef",
                "player_uuid": PLAYER,
                "connection_id": ENDED,
                "dataset_id": "b" * 32,
                "start_tick": 5,
                "end_tick": 10,
                "sample_start_tick": 6,
                "sample_end_tick": 9,
                "rgb_complete": False,
            }
            try:
                # A render can wait offline without consuming the serialized
                # host lifecycle/generation operation slot.
                service.jobs.store.create("server_start", {})
                with mock.patch.object(service, "recordings", return_value=[row]):
                    job = service.create_render_job(
                        recording_id, width=1280, height=720, fps=20
                    )
                    self.assertEqual("queued", job["state"])
                    self.assertEqual(ENDED, job["payload"]["connection_id"])
                    self.assertEqual((6, 9), (job["payload"]["start_tick"], job["payload"]["end_tick"]))
                    self.assertEqual(
                        (5, 10),
                        (
                            job["payload"]["selection_start_tick"],
                            job["payload"]["selection_end_tick"],
                        ),
                    )
                    self.assertEqual(
                        {
                            "width": 1280,
                            "height": 720,
                            "fps": 20,
                            "no_gui": False,
                            "presentation_contract": FULL_CLIENT_PRESENTATION_CONTRACT,
                        },
                        job["payload"]["render"],
                    )
                    with self.assertRaisesRegex(RecorderError, "width"):
                        service.create_render_job(recording_id, width=100)
                    with self.assertRaisesRegex(RecorderError, "fps must be 20"):
                        service.create_render_job(recording_id, fps=30)
                    with self.assertRaisesRegex(RecorderError, "width"):
                        service.create_render_job(recording_id, width=True)
                    with self.assertRaisesRegex(RecorderError, "no_gui must be a boolean"):
                        service.create_render_job(recording_id, no_gui=0)  # type: ignore[arg-type]
                    with self.assertRaisesRegex(RecorderError, "replace_legacy_rgb"):
                        service.create_render_job(
                            recording_id,
                            replace_legacy_rgb=True,
                        )
                    with self.assertRaisesRegex(RecorderError, "replace_legacy_rgb"):
                        service.create_render_job(
                            recording_id,
                            replace_legacy_rgb=1,  # type: ignore[arg-type]
                        )
                    with self.assertRaisesRegex(RecorderError, "retried while queued"):
                        service.retry_render_job(job["id"])
                    service.cancel_render_job(job["id"])
                    retry = service.retry_render_job(job["id"])
                    self.assertEqual(job["id"], retry["retry_of"])
                    self.assertNotEqual(job["id"], retry["id"])
                    self.assertEqual((6, 9), (retry["payload"]["start_tick"], retry["payload"]["end_tick"]))
                    self.assertFalse(retry["payload"]["render"]["no_gui"])
                    self.assertEqual(
                        FULL_CLIENT_PRESENTATION_CONTRACT,
                        retry["payload"]["render"]["presentation_contract"],
                    )

                    legacy_payload = json.loads(json.dumps(job["payload"]))
                    legacy_payload["render"].pop("no_gui")
                    legacy = service.render_queue.create(legacy_payload)
                    service.cancel_render_job(legacy["id"])
                    legacy_retry = service.retry_render_job(legacy["id"])
                    self.assertTrue(legacy_retry["payload"]["render"]["no_gui"])
                    service.cancel_render_job(legacy_retry["id"])
                missing_row = {**row, "dataset_id": "c" * 32}
                service.cancel_render_job(retry["id"])
                with mock.patch.object(service, "recordings", return_value=[missing_row]):
                    with self.assertRaisesRegex(RecorderError, "no longer available"):
                        service.retry_render_job(retry["id"])
            finally:
                service.close()

    def _wait_for_recording_state(
        self,
        service: DashboardService,
        expected: str,
    ) -> dict[str, object]:
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            row = service.recordings()[0]
            if row["state"] == expected:
                return row
            time.sleep(0.01)
        self.fail(f"recording did not reach {expected}: {service.recordings()[0]}")


if __name__ == "__main__":
    unittest.main()
