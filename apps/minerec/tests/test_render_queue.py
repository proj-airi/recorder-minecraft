from __future__ import annotations

import sqlite3
import sys
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from mc_recorder.errors import RecorderError
from mc_recorder.protocol.render.queue import RenderQueueStore

RECORDING_ID = "a" * 24
WORKER_ONE = "11111111-1111-4111-8111-111111111111"
WORKER_TWO = "22222222-2222-4222-8222-222222222222"


def _payload() -> dict[str, object]:
    return {
        "recording_id": RECORDING_ID,
        "session_id": "20260721T000000.000Z-deadbeef",
        "player_uuid": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "connection_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        "dataset_id": "c" * 32,
        "start_tick": 10,
        "end_tick": 40,
        "render": {"width": 640, "height": 360, "fps": 20},
    }


class RenderQueueStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "render.sqlite3"
        self.store = RenderQueueStore(self.path)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_queued_jobs_survive_restart_and_matching_clicks_are_idempotent(self) -> None:
        job = self.store.create(_payload())
        duplicate = self.store.create(_payload())
        self.assertEqual(job["id"], duplicate["id"])
        self.assertEqual("queued", job["state"])

        reopened = RenderQueueStore(self.path)
        restored = reopened.get(job["id"])
        self.assertEqual("queued", restored["state"])
        self.assertEqual(_payload(), restored["payload"])
        self.assertIsNone(restored["active_attempt"])

    def test_existing_database_is_migrated_with_immediate_queue_eligibility(self) -> None:
        legacy_path = Path(self.temporary.name) / "legacy-render.sqlite3"
        with closing(sqlite3.connect(legacy_path)) as connection:
            connection.execute(
                """
                CREATE TABLE render_jobs (
                    id TEXT PRIMARY KEY,
                    fingerprint TEXT NOT NULL,
                    recording_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    state TEXT NOT NULL,
                    progress_json TEXT,
                    result_json TEXT,
                    error TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    started_at REAL,
                    completed_at REAL,
                    active_attempt_id TEXT,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    retry_of TEXT
                )
                """
            )
            connection.commit()

        migrated = RenderQueueStore(legacy_path)
        with closing(sqlite3.connect(legacy_path)) as connection:
            columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(render_jobs)")}
        self.assertIn("eligible_at", columns)

        job = migrated.create(_payload())
        migrated.register_worker("worker", worker_id=WORKER_ONE)
        claimed = migrated.claim(WORKER_ONE)
        assert claimed is not None
        self.assertEqual(job["id"], claimed["job"]["id"])

    def test_dashboard_restart_preserves_a_live_worker_lease(self) -> None:
        job = self.store.create(_payload())
        self.store.register_worker("MacBook", worker_id=WORKER_ONE)
        claimed = self.store.claim(WORKER_ONE)
        assert claimed is not None

        reopened = RenderQueueStore(self.path)
        restored = reopened.get(job["id"])
        self.assertEqual("downloading", restored["state"])
        self.assertEqual(claimed["attempt"]["id"], restored["active_attempt"]["id"])
        self.assertNotIn("lease_token", restored["active_attempt"])

    def test_worker_lease_progress_upload_and_server_completion(self) -> None:
        job = self.store.create(_payload())
        worker = self.store.register_worker(
            "MacBook",
            worker_id=WORKER_ONE,
            capabilities={"resolutions": [[640, 360]]},
        )
        self.assertEqual("online", worker["state"])

        claimed = self.store.claim(WORKER_ONE, job_id=job["id"])
        assert claimed is not None
        attempt = claimed["attempt"]
        self.assertEqual("downloading", claimed["job"]["state"])
        self.assertNotIn("lease_token", claimed["job"]["active_attempt"])
        self.assertEqual("busy", self.store.workers()[0]["state"])

        rendering = self.store.heartbeat(
            WORKER_ONE,
            attempt["id"],
            attempt["lease_token"],
            phase="rendering",
            current=12,
            total=31,
            message="frame 12",
        )
        self.assertEqual("rendering", rendering["state"])
        self.assertEqual(12, rendering["progress"]["current"])

        uploaded = self.store.mark_uploaded(
            WORKER_ONE,
            attempt["id"],
            attempt["lease_token"],
            {"upload_id": "opaque-upload", "bundle_sha256": "d" * 64},
        )
        self.assertEqual("verifying", uploaded["state"])
        self.assertEqual("online", self.store.workers()[0]["state"])
        attaching = self.store.set_server_phase(uploaded["id"], "attaching")
        self.assertEqual("attaching", attaching["state"])
        complete = self.store.complete(attaching["id"], {"dataset_id": _payload()["dataset_id"]})
        self.assertEqual("complete", complete["state"])

        self.assertEqual(
            complete["id"],
            self.store.create(_payload())["id"],
            "a repeated dashboard click must reuse an already completed matching render",
        )

    def test_expired_lease_is_requeued_and_old_worker_is_fenced(self) -> None:
        job = self.store.create(_payload())
        self.store.register_worker("first", worker_id=WORKER_ONE)
        self.store.register_worker("second", worker_id=WORKER_TWO)
        claimed = self.store.claim(WORKER_ONE, lease_seconds=10)
        assert claimed is not None
        future = time.time() + 20

        with mock.patch("mc_recorder.protocol.render.queue.time.time", return_value=future):
            self.assertEqual("queued", self.store.get(job["id"])["state"])
            reclaimed = self.store.claim(WORKER_TWO, lease_seconds=10)
            assert reclaimed is not None
            self.assertEqual(2, reclaimed["attempt"]["generation"])
            with self.assertRaisesRegex(RecorderError, "not owned"):
                self.store.heartbeat(
                    WORKER_ONE,
                    claimed["attempt"]["id"],
                    claimed["attempt"]["lease_token"],
                    phase="rendering",
                )

    def test_expired_scoped_lease_clears_an_earlier_defer_cooldown(self) -> None:
        base_time = 2_000_000.0
        with mock.patch("mc_recorder.protocol.render.queue.time.time", return_value=base_time):
            job = self.store.create(_payload())
            self.store.register_worker("first", worker_id=WORKER_ONE)
            self.store.register_worker("second", worker_id=WORKER_TWO)
            initial = self.store.claim(WORKER_ONE, job_id=job["id"], lease_seconds=10)
            assert initial is not None
            initial_attempt = initial["attempt"]
            self.store.defer_attempt(
                WORKER_ONE,
                initial_attempt["id"],
                initial_attempt["lease_token"],
                "replay is still being saved",
            )

        with mock.patch("mc_recorder.protocol.render.queue.time.time", return_value=base_time + 1):
            forced = self.store.claim(WORKER_ONE, job_id=job["id"], lease_seconds=10)
        assert forced is not None
        with mock.patch("mc_recorder.protocol.render.queue.time.time", return_value=base_time + 12):
            reclaimed = self.store.claim(WORKER_TWO, lease_seconds=10)
        assert reclaimed is not None
        self.assertEqual(job["id"], reclaimed["job"]["id"])
        self.assertEqual(3, reclaimed["attempt"]["generation"])

    def test_not_ready_input_defers_without_failing_the_job(self) -> None:
        job = self.store.create(_payload())
        self.store.register_worker("first", worker_id=WORKER_ONE)
        claimed = self.store.claim(WORKER_ONE, job_id=job["id"])
        assert claimed is not None
        attempt = claimed["attempt"]

        with self.assertRaisesRegex(RecorderError, "defer cooldown"):
            self.store.defer_attempt(
                WORKER_ONE,
                attempt["id"],
                attempt["lease_token"],
                "replay is still being saved",
                cooldown_seconds=301,
            )

        deferred = self.store.defer_attempt(
            WORKER_ONE,
            attempt["id"],
            attempt["lease_token"],
            "replay is still being saved",
        )

        self.assertEqual("queued", deferred["state"])
        self.assertIsNone(deferred["error"])
        self.assertIsNone(deferred["active_attempt"])
        self.assertEqual("online", self.store.workers()[0]["state"])
        reclaimed = self.store.claim(WORKER_ONE, job_id=job["id"])
        assert reclaimed is not None
        self.assertEqual(2, reclaimed["attempt"]["generation"])
        with self.assertRaisesRegex(RecorderError, "not owned"):
            self.store.heartbeat(
                WORKER_ONE,
                attempt["id"],
                attempt["lease_token"],
                phase="downloading",
            )

    def test_deferred_oldest_job_does_not_block_later_ready_work(self) -> None:
        base_time = 1_000_000.0
        later_payload = _payload()
        later_payload["dataset_id"] = "d" * 32
        with mock.patch("mc_recorder.protocol.render.queue.time.time", return_value=base_time):
            oldest = self.store.create(_payload())
        with mock.patch("mc_recorder.protocol.render.queue.time.time", return_value=base_time + 1):
            later = self.store.create(later_payload)
            self.store.register_worker("worker", worker_id=WORKER_ONE)
        with mock.patch("mc_recorder.protocol.render.queue.time.time", return_value=base_time + 2):
            claimed_oldest = self.store.claim(WORKER_ONE, job_id=oldest["id"])
            assert claimed_oldest is not None
            attempt = claimed_oldest["attempt"]
            self.store.defer_attempt(
                WORKER_ONE,
                attempt["id"],
                attempt["lease_token"],
                "replay is still being saved",
            )

        with mock.patch("mc_recorder.protocol.render.queue.time.time", return_value=base_time + 3):
            claimed_later = self.store.claim(WORKER_ONE)
        assert claimed_later is not None
        self.assertEqual(later["id"], claimed_later["job"]["id"])

        with mock.patch("mc_recorder.protocol.render.queue.time.time", return_value=base_time + 4):
            later_attempt = claimed_later["attempt"]
            self.store.fail_attempt(
                WORKER_ONE,
                later_attempt["id"],
                later_attempt["lease_token"],
                "test cleanup",
            )
        with mock.patch("mc_recorder.protocol.render.queue.time.time", return_value=base_time + 33):
            reclaimed_oldest = self.store.claim(WORKER_ONE)
        assert reclaimed_oldest is not None
        self.assertEqual(oldest["id"], reclaimed_oldest["job"]["id"])

    def test_cancel_and_retry_create_a_new_provenance_row(self) -> None:
        original = self.store.create(_payload())
        canceled = self.store.cancel(original["id"])
        self.assertEqual("canceled", canceled["state"])
        self.assertEqual(canceled["id"], self.store.cancel(canceled["id"])["id"])

        retried = self.store.retry(canceled["id"])
        self.assertNotEqual(canceled["id"], retried["id"])
        self.assertEqual(canceled["id"], retried["retry_of"])
        self.assertEqual("queued", retried["state"])
        self.store.register_worker("worker", worker_id=WORKER_ONE)
        claimed = self.store.claim(WORKER_ONE)
        assert claimed is not None
        self.assertEqual(retried["id"], claimed["job"]["id"])
        with self.assertRaisesRegex(RecorderError, "cannot be retried"):
            self.store.retry(claimed["job"]["id"])

    def test_cancel_is_fenced_after_server_verification_begins(self) -> None:
        job = self.store.create(_payload())
        self.store.register_worker("worker", worker_id=WORKER_ONE)
        claimed = self.store.claim(WORKER_ONE, job_id=job["id"])
        assert claimed is not None
        attempt = claimed["attempt"]
        verifying = self.store.mark_uploaded(
            WORKER_ONE,
            attempt["id"],
            attempt["lease_token"],
            {"upload_id": "verified-upload"},
        )
        self.assertEqual("verifying", verifying["state"])

        with self.assertRaisesRegex(RecorderError, "server-side verifying"):
            self.store.cancel(job["id"])
        self.assertEqual("verifying", self.store.get(job["id"])["state"])

    def test_worker_updates_are_bounded_and_monotonic(self) -> None:
        self.store.create(_payload())
        self.store.register_worker("worker", worker_id=WORKER_ONE)
        claimed = self.store.claim(WORKER_ONE)
        assert claimed is not None
        attempt = claimed["attempt"]
        self.store.heartbeat(
            WORKER_ONE,
            attempt["id"],
            attempt["lease_token"],
            phase="rendering",
        )
        with self.assertRaisesRegex(RecorderError, "backward"):
            self.store.heartbeat(
                WORKER_ONE,
                attempt["id"],
                attempt["lease_token"],
                phase="downloading",
            )
        with self.assertRaisesRegex(RecorderError, "0 <= current <= total"):
            self.store.heartbeat(
                WORKER_ONE,
                attempt["id"],
                attempt["lease_token"],
                phase="rendering",
                current=2,
                total=1,
            )
        with self.assertRaisesRegex(RecorderError, "owned"):
            self.store.mark_uploaded(
                WORKER_ONE,
                attempt["id"],
                "wrong-token",
                {"upload_id": "ignored"},
            )

        invalid_payload = _payload()
        invalid_payload["non_finite"] = float("nan")
        with self.assertRaisesRegex(RecorderError, "not JSON serializable"):
            self.store.create(invalid_payload)

    def test_worker_failure_does_not_block_lifecycle_queue(self) -> None:
        job = self.store.create(_payload())
        self.store.register_worker("worker", worker_id=WORKER_ONE)
        claimed = self.store.claim(WORKER_ONE)
        assert claimed is not None
        failed = self.store.fail_attempt(
            WORKER_ONE,
            claimed["attempt"]["id"],
            claimed["attempt"]["lease_token"],
            "renderer crashed",
        )
        self.assertEqual(job["id"], failed["id"])
        self.assertEqual("failed", failed["state"])
        self.assertEqual("renderer crashed", failed["error"])
        self.assertIsNone(self.store.claim(WORKER_ONE))


if __name__ == "__main__":
    unittest.main()
