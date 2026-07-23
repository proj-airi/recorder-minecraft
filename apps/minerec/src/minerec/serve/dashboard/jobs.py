from __future__ import annotations

import json
import queue
import sqlite3
import threading
import traceback
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from minerec.errors import RecorderError

JobFunction = Callable[[], dict[str, Any]]
MAX_JOB_ERROR_CHARS = 2048


def _now() -> str:
    return datetime.now(UTC).isoformat()


class JobStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._lock = threading.Lock()
        with self._session() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS dashboard_jobs (
                    id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    state TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    result_json TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT
                )
                """
            )
            connection.execute(
                """
                UPDATE dashboard_jobs
                   SET state = 'interrupted',
                       error = 'dashboard restarted while the job was running',
                       completed_at = ?
                 WHERE state IN ('queued', 'running')
                """,
                (_now(),),
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    @contextmanager
    def _session(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def create(self, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        job_id = str(uuid.uuid4())
        created_at = _now()
        with self._lock, self._session() as connection:
            connection.execute(
                """
                INSERT INTO dashboard_jobs(id, kind, state, payload_json, created_at)
                VALUES (?, ?, 'queued', ?, ?)
                """,
                (job_id, kind, json.dumps(payload, sort_keys=True), created_at),
            )
        return self.get(job_id)

    def get(self, job_id: str) -> dict[str, Any]:
        with self._lock, self._session() as connection:
            row = connection.execute("SELECT * FROM dashboard_jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(job_id)
        return self._row(row)

    def active(self) -> dict[str, Any] | None:
        with self._lock, self._session() as connection:
            row = connection.execute(
                """
                SELECT * FROM dashboard_jobs
                 WHERE state IN ('queued', 'running')
                 ORDER BY created_at LIMIT 1
                """
            ).fetchone()
        return self._row(row) if row is not None else None

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        bounded = max(1, min(limit, 100))
        with self._lock, self._session() as connection:
            rows = connection.execute("SELECT * FROM dashboard_jobs ORDER BY created_at DESC LIMIT ?", (bounded,)).fetchall()
        return [self._row(row) for row in rows]

    def running(self, job_id: str) -> None:
        self._update(job_id, state="running", started_at=_now())

    def complete(self, job_id: str, result: dict[str, Any]) -> None:
        self._update(
            job_id,
            state="complete",
            result_json=json.dumps(result, sort_keys=True),
            completed_at=_now(),
        )

    def fail(self, job_id: str, error: str) -> None:
        self._update(
            job_id,
            state="failed",
            error=error[:MAX_JOB_ERROR_CHARS],
            completed_at=_now(),
        )

    def _update(self, job_id: str, **values: object) -> None:
        assignments = ", ".join(f"{key} = ?" for key in values)
        with self._lock, self._session() as connection:
            connection.execute(
                f"UPDATE dashboard_jobs SET {assignments} WHERE id = ?",  # noqa: S608 - fixed keys
                (*values.values(), job_id),
            )

    @staticmethod
    def _row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "kind": row["kind"],
            "state": row["state"],
            "payload": json.loads(row["payload_json"]),
            "result": json.loads(row["result_json"]) if row["result_json"] else None,
            "error": row["error"],
            "created_at": row["created_at"],
            "started_at": row["started_at"],
            "completed_at": row["completed_at"],
        }


class JobManager:
    def __init__(self, store: JobStore) -> None:
        self.store = store
        self._queue: queue.Queue[tuple[str, JobFunction] | None] = queue.Queue()
        self._closed = False
        self._submit_lock = threading.Lock()
        self._worker = threading.Thread(target=self._run, name="mc-recorder-dashboard-jobs", daemon=True)
        self._worker.start()

    def submit(self, kind: str, payload: dict[str, Any], function: JobFunction) -> dict[str, Any]:
        with self._submit_lock:
            if self._closed:
                raise RecorderError("dashboard job worker is stopped")
            active = self.store.active()
            if active is not None:
                raise RecorderError(f"another dashboard operation is already {active['state']}: {active['kind']}")
            job = self.store.create(kind, payload)
            self._queue.put((job["id"], function))
            return job

    def close(self) -> None:
        with self._submit_lock:
            if self._closed:
                return
            self._closed = True
            self._queue.put(None)
        self._worker.join()

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                return
            job_id, function = item
            self.store.running(job_id)
            try:
                result = function()
            except Exception as exc:  # job boundary: persist actionable failure without killing HTTP
                message = f"{exc.__class__.__name__}: {exc}"
                if not str(exc):
                    message = traceback.format_exc(limit=1).strip()
                self.store.fail(job_id, message)
            else:
                self.store.complete(job_id, result)
