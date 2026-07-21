from __future__ import annotations

import hashlib
import hmac
import json
import math
import secrets
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator

from .errors import RecorderError


ACTIVE_JOB_STATES = frozenset(
    {"queued", "downloading", "rendering", "uploading", "verifying", "attaching"}
)
TERMINAL_JOB_STATES = frozenset({"complete", "partial", "failed", "canceled"})
WORKER_PHASES = ("downloading", "rendering", "uploading")
SERVER_PHASES = frozenset({"verifying", "attaching"})
MAX_STORED_JSON_BYTES = 1024 * 1024
MAX_ERROR_CHARS = 2048
MAX_PROGRESS_TOTAL = 2**63 - 1
DEFAULT_LEASE_SECONDS = 60
MIN_LEASE_SECONDS = 10
MAX_LEASE_SECONDS = 300
DEFAULT_DEFER_COOLDOWN_SECONDS = 30
MIN_DEFER_COOLDOWN_SECONDS = 1
MAX_DEFER_COOLDOWN_SECONDS = 300


def _now_iso(timestamp: float | None) -> str | None:
    if timestamp is None:
        return None
    return datetime.fromtimestamp(timestamp, UTC).isoformat()


def _uuid(value: object, label: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError) as exc:
        raise RecorderError(f"invalid {label}") from exc


def _json_object(value: object, label: str) -> tuple[str, dict[str, Any]]:
    if not isinstance(value, dict):
        raise RecorderError(f"{label} must be a JSON object")
    try:
        encoded = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError, RecursionError) as exc:
        raise RecorderError(f"{label} is not JSON serializable") from exc
    if len(encoded.encode("utf-8")) > MAX_STORED_JSON_BYTES:
        raise RecorderError(f"{label} is too large")
    return encoded, value


def _lease_seconds(value: object) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not MIN_LEASE_SECONDS <= value <= MAX_LEASE_SECONDS
    ):
        raise RecorderError(
            f"lease seconds must be between {MIN_LEASE_SECONDS} and {MAX_LEASE_SECONDS}"
        )
    return value


def _progress(
    phase: str,
    current: int | None,
    total: int | None,
    message: str | None,
) -> dict[str, Any]:
    if phase not in WORKER_PHASES:
        raise RecorderError(f"invalid worker render phase: {phase}")
    if (current is None) != (total is None):
        raise RecorderError("render progress current and total must be supplied together")
    if current is not None:
        if (
            not isinstance(current, int)
            or isinstance(current, bool)
            or not isinstance(total, int)
            or isinstance(total, bool)
            or current < 0
            or total < 0
            or current > total
            or total > MAX_PROGRESS_TOTAL
        ):
            raise RecorderError("render progress must satisfy 0 <= current <= total")
    if message is not None and (not isinstance(message, str) or len(message) > 256):
        raise RecorderError("render progress message must be at most 256 characters")
    return {
        "phase": phase,
        "current": current,
        "total": total,
        "message": message,
    }


class RenderQueueStore:
    """Persistent render work that survives dashboard and worker restarts.

    A render job has at most one leased attempt. Lease tokens fence an expired
    ephemeral worker from updating a task after another worker has reclaimed it.
    The browser never receives those tokens.
    """

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_symlink():
            raise RecorderError(f"render queue database may not be a symlink: {path}")
        self.path = path
        self._lock = threading.RLock()
        with self._session(immediate=True) as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS render_workers (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    capabilities_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    heartbeat_at REAL NOT NULL,
                    current_job_id TEXT
                );

                CREATE TABLE IF NOT EXISTS render_jobs (
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
                    retry_of TEXT,
                    eligible_at REAL NOT NULL DEFAULT 0
                );

                CREATE INDEX IF NOT EXISTS render_jobs_recent
                    ON render_jobs(created_at DESC);
                CREATE INDEX IF NOT EXISTS render_jobs_recording
                    ON render_jobs(recording_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS render_jobs_fingerprint
                    ON render_jobs(fingerprint, created_at DESC);

                CREATE TABLE IF NOT EXISTS render_attempts (
                    id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL,
                    worker_id TEXT NOT NULL,
                    generation INTEGER NOT NULL,
                    state TEXT NOT NULL,
                    phase TEXT NOT NULL,
                    progress_json TEXT,
                    result_json TEXT,
                    error TEXT,
                    lease_token TEXT,
                    lease_expires_at REAL,
                    created_at REAL NOT NULL,
                    heartbeat_at REAL NOT NULL,
                    completed_at REAL,
                    FOREIGN KEY(job_id) REFERENCES render_jobs(id),
                    FOREIGN KEY(worker_id) REFERENCES render_workers(id)
                );

                CREATE INDEX IF NOT EXISTS render_attempts_job
                    ON render_attempts(job_id, generation DESC);
                CREATE INDEX IF NOT EXISTS render_attempts_lease
                    ON render_attempts(state, lease_expires_at);
                """
            )
            job_columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(render_jobs)").fetchall()
            }
            if "eligible_at" not in job_columns:
                try:
                    connection.execute(
                        "ALTER TABLE render_jobs "
                        "ADD COLUMN eligible_at REAL NOT NULL DEFAULT 0"
                    )
                except sqlite3.OperationalError as exc:
                    # Separate dashboard/RPC processes can both observe the
                    # legacy schema before either migration commits.
                    if "duplicate column name" not in str(exc).lower():
                        raise
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS render_jobs_claimable
                    ON render_jobs(state, eligible_at, created_at, id)
                """
            )
            self._reclaim_expired(connection, time.time())

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @contextmanager
    def _session(self, *, immediate: bool = False) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            if immediate:
                connection.execute("BEGIN IMMEDIATE")
            else:
                connection.execute("BEGIN")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def create(self, payload: dict[str, Any], *, retry_of: str | None = None) -> dict[str, Any]:
        encoded, _ = _json_object(payload, "render job payload")
        recording_id = payload.get("recording_id")
        if (
            not isinstance(recording_id, str)
            or len(recording_id) != 24
            or any(character not in "0123456789abcdef" for character in recording_id)
        ):
            raise RecorderError("render job payload has an invalid recording ID")
        fingerprint = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        retry_id = _uuid(retry_of, "retry job ID") if retry_of is not None else None
        now = time.time()
        with self._lock, self._session(immediate=True) as connection:
            self._reclaim_expired(connection, now)
            if retry_id is None:
                existing = connection.execute(
                    """
                    SELECT * FROM render_jobs
                     WHERE fingerprint = ?
                       AND state IN ('queued', 'downloading', 'rendering', 'uploading',
                                     'verifying', 'attaching', 'complete')
                     ORDER BY created_at DESC LIMIT 1
                    """,
                    (fingerprint,),
                ).fetchone()
                if existing is not None:
                    return self._job(connection, existing)
            job_id = str(uuid.uuid4())
            connection.execute(
                """
                INSERT INTO render_jobs(
                    id, fingerprint, recording_id, payload_json, state,
                    created_at, updated_at, retry_of, eligible_at
                ) VALUES (?, ?, ?, ?, 'queued', ?, ?, ?, 0)
                """,
                (job_id, fingerprint, recording_id, encoded, now, now, retry_id),
            )
            row = connection.execute("SELECT * FROM render_jobs WHERE id = ?", (job_id,)).fetchone()
            assert row is not None
            return self._job(connection, row)

    def get(self, job_id: str) -> dict[str, Any]:
        canonical = _uuid(job_id, "render job ID")
        with self._lock, self._session(immediate=True) as connection:
            self._reclaim_expired(connection, time.time())
            row = connection.execute("SELECT * FROM render_jobs WHERE id = ?", (canonical,)).fetchone()
            if row is None:
                raise KeyError(canonical)
            return self._job(connection, row)

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        if not isinstance(limit, int) or isinstance(limit, bool):
            raise RecorderError("render job limit must be an integer")
        bounded = max(1, min(limit, 100))
        with self._lock, self._session(immediate=True) as connection:
            self._reclaim_expired(connection, time.time())
            rows = connection.execute(
                "SELECT * FROM render_jobs ORDER BY created_at DESC LIMIT ?", (bounded,)
            ).fetchall()
            return [self._job(connection, row) for row in rows]

    def latest_for_recording(self, recording_id: str) -> dict[str, Any] | None:
        with self._lock, self._session(immediate=True) as connection:
            self._reclaim_expired(connection, time.time())
            row = connection.execute(
                """
                SELECT * FROM render_jobs
                 WHERE recording_id = ? ORDER BY created_at DESC LIMIT 1
                """,
                (recording_id,),
            ).fetchone()
            return self._job(connection, row) if row is not None else None

    def register_worker(
        self,
        name: str,
        *,
        worker_id: str | None = None,
        capabilities: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        canonical = str(uuid.uuid4()) if worker_id is None else _uuid(worker_id, "render worker ID")
        if not isinstance(name, str) or not name.strip() or len(name) > 80:
            raise RecorderError("render worker name must be between 1 and 80 characters")
        if any(ord(character) < 32 for character in name):
            raise RecorderError("render worker name may not contain control characters")
        encoded, _ = _json_object(capabilities or {}, "render worker capabilities")
        now = time.time()
        with self._lock, self._session(immediate=True) as connection:
            connection.execute(
                """
                INSERT INTO render_workers(
                    id, name, capabilities_json, created_at, heartbeat_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name = excluded.name,
                    capabilities_json = excluded.capabilities_json,
                    heartbeat_at = excluded.heartbeat_at
                """,
                (canonical, name.strip(), encoded, now, now),
            )
            row = connection.execute("SELECT * FROM render_workers WHERE id = ?", (canonical,)).fetchone()
            assert row is not None
            return self._worker(connection, row, now, stale_after=45)

    def workers(self, *, stale_after: int = 45) -> list[dict[str, Any]]:
        if (
            not isinstance(stale_after, int)
            or isinstance(stale_after, bool)
            or not 10 <= stale_after <= 3600
        ):
            raise RecorderError("worker stale threshold must be between 10 and 3600 seconds")
        now = time.time()
        with self._lock, self._session(immediate=True) as connection:
            self._reclaim_expired(connection, now)
            rows = connection.execute(
                "SELECT * FROM render_workers ORDER BY name, id"
            ).fetchall()
            return [self._worker(connection, row, now, stale_after) for row in rows]

    def worker_heartbeat(self, worker_id: str) -> dict[str, Any]:
        canonical = _uuid(worker_id, "render worker ID")
        now = time.time()
        with self._lock, self._session(immediate=True) as connection:
            self._require_worker(connection, canonical)
            connection.execute(
                "UPDATE render_workers SET heartbeat_at = ? WHERE id = ?", (now, canonical)
            )
            row = connection.execute("SELECT * FROM render_workers WHERE id = ?", (canonical,)).fetchone()
            assert row is not None
            return self._worker(connection, row, now, stale_after=45)

    def claim(
        self,
        worker_id: str,
        *,
        job_id: str | None = None,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
    ) -> dict[str, Any] | None:
        worker = _uuid(worker_id, "render worker ID")
        selected_job = _uuid(job_id, "render job ID") if job_id is not None else None
        duration = _lease_seconds(lease_seconds)
        now = time.time()
        with self._lock, self._session(immediate=True) as connection:
            self._reclaim_expired(connection, now)
            self._require_worker(connection, worker)
            busy = connection.execute(
                """
                SELECT id FROM render_attempts
                 WHERE worker_id = ? AND state = 'leased' AND lease_expires_at > ?
                """,
                (worker, now),
            ).fetchone()
            if busy is not None:
                raise RecorderError("render worker already owns an active lease")
            if selected_job is None:
                row = connection.execute(
                    """
                    SELECT * FROM render_jobs
                     WHERE state = 'queued' AND eligible_at <= ?
                     ORDER BY created_at, id LIMIT 1
                    """,
                    (now,),
                ).fetchone()
            else:
                row = connection.execute(
                    "SELECT * FROM render_jobs WHERE id = ?", (selected_job,)
                ).fetchone()
                if row is None:
                    raise KeyError(selected_job)
                if row["state"] != "queued":
                    raise RecorderError(f"render job cannot be claimed while {row['state']}")
            connection.execute(
                "UPDATE render_workers SET heartbeat_at = ? WHERE id = ?", (now, worker)
            )
            if row is None:
                return None
            job = self._job(connection, row)
            attempt_id = str(uuid.uuid4())
            token = secrets.token_urlsafe(32)
            generation = int(row["attempt_count"]) + 1
            expires = now + duration
            initial_progress = _progress("downloading", None, None, None)
            progress_json = json.dumps(initial_progress, sort_keys=True)
            connection.execute(
                """
                INSERT INTO render_attempts(
                    id, job_id, worker_id, generation, state, phase,
                    progress_json, lease_token, lease_expires_at, created_at, heartbeat_at
                ) VALUES (?, ?, ?, ?, 'leased', 'downloading', ?, ?, ?, ?, ?)
                """,
                (
                    attempt_id,
                    job["id"],
                    worker,
                    generation,
                    progress_json,
                    token,
                    expires,
                    now,
                    now,
                ),
            )
            connection.execute(
                """
                UPDATE render_jobs
                   SET state = 'downloading', progress_json = ?, updated_at = ?,
                       started_at = COALESCE(started_at, ?), active_attempt_id = ?,
                       attempt_count = ?, eligible_at = 0
                 WHERE id = ? AND state = 'queued'
                """,
                (progress_json, now, now, attempt_id, generation, job["id"]),
            )
            connection.execute(
                "UPDATE render_workers SET current_job_id = ? WHERE id = ?",
                (job["id"], worker),
            )
            claimed = connection.execute(
                "SELECT * FROM render_jobs WHERE id = ?", (job["id"],)
            ).fetchone()
            assert claimed is not None
            return {
                "job": self._job(connection, claimed),
                "attempt": {
                    "id": attempt_id,
                    "generation": generation,
                    "lease_token": token,
                    "lease_expires_at": _now_iso(expires),
                    "lease_expires_at_unix_ms": int(expires * 1000),
                },
            }

    def heartbeat(
        self,
        worker_id: str,
        attempt_id: str,
        lease_token: str,
        *,
        phase: str,
        current: int | None = None,
        total: int | None = None,
        message: str | None = None,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
    ) -> dict[str, Any]:
        worker = _uuid(worker_id, "render worker ID")
        attempt = _uuid(attempt_id, "render attempt ID")
        duration = _lease_seconds(lease_seconds)
        progress = _progress(phase, current, total, message)
        now = time.time()
        with self._lock, self._session(immediate=True) as connection:
            row, job = self._leased_attempt(connection, worker, attempt, lease_token, now)
            previous = str(row["phase"])
            if WORKER_PHASES.index(phase) < WORKER_PHASES.index(previous):
                raise RecorderError(f"render phase cannot move backward from {previous} to {phase}")
            encoded = json.dumps(progress, sort_keys=True)
            expires = now + duration
            connection.execute(
                """
                UPDATE render_attempts
                   SET phase = ?, progress_json = ?, heartbeat_at = ?, lease_expires_at = ?
                 WHERE id = ?
                """,
                (phase, encoded, now, expires, attempt),
            )
            connection.execute(
                """
                UPDATE render_jobs SET state = ?, progress_json = ?, updated_at = ?
                 WHERE id = ? AND active_attempt_id = ?
                """,
                (phase, encoded, now, job["id"], attempt),
            )
            connection.execute(
                "UPDATE render_workers SET heartbeat_at = ? WHERE id = ?", (now, worker)
            )
            updated = connection.execute(
                "SELECT * FROM render_jobs WHERE id = ?", (job["id"],)
            ).fetchone()
            assert updated is not None
            value = self._job(connection, updated)
            value["lease_expires_at"] = _now_iso(expires)
            return value

    def mark_uploaded(
        self,
        worker_id: str,
        attempt_id: str,
        lease_token: str,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        worker = _uuid(worker_id, "render worker ID")
        attempt = _uuid(attempt_id, "render attempt ID")
        result_json, _ = _json_object(result, "render upload result")
        now = time.time()
        with self._lock, self._session(immediate=True) as connection:
            _, job = self._leased_attempt(connection, worker, attempt, lease_token, now)
            connection.execute(
                """
                UPDATE render_attempts
                   SET state = 'uploaded', phase = 'uploading', result_json = ?,
                       heartbeat_at = ?, completed_at = ?, lease_token = NULL,
                       lease_expires_at = NULL
                 WHERE id = ?
                """,
                (result_json, now, now, attempt),
            )
            connection.execute(
                """
                UPDATE render_jobs
                   SET state = 'verifying', result_json = ?, updated_at = ?
                 WHERE id = ? AND active_attempt_id = ?
                """,
                (result_json, now, job["id"], attempt),
            )
            connection.execute(
                "UPDATE render_workers SET current_job_id = NULL, heartbeat_at = ? WHERE id = ?",
                (now, worker),
            )
            updated = connection.execute(
                "SELECT * FROM render_jobs WHERE id = ?", (job["id"],)
            ).fetchone()
            assert updated is not None
            return self._job(connection, updated)

    def fail_attempt(
        self,
        worker_id: str,
        attempt_id: str,
        lease_token: str,
        error: str,
    ) -> dict[str, Any]:
        worker = _uuid(worker_id, "render worker ID")
        attempt = _uuid(attempt_id, "render attempt ID")
        message = str(error)[:MAX_ERROR_CHARS] or "render worker failed without an error message"
        now = time.time()
        with self._lock, self._session(immediate=True) as connection:
            _, job = self._leased_attempt(connection, worker, attempt, lease_token, now)
            connection.execute(
                """
                UPDATE render_attempts
                   SET state = 'failed', error = ?, heartbeat_at = ?, completed_at = ?,
                       lease_token = NULL, lease_expires_at = NULL
                 WHERE id = ?
                """,
                (message, now, now, attempt),
            )
            connection.execute(
                """
                UPDATE render_jobs
                   SET state = 'failed', error = ?, updated_at = ?, completed_at = ?
                 WHERE id = ? AND active_attempt_id = ?
                """,
                (message, now, now, job["id"], attempt),
            )
            connection.execute(
                "UPDATE render_workers SET current_job_id = NULL, heartbeat_at = ? WHERE id = ?",
                (now, worker),
            )
            updated = connection.execute(
                "SELECT * FROM render_jobs WHERE id = ?", (job["id"],)
            ).fetchone()
            assert updated is not None
            return self._job(connection, updated)

    def defer_attempt(
        self,
        worker_id: str,
        attempt_id: str,
        lease_token: str,
        reason: str,
        *,
        cooldown_seconds: int = DEFAULT_DEFER_COOLDOWN_SECONDS,
    ) -> dict[str, Any]:
        """Return a leased job to the queue when an immutable input is not ready yet."""

        worker = _uuid(worker_id, "render worker ID")
        attempt = _uuid(attempt_id, "render attempt ID")
        if (
            not isinstance(cooldown_seconds, int)
            or isinstance(cooldown_seconds, bool)
            or not MIN_DEFER_COOLDOWN_SECONDS
            <= cooldown_seconds
            <= MAX_DEFER_COOLDOWN_SECONDS
        ):
            raise RecorderError(
                "render defer cooldown must be between "
                f"{MIN_DEFER_COOLDOWN_SECONDS} and {MAX_DEFER_COOLDOWN_SECONDS} seconds"
            )
        message = str(reason)[:MAX_ERROR_CHARS] or "render input is not ready"
        now = time.time()
        eligible_at = now + cooldown_seconds
        with self._lock, self._session(immediate=True) as connection:
            _, job = self._leased_attempt(connection, worker, attempt, lease_token, now)
            connection.execute(
                """
                UPDATE render_attempts
                   SET state = 'deferred', error = ?, heartbeat_at = ?, completed_at = ?,
                       lease_token = NULL, lease_expires_at = NULL
                 WHERE id = ?
                """,
                (message, now, now, attempt),
            )
            connection.execute(
                """
                UPDATE render_jobs
                   SET state = 'queued', progress_json = NULL, error = NULL,
                       updated_at = ?, active_attempt_id = NULL, eligible_at = ?
                 WHERE id = ? AND active_attempt_id = ?
                """,
                (now, eligible_at, job["id"], attempt),
            )
            connection.execute(
                """
                UPDATE render_workers SET current_job_id = NULL, heartbeat_at = ?
                 WHERE id = ?
                """,
                (now, worker),
            )
            updated = connection.execute(
                "SELECT * FROM render_jobs WHERE id = ?", (job["id"],)
            ).fetchone()
            assert updated is not None
            return self._job(connection, updated)

    def set_server_phase(
        self,
        job_id: str,
        phase: str,
        *,
        progress: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        canonical = _uuid(job_id, "render job ID")
        if phase not in SERVER_PHASES:
            raise RecorderError(f"invalid server render phase: {phase}")
        progress_json = None
        if progress is not None:
            progress_json, _ = _json_object(progress, "server render progress")
        now = time.time()
        with self._lock, self._session(immediate=True) as connection:
            row = connection.execute("SELECT * FROM render_jobs WHERE id = ?", (canonical,)).fetchone()
            if row is None:
                raise KeyError(canonical)
            current = str(row["state"])
            if current not in SERVER_PHASES:
                raise RecorderError(f"render job cannot enter {phase} while {current}")
            if current == "attaching" and phase == "verifying":
                raise RecorderError("render server phase cannot move backward")
            connection.execute(
                """
                UPDATE render_jobs SET state = ?, progress_json = COALESCE(?, progress_json),
                       updated_at = ? WHERE id = ?
                """,
                (phase, progress_json, now, canonical),
            )
            updated = connection.execute("SELECT * FROM render_jobs WHERE id = ?", (canonical,)).fetchone()
            assert updated is not None
            return self._job(connection, updated)

    def complete(
        self,
        job_id: str,
        result: dict[str, Any],
        *,
        partial: bool = False,
    ) -> dict[str, Any]:
        canonical = _uuid(job_id, "render job ID")
        result_json, _ = _json_object(result, "render job result")
        state = "partial" if partial else "complete"
        now = time.time()
        with self._lock, self._session(immediate=True) as connection:
            row = connection.execute("SELECT * FROM render_jobs WHERE id = ?", (canonical,)).fetchone()
            if row is None:
                raise KeyError(canonical)
            if row["state"] not in SERVER_PHASES:
                raise RecorderError(f"render job cannot complete while {row['state']}")
            connection.execute(
                """
                UPDATE render_jobs SET state = ?, result_json = ?, error = NULL,
                       updated_at = ?, completed_at = ? WHERE id = ?
                """,
                (state, result_json, now, now, canonical),
            )
            updated = connection.execute("SELECT * FROM render_jobs WHERE id = ?", (canonical,)).fetchone()
            assert updated is not None
            return self._job(connection, updated)

    def fail(self, job_id: str, error: str) -> dict[str, Any]:
        canonical = _uuid(job_id, "render job ID")
        message = str(error)[:MAX_ERROR_CHARS] or "render job failed without an error message"
        now = time.time()
        with self._lock, self._session(immediate=True) as connection:
            row = connection.execute("SELECT * FROM render_jobs WHERE id = ?", (canonical,)).fetchone()
            if row is None:
                raise KeyError(canonical)
            if row["state"] in TERMINAL_JOB_STATES:
                raise RecorderError(f"render job cannot fail while {row['state']}")
            self._finish_active_attempt(connection, row, "failed", message, now)
            connection.execute(
                """
                UPDATE render_jobs SET state = 'failed', error = ?, updated_at = ?,
                       completed_at = ? WHERE id = ?
                """,
                (message, now, now, canonical),
            )
            updated = connection.execute("SELECT * FROM render_jobs WHERE id = ?", (canonical,)).fetchone()
            assert updated is not None
            return self._job(connection, updated)

    def cancel(self, job_id: str) -> dict[str, Any]:
        canonical = _uuid(job_id, "render job ID")
        now = time.time()
        with self._lock, self._session(immediate=True) as connection:
            row = connection.execute("SELECT * FROM render_jobs WHERE id = ?", (canonical,)).fetchone()
            if row is None:
                raise KeyError(canonical)
            if row["state"] == "canceled":
                return self._job(connection, row)
            if row["state"] in SERVER_PHASES:
                raise RecorderError(
                    f"render job cannot be canceled while server-side {row['state']} is in progress"
                )
            if row["state"] in {"complete", "partial", "failed"}:
                raise RecorderError(f"render job cannot be canceled while {row['state']}")
            self._finish_active_attempt(connection, row, "canceled", "canceled by operator", now)
            connection.execute(
                """
                UPDATE render_jobs SET state = 'canceled', error = NULL,
                       updated_at = ?, completed_at = ? WHERE id = ?
                """,
                (now, now, canonical),
            )
            updated = connection.execute("SELECT * FROM render_jobs WHERE id = ?", (canonical,)).fetchone()
            assert updated is not None
            return self._job(connection, updated)

    def retry(self, job_id: str) -> dict[str, Any]:
        original = self.get(job_id)
        if original["state"] not in {"failed", "partial", "canceled"}:
            raise RecorderError(f"render job cannot be retried while {original['state']}")
        return self.create(original["payload"], retry_of=original["id"])

    def _leased_attempt(
        self,
        connection: sqlite3.Connection,
        worker_id: str,
        attempt_id: str,
        lease_token: str,
        now: float,
    ) -> tuple[sqlite3.Row, sqlite3.Row]:
        if not isinstance(lease_token, str):
            raise RecorderError("invalid render lease token")
        row = connection.execute(
            "SELECT * FROM render_attempts WHERE id = ?", (attempt_id,)
        ).fetchone()
        if row is None:
            raise KeyError(attempt_id)
        stored_token = str(row["lease_token"] or "")
        allowed = hmac.compare_digest(str(row["worker_id"]), worker_id) & hmac.compare_digest(
            stored_token, lease_token
        )
        if not allowed or row["state"] != "leased":
            raise RecorderError("render lease is not owned by this worker")
        expires = row["lease_expires_at"]
        if not isinstance(expires, (int, float)) or not math.isfinite(expires) or expires <= now:
            self._reclaim_expired(connection, now)
            raise RecorderError("render lease has expired")
        job = connection.execute("SELECT * FROM render_jobs WHERE id = ?", (row["job_id"],)).fetchone()
        if job is None or job["active_attempt_id"] != attempt_id or job["state"] not in WORKER_PHASES:
            raise RecorderError("render lease is no longer active")
        return row, job

    @staticmethod
    def _require_worker(connection: sqlite3.Connection, worker_id: str) -> sqlite3.Row:
        row = connection.execute("SELECT * FROM render_workers WHERE id = ?", (worker_id,)).fetchone()
        if row is None:
            raise RecorderError("render worker is not registered")
        return row

    @staticmethod
    def _finish_active_attempt(
        connection: sqlite3.Connection,
        job: sqlite3.Row,
        state: str,
        error: str,
        now: float,
    ) -> None:
        attempt_id = job["active_attempt_id"]
        if attempt_id is None:
            return
        attempt = connection.execute(
            "SELECT worker_id FROM render_attempts WHERE id = ?", (attempt_id,)
        ).fetchone()
        connection.execute(
            """
            UPDATE render_attempts SET state = ?, error = ?, completed_at = ?,
                   lease_token = NULL, lease_expires_at = NULL WHERE id = ?
            """,
            (state, error, now, attempt_id),
        )
        if attempt is not None:
            connection.execute(
                "UPDATE render_workers SET current_job_id = NULL WHERE id = ?",
                (attempt["worker_id"],),
            )

    @staticmethod
    def _reclaim_expired(connection: sqlite3.Connection, now: float) -> None:
        rows = connection.execute(
            """
            SELECT id, job_id, worker_id FROM render_attempts
             WHERE state = 'leased' AND lease_expires_at <= ?
            """,
            (now,),
        ).fetchall()
        for row in rows:
            connection.execute(
                """
                UPDATE render_attempts
                   SET state = 'expired', error = 'worker lease expired', completed_at = ?,
                       lease_token = NULL, lease_expires_at = NULL WHERE id = ?
                """,
                (now, row["id"]),
            )
            connection.execute(
                """
                UPDATE render_jobs
                   SET state = 'queued', progress_json = NULL, updated_at = ?,
                       active_attempt_id = NULL, eligible_at = 0
                 WHERE id = ? AND active_attempt_id = ?
                """,
                (now, row["job_id"], row["id"]),
            )
            connection.execute(
                """
                UPDATE render_workers SET current_job_id = NULL
                 WHERE id = ? AND current_job_id = ?
                """,
                (row["worker_id"], row["job_id"]),
            )

    @staticmethod
    def _job(connection: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
        active_attempt = None
        if row["active_attempt_id"]:
            attempt = connection.execute(
                "SELECT * FROM render_attempts WHERE id = ?", (row["active_attempt_id"],)
            ).fetchone()
            if attempt is not None:
                active_attempt = {
                    "id": attempt["id"],
                    "worker_id": attempt["worker_id"],
                    "generation": int(attempt["generation"]),
                    "state": attempt["state"],
                    "phase": attempt["phase"],
                    "heartbeat_at": _now_iso(float(attempt["heartbeat_at"])),
                    "lease_expires_at": _now_iso(attempt["lease_expires_at"]),
                }
        return {
            "id": row["id"],
            "recording_id": row["recording_id"],
            "state": row["state"],
            "payload": json.loads(row["payload_json"]),
            "progress": json.loads(row["progress_json"]) if row["progress_json"] else None,
            "result": json.loads(row["result_json"]) if row["result_json"] else None,
            "error": row["error"],
            "created_at": _now_iso(float(row["created_at"])),
            "updated_at": _now_iso(float(row["updated_at"])),
            "started_at": _now_iso(row["started_at"]),
            "completed_at": _now_iso(row["completed_at"]),
            "attempt_count": int(row["attempt_count"]),
            "active_attempt": active_attempt,
            "retry_of": row["retry_of"],
        }

    @staticmethod
    def _worker(
        connection: sqlite3.Connection,
        row: sqlite3.Row,
        now: float,
        stale_after: int,
    ) -> dict[str, Any]:
        heartbeat = float(row["heartbeat_at"])
        online = now - heartbeat <= stale_after
        current_job = row["current_job_id"]
        busy = False
        if online and current_job:
            attempt = connection.execute(
                """
                SELECT 1 FROM render_attempts
                 WHERE job_id = ? AND worker_id = ? AND state = 'leased'
                   AND lease_expires_at > ? LIMIT 1
                """,
                (current_job, row["id"], now),
            ).fetchone()
            busy = attempt is not None
        return {
            "id": row["id"],
            "name": row["name"],
            "state": "busy" if busy else "online" if online else "offline",
            "capabilities": json.loads(row["capabilities_json"]),
            "created_at": _now_iso(float(row["created_at"])),
            "heartbeat_at": _now_iso(heartbeat),
            "heartbeat_age_seconds": round(max(0.0, now - heartbeat), 3),
            "current_job_id": current_job if busy else None,
        }
