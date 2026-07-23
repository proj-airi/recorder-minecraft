from __future__ import annotations

import fcntl
import json
import os
import secrets
import time
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any, TextIO

from minerec.config import RecorderConfig
from minerec.errors import RecorderError
from minerec.processing.artifacts.catalog import ArtifactCatalog
from minerec.processing.dataset.viewer import DatasetViewer
from minerec.render.control.contract import FULL_CLIENT_PRESENTATION_CONTRACT
from minerec.render.control.queue import RenderQueueStore
from minerec.serve.dashboard.index import DatasetIndex

MIN_RENDER_WIDTH = 160
MAX_RENDER_WIDTH = 3840
MIN_RENDER_HEIGHT = 90
MAX_RENDER_HEIGHT = 2160
MAX_RENDER_PIXELS = 3840 * 2160
RENDER_FPS = 20


def _required_uuid(value: object, label: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError) as exc:
        raise RecorderError(f"invalid {label}: {value!r}") from exc


class DashboardService:
    """Read filesystem artifacts and operate the dataset render queue."""

    def __init__(self, config: RecorderConfig) -> None:
        self.config = config
        self._instance_handle: TextIO | None = self._acquire_instance_lock(config.paths.runtime)
        try:
            self.artifact_catalog = ArtifactCatalog(
                config.paths.captures,
                config.paths.replays,
            )
            self.dataset_viewer = DatasetViewer(
                config.paths.exports,
                config.paths.runtime,
            )
            self.dataset_index = DatasetIndex(self.dataset_viewer)
            self.render_queue = RenderQueueStore(config.paths.runtime / "render-queue.sqlite3")
        except Exception:
            index = getattr(self, "dataset_index", None)
            if index is not None:
                index.close()
            self._release_instance_lock()
            raise
        self.csrf_token = secrets.token_urlsafe(32)
        self._closed = False

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
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
        json.dump(
            {
                "pid": os.getpid(),
                "started_at_unix_ms": int(time.time() * 1000),
            },
            handle,
        )
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

    def status(self) -> dict[str, Any]:
        jobs = self.render_queue.recent(100)
        workers = self.render_queue.workers()
        return {
            "dashboard": {"state": "ready"},
            "render": {
                "active_jobs": sum(job["state"] not in {"complete", "partial", "failed", "canceled"} for job in jobs),
                "online_workers": sum(worker["state"] in {"online", "busy"} for worker in workers),
            },
            "csrf_token": self.csrf_token,
        }

    def artifacts(self) -> dict[str, Any]:
        filesystem = self.artifact_catalog.scan().as_json()
        datasets, indexing, error, refreshed_at = self.dataset_index.snapshot()
        self.dataset_index.request_refresh()
        return {
            **filesystem,
            "datasets": [{**asdict(dataset), "id": dataset.dataset_id, "status": "verified"} for dataset in datasets.datasets],
            "rejected_datasets": [asdict(issue) for issue in datasets.rejected],
            "datasets_indexing": indexing,
            "datasets_error": error,
            "datasets_refreshed_at_unix": refreshed_at,
        }

    def create_render_job(
        self,
        dataset_id: str,
        *,
        player_uuid: str | None = None,
        connection_id: str | None = None,
        width: int = 640,
        height: int = 360,
        fps: int = RENDER_FPS,
        no_gui: bool = False,
        _retry_of: str | None = None,
    ) -> dict[str, Any]:
        self._validate_render_settings(width, height, fps, no_gui)
        metadata = self.dataset_viewer.get_dataset_metadata(dataset_id)
        connections = self.dataset_viewer.list_player_connections(dataset_id)
        if player_uuid is not None:
            canonical_player = _required_uuid(player_uuid, "player UUID")
            connections = tuple(item for item in connections if item.player_uuid == canonical_player)
        if connection_id is not None:
            canonical_connection = _required_uuid(connection_id, "connection UUID")
            connections = tuple(item for item in connections if item.connection_id == canonical_connection)
        if not connections:
            raise RecorderError("dataset has no matching player connection")
        if len(connections) != 1:
            raise RecorderError("dataset has multiple player connections; select one explicitly")
        connection = connections[0]
        replay_catalog = self.artifact_catalog.scan()
        if replay_catalog.truncated:
            raise RecorderError("replay artifact catalog is incomplete; resolve catalog issues before rendering")
        matching_replays = [replay for replay in replay_catalog.replay_archives if replay.session_id == metadata.session_id and replay.player_uuid == connection.player_uuid and replay.connection_id == connection.connection_id]
        if not matching_replays:
            raise RecorderError("dataset connection has no matching saved Flashback replay")
        if metadata.rgb_samples >= metadata.sample_count and metadata.sample_count:
            raise RecorderError("dataset already has complete RGB coverage")
        payload = self._render_job_payload(
            dataset_id=dataset_id,
            session_id=metadata.session_id,
            player_uuid=connection.player_uuid,
            connection_id=connection.connection_id,
            start_tick=connection.first_tick,
            end_tick=connection.last_tick,
            width=width,
            height=height,
            fps=fps,
            no_gui=no_gui,
        )
        return self.render_queue.create(payload, retry_of=_retry_of)

    @classmethod
    def _render_job_payload(
        cls,
        *,
        dataset_id: str,
        session_id: str,
        player_uuid: str,
        connection_id: str,
        start_tick: int,
        end_tick: int,
        width: int,
        height: int,
        fps: int,
        no_gui: bool,
    ) -> dict[str, Any]:
        cls._validate_render_settings(width, height, fps, no_gui)
        render: dict[str, Any] = {
            "width": width,
            "height": height,
            "fps": fps,
            "no_gui": no_gui,
        }
        if not no_gui:
            render["presentation_contract"] = FULL_CLIENT_PRESENTATION_CONTRACT
        return {
            "dataset_id": dataset_id,
            "session_id": session_id,
            "player_uuid": _required_uuid(player_uuid, "player UUID"),
            "connection_id": _required_uuid(connection_id, "connection UUID"),
            "start_tick": start_tick,
            "end_tick": end_tick,
            "selection_start_tick": start_tick,
            "selection_end_tick": end_tick,
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
        payload = original["payload"]
        return self.create_render_job(
            original["dataset_id"],
            player_uuid=payload["player_uuid"],
            connection_id=payload["connection_id"],
            width=payload["render"]["width"],
            height=payload["render"]["height"],
            fps=payload["render"]["fps"],
            no_gui=payload["render"].get("no_gui", False),
            _retry_of=original["id"],
        )

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
            raise RecorderError(f"render resolution may not exceed {MAX_RENDER_PIXELS} pixels")
        if fps != RENDER_FPS:
            raise RecorderError(f"render fps must be exactly {RENDER_FPS} for tick alignment")
        if not isinstance(no_gui, bool):
            raise RecorderError("render no_gui must be a boolean")
