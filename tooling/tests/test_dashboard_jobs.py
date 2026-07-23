from __future__ import annotations

import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from mc_recorder.errors import RecorderError
from mc_recorder.serving.dashboard.jobs import JobManager, JobStore


class DashboardJobTest(unittest.TestCase):
    def test_persists_success_and_serializes_operations(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = JobStore(Path(temporary) / "jobs.sqlite3")
            manager = JobManager(store)
            gate = __import__("threading").Event()
            try:
                job = manager.submit("first", {"value": 1}, lambda: (gate.wait(2), {"ok": True})[1])
                with self.assertRaisesRegex(RecorderError, "another dashboard operation"):
                    manager.submit("second", {}, lambda: {})
                gate.set()
                self._wait_for(store, job["id"], "complete")
                self.assertEqual({"ok": True}, store.get(job["id"])["result"])
            finally:
                manager.close()

    def test_persists_failure_and_marks_stale_jobs_interrupted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "jobs.sqlite3"
            store = JobStore(path)
            manager = JobManager(store)
            try:
                job = manager.submit(
                    "broken",
                    {},
                    lambda: (_ for _ in ()).throw(ValueError("boom" + "x" * 3000)),
                )
                self._wait_for(store, job["id"], "failed")
                error = store.get(job["id"])["error"]
                self.assertIn("boom", error)
                self.assertEqual(2048, len(error))
            finally:
                manager.close()

            stale = store.create("stale", {})
            self.assertEqual("queued", store.get(stale["id"])["state"])
            restarted = JobStore(path)
            self.assertEqual("interrupted", restarted.get(stale["id"])["state"])

    def test_close_waits_for_the_active_worker_to_exit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = JobStore(Path(temporary) / "jobs.sqlite3")
            manager = JobManager(store)
            started = threading.Event()
            release = threading.Event()

            def work() -> dict[str, bool]:
                started.set()
                release.wait(2)
                return {"ok": True}

            job = manager.submit("long", {}, work)
            self.assertTrue(started.wait(1))
            closer = threading.Thread(target=manager.close)
            closer.start()
            try:
                time.sleep(0.05)
                self.assertTrue(closer.is_alive())
            finally:
                release.set()
                closer.join(timeout=2)
            self.assertFalse(closer.is_alive())
            self.assertEqual("complete", store.get(job["id"])["state"])

    def _wait_for(self, store: JobStore, job_id: str, expected: str) -> None:
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if store.get(job_id)["state"] == expected:
                return
            time.sleep(0.01)
        self.fail(f"job did not become {expected}: {store.get(job_id)}")


if __name__ == "__main__":
    unittest.main()
