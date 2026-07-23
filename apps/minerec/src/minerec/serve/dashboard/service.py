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
from typing import Any, TextIO, cast

from minerec.config import RecorderConfig
from minerec.errors import RecorderError
from minerec.operations import operation_lock
from minerec.processing.capture.episodes import directory_size, inspect_epoch, resolve_episode
from minerec.processing.capture.exporter import export_episode
from minerec.processing.capture.storage import pin_sealed_epochs
from minerec.processing.dataset.viewer import DatasetViewer, DatasetViewerError, opaque_dataset_id
from minerec.processing.scene.job import (
    SceneJob,
    cleanup_scene_job,
    cleanup_stale_scene_jobs,
    launch_scene_job,
    prepare_scene_job,
)
from minerec.processing.scene.store import compact_scene_stream, validate_scene_store
from minerec.render.control.contract import FULL_CLIENT_PRESENTATION_CONTRACT
from minerec.render.control.queue import RenderQueueStore
from minerec.render.control.sources import ReplayNotReadyError
from minerec.serve.dashboard.index import DatasetIndex
from minerec.serve.dashboard.jobs import JobManager, JobStore

MAX_CONTROL_JSON_BYTES = 8 * 1024 * 1024
HEARTBEAT_STALE_SECONDS = 5.0
MIN_RENDER_WIDTH = 160
MAX_RENDER_WIDTH = 3840
MIN_RENDER_HEIGHT = 90
MAX_RENDER_HEIGHT = 2160
MAX_RENDER_PIXELS = 3840 * 2160
RENDER_FPS = 20
REPLAY_READY_TIMEOUT_SECONDS = 300.0
STALE_RGB_PRESENTATIONS = frozenset({"legacy_gui_unsynchronized", "mixed_legacy_gui_unsynchronized"})


def _read_json_object(path: Path, *, maximum: int = MAX_CONTROL_JSON_BYTES) -> dict[str, Any] | None:
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > maximum:
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
    except OSError, json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _required_uuid(value: object, label: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError) as exc:
        raise RecorderError(f"invalid {label}: {value!r}") from exc


class DashboardService:
    def __init__(self, config: RecorderConfig) -> None:
        self.config = config
        self.control_root = config.paths.runtime / "control"
        self.control_root.mkdir(parents=True, exist_ok=True)
        self._instance_handle: TextIO | None = self._acquire_instance_lock(config.paths.runtime)
        try:
            self.dataset_viewer = DatasetViewer(config.paths.exports, config.paths.runtime)
            self.dataset_index = DatasetIndex(self.dataset_viewer)
            self.jobs = JobManager(JobStore(config.paths.runtime / "dashboard.sqlite3"))
            self.render_queue = RenderQueueStore(config.paths.runtime / "render-queue.sqlite3")
        except Exception:
            jobs = getattr(self, "jobs", None)
            if jobs is not None:
                jobs.close()
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
        epoch = cast(dict[str, Any], value.get("epoch")) if isinstance(value.get("epoch"), dict) else {}
        writer = cast(dict[str, Any], value.get("writer")) if isinstance(value.get("writer"), dict) else {}
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
        value = {
            "state": "external",
            "services": [],
            "message": "Minecraft server lifecycle is managed by Docker Compose outside minerec",
        }
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
        raise RecorderError("Minecraft server lifecycle is managed by Docker Compose; use hack/minecraft-server start")

    def _stop_server(self) -> dict[str, Any]:
        self._invalidate_host_status()
        raise RecorderError("Minecraft server lifecycle is managed by Docker Compose; use hack/minecraft-server stop")

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
                candidates = [cast(dict[str, Any], value) for value in raw_rows.values() if isinstance(value, dict)]
            elif isinstance(raw_rows, list):
                candidates = [cast(dict[str, Any], value) for value in raw_rows if isinstance(value, dict)]
            else:
                candidates = []
            for raw in candidates:
                row = {**raw, **({"session_id": session_id} if "session_id" not in raw else {})}
                identity = row.get("session_id"), row.get("connection_id")
                if all(isinstance(value, str) and value for value in identity):
                    rows_by_identity[cast(tuple[str, str], identity)] = row
        return list(rows_by_identity.values())

    def recordings(self) -> list[dict[str, Any]]:
        capture = self.capture_status()
        current_session = capture.get("session_id")
        fresh = bool(capture.get("fresh"))
        capture_state = capture.get("state")
        capture_failure = capture.get("failure_reason")
        recent_jobs = self.jobs.store.recent(100)
        active_jobs = {job["payload"].get("recording_id"): job for job in recent_jobs if job["state"] in {"queued", "running"} and job["kind"] == "generate_dataset"}
        latest_jobs: dict[str, dict[str, Any]] = {}
        for job in recent_jobs:
            recording_id = job["payload"].get("recording_id")
            if job["kind"] == "generate_dataset" and isinstance(recording_id, str):
                latest_jobs.setdefault(recording_id, job)
        connection_rows = self._connection_rows()
        session_ids = {row["session_id"] for row in connection_rows if isinstance(row.get("session_id"), str)}
        sliced_by_session = {session_id: self._sliced_through_sequence(session_id) for session_id in session_ids}
        results: list[dict[str, Any]] = []
        for raw in connection_rows:
            session_id = raw.get("session_id")
            player_uuid = raw.get("player_uuid")
            connection_id = raw.get("connection_id")
            if not all(isinstance(value, str) and value for value in (session_id, player_uuid, connection_id)):
                continue
            recording_id = self._recording_id(session_id, connection_id)  # ty:ignore[invalid-argument-type]
            end_sequence = self._integer(raw, "end_sequence", "connection_end_sequence")
            start_tick = self._integer(
                raw,
                "join_server_tick",
                "start_server_tick",
                "start_tick",
                "connection_start_server_tick",
            )
            end_tick = self._integer(raw, "end_server_tick", "end_tick", "connection_end_server_tick")
            output = self._dataset_output(session_id, player_uuid, connection_id)  # ty:ignore[invalid-argument-type]
            dataset_status, dataset_error = self._dataset_match(
                output,
                session_id=session_id,  # ty:ignore[invalid-argument-type]
                player_uuid=player_uuid,  # ty:ignore[invalid-argument-type]
                connection_id=connection_id,  # ty:ignore[invalid-argument-type]
                start_tick=start_tick,
                end_tick=end_tick,
            )
            dataset_ready = dataset_status == "matching"
            render_job = self.render_queue.latest_for_recording(recording_id)
            sample_count = rgb_samples = None
            rgb_presentation = None
            sample_start_tick = sample_end_tick = None
            if dataset_ready:
                metadata = self.dataset_viewer.get_dataset_metadata(self._dataset_id(output))
                sample_count = metadata.sample_count
                rgb_samples = metadata.rgb_samples
                rgb_presentation = metadata.rgb_presentation
                sample_start_tick = metadata.first_tick
                sample_end_tick = metadata.last_tick
            rgb_coverage_complete = sample_count is not None and rgb_samples is not None and sample_count > 0 and rgb_samples >= sample_count
            legacy_rgb_complete = rgb_coverage_complete and rgb_presentation in STALE_RGB_PRESENTATIONS
            rgb_complete = rgb_coverage_complete and not legacy_rgb_complete
            sliced_sequence = sliced_by_session.get(session_id, -1)
            current_capture = session_id == current_session
            capture_recording = fresh and current_capture and capture_state == "recording"
            capture_failed = current_capture and capture_state == "failed"
            if dataset_ready:
                state = "complete"
            elif recording_id in active_jobs:
                with self._state_lock:
                    state = self._recording_phases.get(recording_id, "slicing")
            elif dataset_status == "conflicting":
                state = "failed"
            elif latest_jobs.get(recording_id, {}).get("state") == "failed" and end_sequence is not None and (sliced_sequence >= end_sequence or capture_recording):
                state = "failed"
            elif dataset_status == "indexing":
                state = "generating"
            elif end_sequence is None:
                if capture_failed:
                    state = "failed"
                else:
                    state = "recording" if capture_recording else "interrupted"
            elif sliced_sequence >= end_sequence:
                state = "disconnected"
            elif capture_failed:
                state = "failed"
            elif capture_recording:
                state = "waiting_for_slice"
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
                    "sliced_through_sequence": sliced_sequence,
                    "state": state,
                    "can_generate": end_sequence is not None and state in {"disconnected", "waiting_for_slice", "failed"} and dataset_status != "conflicting" and (sliced_sequence >= end_sequence or capture_recording),
                    "dataset_id": self._dataset_id(output) if dataset_ready else None,
                    "sample_count": sample_count,
                    "sample_start_tick": sample_start_tick,
                    "sample_end_tick": sample_end_tick,
                    "rgb_samples": rgb_samples,
                    "rgb_coverage_complete": rgb_coverage_complete,
                    "rgb_complete": rgb_complete,
                    "rgb_presentation": rgb_presentation,
                    "can_replace_legacy_rgb": legacy_rgb_complete,
                    "can_render": dataset_ready
                    and not rgb_complete
                    and sample_start_tick is not None
                    and sample_end_tick is not None
                    and (render_job is None or render_job["state"] in {"failed", "partial", "canceled"} or (legacy_rgb_complete and render_job["state"] == "complete")),
                    "render_job": render_job,
                    "error": (dataset_error or latest_jobs.get(recording_id, {}).get("error") or (capture_failure if capture_failed else None) if state == "failed" else None),
                    "local_render_command": (f"minerec render {session_id} --player {player_uuid} --connection {connection_id} --replay /path/to/replay.zip"),
                }
            )
        return sorted(
            results,
            key=lambda item: (
                str(item.get("player_name") or item["player_uuid"]),
                int(item.get("start_tick") or 0),
            ),
        )

    def create_render_job(
        self,
        recording_id: str,
        *,
        width: int = 640,
        height: int = 360,
        fps: int = RENDER_FPS,
        no_gui: bool = False,
        replace_legacy_rgb: bool = False,
    ) -> dict[str, Any]:
        self._validate_render_settings(width, height, fps, no_gui)
        if not isinstance(replace_legacy_rgb, bool):
            raise RecorderError("replace_legacy_rgb must be a boolean")
        row = next((item for item in self.recordings() if item["id"] == recording_id), None)
        if row is None:
            raise RecorderError("recording not found")
        if row["state"] != "complete" or not row.get("dataset_id"):
            raise RecorderError("a structured dataset must be complete before RGB rendering")
        coverage_complete, legacy_rgb_complete = self._rgb_coverage_state(row)
        if replace_legacy_rgb:
            if not legacy_rgb_complete:
                raise RecorderError("replace_legacy_rgb is only allowed for fully covered legacy GUI RGB")
            if no_gui:
                raise RecorderError("legacy GUI RGB can only be replaced by a full-client render")
        elif legacy_rgb_complete:
            raise RecorderError("the dataset has legacy unsynchronized GUI RGB; set replace_legacy_rgb=true to re-render it")
        elif coverage_complete:
            raise RecorderError("the dataset already has complete RGB coverage")
        payload = self._render_job_payload(
            row,
            width=width,
            height=height,
            fps=fps,
            no_gui=no_gui,
        )
        return self.render_queue.create(payload)

    def _render_job_payload(
        self,
        row: dict[str, Any],
        *,
        width: int,
        height: int,
        fps: int,
        no_gui: bool,
    ) -> dict[str, Any]:
        self._validate_render_settings(width, height, fps, no_gui)
        selection_start = row.get("start_tick")
        selection_end = row.get("end_tick")
        render_start = row.get("sample_start_tick")
        render_end = row.get("sample_end_tick")
        ticks = (selection_start, selection_end, render_start, render_end)
        if any(not isinstance(value, int) or isinstance(value, bool) or value < 0 for value in ticks):
            raise RecorderError("the dataset has no bounded renderable sample tick range")
        assert isinstance(selection_start, int)
        assert isinstance(selection_end, int)
        assert isinstance(render_start, int)
        assert isinstance(render_end, int)
        if not selection_start <= render_start <= render_end <= selection_end:
            raise RecorderError("the dataset sample tick range is outside its connection selection")
        render: dict[str, Any] = {
            "width": width,
            "height": height,
            "fps": fps,
            "no_gui": no_gui,
        }
        if not no_gui:
            render["presentation_contract"] = FULL_CLIENT_PRESENTATION_CONTRACT
        return {
            "recording_id": row["id"],
            "session_id": row["session_id"],
            "player_uuid": _required_uuid(row["player_uuid"], "player UUID"),
            "connection_id": _required_uuid(row["connection_id"], "connection UUID"),
            "dataset_id": row["dataset_id"],
            "start_tick": render_start,
            "end_tick": render_end,
            "selection_start_tick": selection_start,
            "selection_end_tick": selection_end,
            "render": render,
        }

    def render_jobs(self, limit: int = 50) -> list[dict[str, Any]]:
        return self.render_queue.recent(limit)

    def render_workers(self) -> list[dict[str, Any]]:
        return self.render_queue.workers()

    def cancel_render_job(self, job_id: str) -> dict[str, Any]:
        return self.render_queue.cancel(job_id)

    def retry_render_job(self, job_id: str) -> dict[str, Any]:
        original = self.render_queue.get(job_id)
        if original["state"] not in {"failed", "partial", "canceled"}:
            raise RecorderError(f"render job cannot be retried while {original['state']}")
        recording_id = original["recording_id"]
        row = next((item for item in self.recordings() if item["id"] == recording_id), None)
        if row is None or row["state"] != "complete" or row.get("dataset_id") != original["payload"].get("dataset_id"):
            raise RecorderError("the source dataset is no longer available for this render job")
        coverage_complete, legacy_rgb_complete = self._rgb_coverage_state(row)
        if legacy_rgb_complete:
            raise RecorderError("legacy GUI RGB must be explicitly replaced with a new render request")
        if coverage_complete:
            raise RecorderError("the dataset already has complete RGB coverage")
        render = original["payload"].get("render")
        if not isinstance(render, dict):
            raise RecorderError("the original render job has invalid settings")
        payload = self._render_job_payload(
            row,
            width=render.get("width"),
            height=render.get("height"),
            fps=render.get("fps"),
            no_gui=render.get("no_gui", True),
        )
        return self.render_queue.create(payload, retry_of=original["id"])

    @staticmethod
    def _rgb_coverage_state(row: dict[str, Any]) -> tuple[bool, bool]:
        raw_coverage = row.get("rgb_coverage_complete")
        coverage_complete = raw_coverage if isinstance(raw_coverage, bool) else bool(row.get("rgb_complete"))
        legacy_rgb_complete = coverage_complete and row.get("rgb_presentation") in STALE_RGB_PRESENTATIONS
        return coverage_complete, legacy_rgb_complete

    def register_render_worker(
        self,
        name: str,
        *,
        worker_id: str | None = None,
        capabilities: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.render_queue.register_worker(
            name,
            worker_id=worker_id,
            capabilities=capabilities,
        )

    def claim_render_job(
        self,
        worker_id: str,
        *,
        job_id: str | None = None,
        lease_seconds: int = 60,
    ) -> dict[str, Any] | None:
        return self.render_queue.claim(
            worker_id,
            job_id=job_id,
            lease_seconds=lease_seconds,
        )

    def heartbeat_render_job(
        self,
        worker_id: str,
        attempt_id: str,
        lease_token: str,
        *,
        phase: str,
        current: int | None = None,
        total: int | None = None,
        message: str | None = None,
        lease_seconds: int = 60,
    ) -> dict[str, Any]:
        return self.render_queue.heartbeat(
            worker_id,
            attempt_id,
            lease_token,
            phase=phase,
            current=current,
            total=total,
            message=message,
            lease_seconds=lease_seconds,
        )

    def mark_render_uploaded(
        self,
        worker_id: str,
        attempt_id: str,
        lease_token: str,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        return self.render_queue.mark_uploaded(worker_id, attempt_id, lease_token, result)

    def fail_render_attempt(
        self,
        worker_id: str,
        attempt_id: str,
        lease_token: str,
        error: str,
    ) -> dict[str, Any]:
        return self.render_queue.fail_attempt(worker_id, attempt_id, lease_token, error)

    @staticmethod
    def _validate_render_settings(
        width: object,
        height: object,
        fps: object,
        no_gui: object,
    ) -> None:
        if not isinstance(width, int) or isinstance(width, bool) or not MIN_RENDER_WIDTH <= width <= MAX_RENDER_WIDTH:
            raise RecorderError(f"render width must be between {MIN_RENDER_WIDTH} and {MAX_RENDER_WIDTH}")
        if not isinstance(height, int) or isinstance(height, bool) or not MIN_RENDER_HEIGHT <= height <= MAX_RENDER_HEIGHT:
            raise RecorderError(f"render height must be between {MIN_RENDER_HEIGHT} and {MAX_RENDER_HEIGHT}")
        if width * height > MAX_RENDER_PIXELS:
            raise RecorderError("render resolution exceeds the maximum pixel count")
        if not isinstance(fps, int) or isinstance(fps, bool) or fps != RENDER_FPS:
            raise RecorderError(f"render fps must be {RENDER_FPS}")
        if not isinstance(no_gui, bool):
            raise RecorderError("render no_gui must be a boolean")

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
            self._recording_phases[recording_id] = "generating" if int(row["sliced_through_sequence"]) >= int(row["end_sequence"]) else "slicing"

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
                        raise RecorderError(f"refusing to reuse viewer-invalid dataset output: {output}: {exc}") from exc
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

            if self._sliced_through_sequence(session_id) < end_sequence:
                raise RecorderError("recording slice is not ready for the disconnected connection")

            with self._state_lock:
                self._recording_phases[str(row["id"])] = "waiting_replay"
            episode = resolve_episode(self.config.paths.captures, session_id)
            # Retention takes an exclusive lock on these same manifest files.
            # Keep the exact canonical epochs alive from replay wait through
            # the final atomic dataset publication.
            with pin_sealed_epochs(episode) as pinned_epochs:
                return self._extract_and_publish_dataset(
                    row,
                    episode=episode,
                    output=output,
                    player_uuid=player_uuid,
                    connection_id=connection_id,
                    pinned_epoch_paths=pinned_epochs,
                )

    def _extract_and_publish_dataset(
        self,
        row: dict[str, Any],
        *,
        episode: Path,
        output: Path,
        player_uuid: str,
        connection_id: str,
        pinned_epoch_paths: tuple[Path, ...],
    ) -> dict[str, Any]:
        cleanup_stale_scene_jobs(self.config.paths.runtime, keep=1)
        deadline = time.monotonic() + REPLAY_READY_TIMEOUT_SECONDS
        while True:
            try:
                scene_job = prepare_scene_job(
                    self.config,
                    episode,
                    player_uuid=player_uuid,
                    connection_id=connection_id,
                    first_tick=row.get("start_tick"),
                    last_tick=row.get("end_tick"),
                    pinned_epoch_paths=pinned_epoch_paths,
                )
                break
            except ReplayNotReadyError:
                if time.monotonic() >= deadline:
                    raise RecorderError("timed out waiting for the connection replay to become immutable")
                time.sleep(1.0)
        try:
            result = self._publish_prepared_scene_job(
                row,
                episode=episode,
                output=output,
                player_uuid=player_uuid,
                connection_id=connection_id,
                scene_job=scene_job,
            )
        except BaseException:
            # Keep only the newest failed job for bounded diagnostics; older
            # crash/failure intermediates are marker-checked before deletion.
            cleanup_stale_scene_jobs(self.config.paths.runtime, keep=1)
            raise
        # The published dataset contains its own verified scene-store copy.
        cleanup_scene_job(scene_job)
        return result

    def _publish_prepared_scene_job(
        self,
        row: dict[str, Any],
        *,
        episode: Path,
        output: Path,
        player_uuid: str,
        connection_id: str,
        scene_job: SceneJob,
    ) -> dict[str, Any]:
        with self._state_lock:
            self._recording_phases[str(row["id"])] = "extracting"
        verified_stream = launch_scene_job(self.config, scene_job, capture_output=True)
        with self._state_lock:
            self._recording_phases[str(row["id"])] = "compacting"
        scene_store = scene_job.directory / "scene-v1.sqlite3"
        compact_scene_stream(
            scene_job.stream,
            scene_store,
            expected_session_id=scene_job.session_id,
            expected_player_uuid=scene_job.player_uuid,
            expected_connection_id=scene_job.connection_id,
            expected_ticks=scene_job.state_ticks,
            verified_stream=verified_stream,
        )
        with self._state_lock:
            self._recording_phases[str(row["id"])] = "verifying"
        validate_scene_store(
            scene_store,
            expected_session_id=scene_job.session_id,
            expected_player_uuid=scene_job.player_uuid,
            expected_connection_id=scene_job.connection_id,
            expected_ticks=scene_job.state_ticks,
        )
        with self._state_lock:
            self._recording_phases[str(row["id"])] = "attaching"
        result = export_episode(
            episode,
            output,
            players=[player_uuid],
            connections=[connection_id],
            first_tick=row.get("start_tick"),
            last_tick=row.get("end_tick"),
            scenes=[scene_store],
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
            "scene_count": result.scene_count,
        }

    def _sliced_through_sequence(self, session_id: str) -> int:
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
        verified = last if info is not None and info.status == "sealed" and isinstance(last, int) and not isinstance(last, bool) else None
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
