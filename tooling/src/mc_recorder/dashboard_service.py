from __future__ import annotations

import fcntl
import hashlib
import json
import os
import secrets
import threading
import time
import uuid
from pathlib import Path
from typing import Any, TextIO

from .config import RecorderConfig
from .dashboard_index import DatasetIndex
from .dashboard_jobs import JobManager, JobStore
from .dataset_viewer import DatasetViewer, DatasetViewerError, opaque_dataset_id
from .episodes import directory_size, inspect_epoch, resolve_episode
from .errors import RecorderError
from .exporter import export_episode
from .operations import operation_lock
from .server import compose_status, start_server, stop_server


MAX_CONTROL_JSON_BYTES = 8 * 1024 * 1024
HEARTBEAT_STALE_SECONDS = 5.0
SEAL_RESPONSE_TIMEOUT_SECONDS = 90.0


def _read_json_object(path: Path, *, maximum: int = MAX_CONTROL_JSON_BYTES) -> dict[str, Any] | None:
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > maximum:
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _required_uuid(value: object, label: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError) as exc:
        raise RecorderError(f"invalid {label}: {value!r}") from exc


class DashboardService:
    def __init__(self, config: RecorderConfig):
        self.config = config
        self.control_root = config.paths.runtime / "control"
        self.control_root.mkdir(parents=True, exist_ok=True)
        (self.control_root / "requests").mkdir(parents=True, exist_ok=True)
        (self.control_root / "responses").mkdir(parents=True, exist_ok=True)
        self._instance_handle: TextIO | None = self._acquire_instance_lock(config.paths.runtime)
        try:
            self.dataset_viewer = DatasetViewer(config.paths.exports, config.paths.runtime)
            self.dataset_index = DatasetIndex(self.dataset_viewer)
            self.jobs = JobManager(JobStore(config.paths.runtime / "dashboard.sqlite3"))
        except Exception:
            index = getattr(self, "dataset_index", None)
            if index is not None:
                index.close()
            self._release_instance_lock()
            raise
        self.csrf_token = secrets.token_urlsafe(32)
        self._dataset_cache: dict[
            Path,
            tuple[tuple[tuple[object, ...], ...], bool, str | None],
        ] = {}
        self._sealed_cache: dict[Path, tuple[tuple[tuple[object, ...], ...], int | None]] = {}
        self._recording_phases: dict[str, str] = {}
        self._state_lock = threading.Lock()
        self._compose_cache: tuple[float, dict[str, Any]] | None = None
        self._storage_cache: tuple[float, int] | None = None
        self._closed = False

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self.jobs.close()
        finally:
            try:
                self.dataset_index.close()
            finally:
                self._release_instance_lock()

    @staticmethod
    def _acquire_instance_lock(runtime: Path) -> TextIO:
        runtime.mkdir(parents=True, exist_ok=True)
        path = runtime / "dashboard.lock"
        if path.is_symlink():
            raise RecorderError(f"dashboard instance lock may not be a symlink: {path}")
        handle = path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            handle.seek(0)
            owner = handle.read(2048).strip()
            handle.close()
            detail = f" ({owner})" if owner else ""
            raise RecorderError(f"another dashboard process is already running{detail}") from exc
        handle.seek(0)
        handle.truncate()
        json.dump({"pid": os.getpid(), "started_at_unix_ms": int(time.time() * 1000)}, handle)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
        return handle

    def _release_instance_lock(self) -> None:
        handle = getattr(self, "_instance_handle", None)
        if handle is None:
            return
        self._instance_handle = None
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()

    def capture_status(self) -> dict[str, Any]:
        value = _read_json_object(self.control_root / "status.json") or {}
        heartbeat_ms = value.get("heartbeat_unix_ms")
        if not isinstance(heartbeat_ms, (int, float)) or isinstance(heartbeat_ms, bool):
            heartbeat_ms = value.get("updated_at_unix_ms")
        age_seconds: float | None = None
        if isinstance(heartbeat_ms, (int, float)) and not isinstance(heartbeat_ms, bool):
            age_seconds = max(0.0, time.time() - float(heartbeat_ms) / 1000.0)
        state = str(value.get("state") or value.get("status") or "offline")
        terminal = state in {"stopped", "failed"}
        if not terminal and (age_seconds is None or age_seconds > HEARTBEAT_STALE_SECONDS):
            state = "offline" if not value else "stale"
        epoch = value.get("epoch") if isinstance(value.get("epoch"), dict) else {}
        writer = value.get("writer") if isinstance(value.get("writer"), dict) else {}
        active_connections = value.get("active_connections")
        return {
            **value,
            "state": state,
            "heartbeat_age_seconds": round(age_seconds, 3) if age_seconds is not None else None,
            "fresh": age_seconds is not None and age_seconds <= HEARTBEAT_STALE_SECONDS,
            "epoch_index": epoch.get("index"),
            "epoch_start_server_tick": epoch.get("start_server_tick"),
            "writer_queue_depth": writer.get("queue_size"),
            "writer_queue_capacity": writer.get("queue_capacity"),
            "connected_player_count": len(active_connections) if isinstance(active_connections, list) else None,
        }

    def status(self) -> dict[str, Any]:
        server = self._cached_compose_status()
        active = self.jobs.store.active()
        if active is not None:
            if active["kind"] == "server_start":
                server["state"] = "starting"
            elif active["kind"] == "server_stop":
                server["state"] = "stopping"
        used = self._cached_storage_usage()
        quota = self.config.storage.quota_bytes
        warn = quota * self.config.storage.warn_percent // 100
        storage_state = "full" if used >= quota else "warning" if used >= warn else "ok"
        return {
            "server": server,
            "capture": self.capture_status(),
            "storage": {
                "state": storage_state,
                "used_bytes": used,
                "quota_bytes": quota,
                "warn_bytes": warn,
            },
            "active_job": active,
            "recent_jobs": self.jobs.store.recent(10),
            "csrf_token": self.csrf_token,
        }

    def _cached_compose_status(self) -> dict[str, Any]:
        now = time.monotonic()
        with self._state_lock:
            cached = self._compose_cache
            if cached is not None and now - cached[0] < 3.0:
                return dict(cached[1])
        value = dict(compose_status(self.config))
        with self._state_lock:
            self._compose_cache = now, value
        return dict(value)

    def _cached_storage_usage(self) -> int:
        now = time.monotonic()
        with self._state_lock:
            cached = self._storage_cache
            if cached is not None and now - cached[0] < 10.0:
                return cached[1]
        used = directory_size(self.config.paths.captures) + directory_size(self.config.paths.replays)
        with self._state_lock:
            self._storage_cache = now, used
        return used

    def _invalidate_host_status(self) -> None:
        with self._state_lock:
            self._compose_cache = None
            self._storage_cache = None

    def start_server_job(self) -> dict[str, Any]:
        return self.jobs.submit("server_start", {}, self._start_server)

    def stop_server_job(self) -> dict[str, Any]:
        return self.jobs.submit("server_stop", {}, self._stop_server)

    def _start_server(self) -> dict[str, Any]:
        self._invalidate_host_status()
        try:
            jar, report = start_server(self.config, wait=True, capture_output=True)
            return {"jar": str(jar), "storage": report.as_json()}
        finally:
            self._invalidate_host_status()

    def _stop_server(self) -> dict[str, Any]:
        self._invalidate_host_status()
        try:
            report = stop_server(self.config, timeout_seconds=120, capture_output=True)
            return {"storage": report.as_json()}
        finally:
            self._invalidate_host_status()

    def _connection_rows(self) -> list[dict[str, Any]]:
        paths = [self.control_root / "connections.json"]
        sessions = self.control_root / "sessions"
        if sessions.is_dir() and not sessions.is_symlink():
            paths.extend(sorted(sessions.glob("*.connections.json")))
        rows_by_identity: dict[tuple[str, str], dict[str, Any]] = {}
        for path in paths:
            ledger = _read_json_object(path) or {}
            session_id = ledger.get("session_id")
            raw_rows: object = ledger.get("connections", ledger.get("items", []))
            if isinstance(raw_rows, dict):
                candidates = [value for value in raw_rows.values() if isinstance(value, dict)]
            elif isinstance(raw_rows, list):
                candidates = [value for value in raw_rows if isinstance(value, dict)]
            else:
                candidates = []
            for raw in candidates:
                row = {**raw, **({"session_id": session_id} if "session_id" not in raw else {})}
                identity = row.get("session_id"), row.get("connection_id")
                if all(isinstance(value, str) and value for value in identity):
                    rows_by_identity[(identity[0], identity[1])] = row
        return list(rows_by_identity.values())

    def recordings(self) -> list[dict[str, Any]]:
        capture = self.capture_status()
        current_session = capture.get("session_id")
        fresh = bool(capture.get("fresh"))
        capture_state = capture.get("state")
        capture_failure = capture.get("failure_reason")
        recent_jobs = self.jobs.store.recent(100)
        active_jobs = {
            job["payload"].get("recording_id"): job
            for job in recent_jobs
            if job["state"] in {"queued", "running"} and job["kind"] == "generate_dataset"
        }
        latest_jobs: dict[str, dict[str, Any]] = {}
        for job in recent_jobs:
            recording_id = job["payload"].get("recording_id")
            if job["kind"] == "generate_dataset" and isinstance(recording_id, str):
                latest_jobs.setdefault(recording_id, job)
        connection_rows = self._connection_rows()
        sealed_by_session = {
            session_id: self._sealed_through_sequence(session_id)
            for session_id in {
                row.get("session_id")
                for row in connection_rows
                if isinstance(row.get("session_id"), str)
            }
        }
        results: list[dict[str, Any]] = []
        for raw in connection_rows:
            session_id = raw.get("session_id")
            player_uuid = raw.get("player_uuid")
            connection_id = raw.get("connection_id")
            if not all(isinstance(value, str) and value for value in (session_id, player_uuid, connection_id)):
                continue
            recording_id = self._recording_id(session_id, connection_id)
            end_sequence = self._integer(raw, "end_sequence", "connection_end_sequence")
            start_tick = self._integer(
                raw,
                "join_server_tick",
                "start_server_tick",
                "start_tick",
                "connection_start_server_tick",
            )
            end_tick = self._integer(raw, "end_server_tick", "end_tick", "connection_end_server_tick")
            output = self._dataset_output(session_id, player_uuid, connection_id)
            dataset_status, dataset_error = self._dataset_match(
                output,
                session_id=session_id,
                player_uuid=player_uuid,
                connection_id=connection_id,
                start_tick=start_tick,
                end_tick=end_tick,
            )
            dataset_ready = dataset_status == "matching"
            sealed_sequence = sealed_by_session.get(session_id, -1)
            current_capture = session_id == current_session
            capture_recording = fresh and current_capture and capture_state == "recording"
            capture_failed = current_capture and capture_state == "failed"
            if dataset_ready:
                state = "complete"
            elif recording_id in active_jobs:
                with self._state_lock:
                    state = self._recording_phases.get(recording_id, "sealing")
            elif dataset_status == "conflicting":
                state = "failed"
            elif (
                latest_jobs.get(recording_id, {}).get("state") == "failed"
                and end_sequence is not None
                and (sealed_sequence >= end_sequence or capture_recording)
            ):
                state = "failed"
            elif dataset_status == "indexing":
                state = "generating"
            elif end_sequence is None:
                if capture_failed:
                    state = "failed"
                else:
                    state = "recording" if capture_recording else "interrupted"
            elif sealed_sequence >= end_sequence:
                state = "disconnected"
            elif capture_failed:
                state = "failed"
            elif capture_recording:
                state = "waiting_for_seal"
            else:
                state = "interrupted"
            results.append(
                {
                    **raw,
                    "id": recording_id,
                    "session_id": session_id,
                    "player_uuid": player_uuid,
                    "connection_id": connection_id,
                    "start_tick": start_tick,
                    "end_tick": end_tick,
                    "end_sequence": end_sequence,
                    "sealed_through_sequence": sealed_sequence,
                    "state": state,
                    "can_generate": end_sequence is not None
                    and state in {"disconnected", "waiting_for_seal", "failed"}
                    and dataset_status != "conflicting"
                    and (sealed_sequence >= end_sequence or capture_recording),
                    "dataset_id": self._dataset_id(output) if dataset_ready else None,
                    "error": (
                        dataset_error
                        or latest_jobs.get(recording_id, {}).get("error")
                        or (capture_failure if capture_failed else None)
                        if state == "failed"
                        else None
                    ),
                    "local_render_command": (
                        f"mc-recorder render {session_id} --player {player_uuid} "
                        f"--connection {connection_id} --replay /path/to/replay.zip"
                    ),
                }
            )
        return sorted(
            results,
            key=lambda item: (
                str(item.get("player_name") or item["player_uuid"]),
                int(item.get("start_tick") or 0),
            ),
        )

    def generate_job(self, recording_id: str) -> dict[str, Any]:
        row = next((item for item in self.recordings() if item["id"] == recording_id), None)
        if row is None:
            raise RecorderError("recording not found")
        if row["end_sequence"] is None:
            raise RecorderError("active connections cannot be generated")
        if row["state"] != "complete" and not row["can_generate"]:
            raise RecorderError(f"recording is not ready to generate: {row['state']}")
        payload = {
            "recording_id": recording_id,
            "session_id": row["session_id"],
            "player_uuid": row["player_uuid"],
            "connection_id": row["connection_id"],
        }
        with self._state_lock:
            self._recording_phases[recording_id] = (
                "generating"
                if int(row["sealed_through_sequence"]) >= int(row["end_sequence"])
                else "sealing"
            )

        def generate() -> dict[str, Any]:
            try:
                return self._generate_dataset(row)
            finally:
                with self._state_lock:
                    self._recording_phases.pop(recording_id, None)

        try:
            return self.jobs.submit(
                "generate_dataset",
                payload,
                generate,
            )
        except Exception:
            with self._state_lock:
                self._recording_phases.pop(recording_id, None)
            raise

    def _generate_dataset(self, row: dict[str, Any]) -> dict[str, Any]:
        session_id = str(row["session_id"])
        player_uuid = _required_uuid(row["player_uuid"], "player UUID")
        connection_id = _required_uuid(row["connection_id"], "connection UUID")
        end_sequence = int(row["end_sequence"])
        output = self._dataset_output(session_id, player_uuid, connection_id)
        with operation_lock(self.config.paths.runtime, "generate_dataset"):
            if output.exists() or output.is_symlink():
                dataset_status, _ = self._dataset_match(
                    output,
                    session_id=session_id,
                    player_uuid=player_uuid,
                    connection_id=connection_id,
                    start_tick=row.get("start_tick"),
                    end_tick=row.get("end_tick"),
                )
                if dataset_status == "indexing":
                    try:
                        self.dataset_viewer.get_dataset_metadata(self._dataset_id(output))
                    except DatasetViewerError as exc:
                        raise RecorderError(
                            f"refusing to reuse viewer-invalid dataset output: {output}: {exc}"
                        ) from exc
                    self._dataset_cache.pop(output, None)
                    self.dataset_index.request_refresh()
                    dataset_status, _ = self._dataset_match(
                        output,
                        session_id=session_id,
                        player_uuid=player_uuid,
                        connection_id=connection_id,
                        start_tick=row.get("start_tick"),
                        end_tick=row.get("end_tick"),
                    )
                if dataset_status == "matching":
                    return {
                        "output": str(output),
                        "reused": True,
                        "dataset_id": self._dataset_id(output),
                    }
                raise RecorderError(f"refusing to overwrite existing dataset output: {output}")

            if self._sealed_through_sequence(session_id) < end_sequence:
                self._request_seal(row)
            if self._sealed_through_sequence(session_id) < end_sequence:
                raise RecorderError("recorder acknowledged sealing but no sealed epoch covers the disconnect")

            with self._state_lock:
                self._recording_phases[str(row["id"])] = "generating"
            episode = resolve_episode(self.config.paths.captures, session_id)
            result = export_episode(
                episode,
                output,
                players=[player_uuid],
                connections=[connection_id],
                first_tick=row.get("start_tick"),
                last_tick=row.get("end_tick"),
            )
            self._dataset_cache.pop(result.output, None)
            self.dataset_viewer.get_dataset_metadata(self._dataset_id(result.output))
            self.dataset_index.request_refresh()
            return {
                "output": str(result.output),
                "reused": False,
                "dataset_id": self._dataset_id(result.output),
                "sample_count": result.sample_count,
                "state_count": result.state_count,
                "action_count": result.action_count,
            }

    def _request_seal(self, row: dict[str, Any]) -> None:
        capture = self.capture_status()
        if not capture.get("fresh") or capture.get("session_id") != row["session_id"]:
            raise RecorderError("the active epoch cannot be sealed because its recorder heartbeat is unavailable")
        request_id = str(uuid.uuid4())
        request = {
            "schema_version": 1,
            "request_id": request_id,
            "operation": "seal_connection",
            "expected_session_id": row["session_id"],
            "player_uuid": row["player_uuid"],
            "connection_id": row["connection_id"],
            "connection_end_sequence": row["end_sequence"],
            "requested_at_unix_ms": int(time.time() * 1000),
        }
        destination = self.control_root / "requests" / f"{request_id}.json"
        temporary = destination.with_suffix(".json.tmp")
        data = json.dumps(request, indent=2, sort_keys=True) + "\n"
        temporary.write_text(data, encoding="utf-8")
        os.replace(temporary, destination)

        response_path = self.control_root / "responses" / f"{request_id}.json"
        deadline = time.monotonic() + SEAL_RESPONSE_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            response = _read_json_object(response_path, maximum=1024 * 1024)
            if response is not None:
                status = response.get("status")
                if status not in {"complete", "completed", "ok"}:
                    error = response.get("error")
                    if isinstance(error, dict):
                        message = error.get("message") or error.get("code")
                    else:
                        message = error
                    raise RecorderError(str(message or response.get("message") or status))
                if response.get("request_id") != request_id:
                    raise RecorderError("recorder seal response request ID does not match")
                self._validate_seal_response(response, row)
                return
            if not self.capture_status().get("fresh"):
                raise RecorderError("recorder went offline while sealing the active epoch")
            time.sleep(0.1)
        raise RecorderError("timed out waiting for the recorder to seal the active epoch")

    def _sealed_through_sequence(self, session_id: str) -> int:
        try:
            episode = resolve_episode(self.config.paths.captures, session_id)
        except RecorderError:
            return -1
        epochs = episode / "epochs"
        if not epochs.is_dir() or epochs.is_symlink():
            return -1
        candidates: list[tuple[int, Path, dict[str, Any]]] = []
        for child in epochs.iterdir():
            manifest = _read_json_object(child / "manifest.json", maximum=1024 * 1024)
            if manifest is None or manifest.get("sealed") is not True:
                continue
            last = manifest.get("last_sequence")
            if isinstance(last, int) and not isinstance(last, bool):
                candidates.append((last, child, manifest))
        for last, child, manifest in sorted(candidates, reverse=True, key=lambda item: item[0]):
            if manifest.get("session_id") != session_id:
                continue
            if self._verified_epoch_last_sequence(child, manifest) == last:
                return last
        return -1

    def _validate_seal_response(self, response: dict[str, Any], row: dict[str, Any]) -> None:
        expected = {
            "session_id": row["session_id"],
            "player_uuid": row["player_uuid"],
            "connection_id": row["connection_id"],
            "connection_end_sequence": row["end_sequence"],
        }
        for key, value in expected.items():
            if response.get(key) != value:
                raise RecorderError(f"recorder seal response {key} does not match the request")
        epoch_index = response.get("sealed_epoch_index")
        sealed_through = response.get("sealed_through_sequence")
        if (
            not isinstance(epoch_index, int)
            or isinstance(epoch_index, bool)
            or epoch_index < 0
            or not isinstance(sealed_through, int)
            or isinstance(sealed_through, bool)
            or sealed_through < int(row["end_sequence"])
        ):
            raise RecorderError("recorder seal response does not cover the connection end sequence")
        expected_relative = f"epochs/epoch-{epoch_index:06d}/manifest.json"
        if response.get("sealed_manifest") != expected_relative:
            raise RecorderError("recorder seal response manifest path is not canonical")
        episode = resolve_episode(self.config.paths.captures, str(row["session_id"]))
        epoch = episode / "epochs" / f"epoch-{epoch_index:06d}"
        manifest = _read_json_object(epoch / "manifest.json", maximum=1024 * 1024) or {}
        if (
            manifest.get("session_id") != row["session_id"]
            or manifest.get("epoch_index") != epoch_index
            or manifest.get("last_sequence") != sealed_through
            or manifest.get("events_sha256") != response.get("events_sha256")
            or self._verified_epoch_last_sequence(epoch, manifest) != sealed_through
        ):
            raise RecorderError("recorder seal response does not match a verified epoch manifest")

    def _verified_epoch_last_sequence(
        self,
        epoch: Path,
        manifest: dict[str, Any],
    ) -> int | None:
        signature = self._epoch_signature(epoch)
        cached = self._sealed_cache.get(epoch)
        if cached is not None and cached[0] == signature:
            return cached[1]
        info = inspect_epoch(epoch)
        last = manifest.get("last_sequence")
        verified = (
            last
            if info is not None
            and info.status == "sealed"
            and isinstance(last, int)
            and not isinstance(last, bool)
            else None
        )
        self._sealed_cache[epoch] = signature, verified
        return verified

    @staticmethod
    def _epoch_signature(epoch: Path) -> tuple[tuple[object, ...], ...]:
        entries = [epoch, epoch / "manifest.json", epoch / "events.jsonl"]
        try:
            return tuple(
                (
                    entry.name,
                    stat.st_dev,
                    stat.st_ino,
                    stat.st_mode,
                    stat.st_size,
                    stat.st_mtime_ns,
                    stat.st_ctime_ns,
                )
                for entry in entries
                for stat in (entry.lstat(),)
            )
        except OSError:
            return ()

    def _dataset_output(self, session_id: str, player_uuid: str, connection_id: str) -> Path:
        # UUID normalization plus strict session syntax keeps this deterministic name safe.
        _required_uuid(player_uuid, "player UUID")
        _required_uuid(connection_id, "connection UUID")
        if not session_id or session_id.startswith(".") or "/" in session_id or "\\" in session_id:
            raise RecorderError(f"invalid episode id: {session_id!r}")
        return self.config.paths.exports / f"{session_id}-{player_uuid}-{connection_id}.dataset"

    def _dataset_match(
        self,
        path: Path,
        *,
        session_id: str,
        player_uuid: str,
        connection_id: str,
        start_tick: int | None,
        end_tick: int | None,
    ) -> tuple[str, str | None]:
        if not path.exists() and not path.is_symlink():
            return "missing", None
        signature = self._dataset_signature(path)
        cached = self._dataset_cache.get(path)
        if cached is not None and cached[0] == signature:
            verified = cached[1]
            verification_error = cached[2]
        elif not path.is_dir() or path.is_symlink():
            return "conflicting", "dataset output is not a regular non-symlink directory"
        elif self.dataset_viewer.has_cached_verified_index(self._dataset_id(path)):
            verified = True
            verification_error = None
            self._dataset_cache[path] = (signature, True, None)
        else:
            rejection = self.dataset_index.rejection_for(path.name)
            self.dataset_index.request_refresh()
            if rejection is not None:
                return "conflicting", f"dataset output is not viewer-valid: {rejection}"
            return "indexing", None
        if not verified:
            return "conflicting", verification_error
        manifest = _read_json_object(path / "manifest.json") or {}
        selection = manifest.get("selection")
        matches = (
            manifest.get("session_id") == session_id
            and isinstance(selection, dict)
            and selection.get("players") == [player_uuid]
            and selection.get("connections") == [connection_id]
            and selection.get("from_tick") == start_tick
            and selection.get("to_tick") == end_tick
        )
        if not matches:
            return "conflicting", "dataset output exists with a different session or selection"
        return "matching", None

    @staticmethod
    def _dataset_signature(path: Path) -> tuple[tuple[object, ...], ...]:
        try:
            entries = [path, *sorted(path.iterdir(), key=lambda item: item.name)]
            return tuple(
                (
                    entry.name,
                    stat.st_dev,
                    stat.st_ino,
                    stat.st_mode,
                    stat.st_size,
                    stat.st_mtime_ns,
                    stat.st_ctime_ns,
                )
                for entry in entries
                for stat in (entry.lstat(),)
            )
        except OSError:
            return ()

    @staticmethod
    def _recording_id(session_id: str, connection_id: str) -> str:
        return hashlib.sha256(f"recording\0{session_id}\0{connection_id}".encode()).hexdigest()[:24]

    def _dataset_id(self, path: Path) -> str:
        return opaque_dataset_id(self.config.paths.exports, path.name)

    @staticmethod
    def _integer(mapping: dict[str, Any], *keys: str) -> int | None:
        for key in keys:
            value = mapping.get(key)
            if isinstance(value, int) and not isinstance(value, bool):
                return value
        return None
