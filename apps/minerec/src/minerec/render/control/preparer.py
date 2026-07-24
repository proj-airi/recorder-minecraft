from __future__ import annotations

import socket
import uuid
from collections.abc import Callable
from typing import Any, ContextManager

from minerec.config import RecorderConfig, render_queue_database
from minerec.errors import RecorderError
from minerec.processing.artifacts.catalog import (
    ArtifactCatalog,
    ReplayArchiveArtifact,
)
from minerec.processing.capture.episodes import resolve_episode
from minerec.processing.capture.exporter import export_episode
from minerec.processing.capture.snapshot import (
    CaptureSnapshot,
    snapshot_connection,
)
from minerec.processing.dataset.viewer import DatasetViewer, opaque_dataset_id
from minerec.render.control.queue import MAX_LEASE_SECONDS, RenderQueueStore

SnapshotFactory = Callable[..., ContextManager[CaptureSnapshot]]
Exporter = Callable[..., Any]


class RenderPreparer:
    """Build and verify a connection-scoped dataset before GUI rendering."""

    def __init__(
        self,
        config: RecorderConfig,
        *,
        preparer_id: str | None = None,
        queue: RenderQueueStore | None = None,
        artifact_catalog: ArtifactCatalog | None = None,
        dataset_viewer: DatasetViewer | None = None,
        snapshot: SnapshotFactory = snapshot_connection,
        exporter: Exporter = export_episode,
    ) -> None:
        self.config = config
        self.preparer_id = preparer_id or f"{socket.gethostname()}-{uuid.uuid4()}"
        self.queue = queue or RenderQueueStore(render_queue_database(config))
        self.artifact_catalog = artifact_catalog or ArtifactCatalog(
            config.paths.captures,
            config.paths.replays,
        )
        self.dataset_viewer = dataset_viewer or DatasetViewer(
            config.paths.exports,
            config.paths.runtime,
        )
        self.snapshot = snapshot
        self.exporter = exporter

    def dataset_id(self, directory_name: str) -> str:
        return opaque_dataset_id(self.config.paths.exports, directory_name)

    def run_once(self) -> dict[str, Any] | None:
        claim = self.queue.claim_preparation(
            self.preparer_id,
            lease_seconds=MAX_LEASE_SECONDS,
        )
        if claim is None:
            return None
        job = claim["job"]
        job_id = job["id"]
        lease_token = claim["lease_token"]
        try:
            return self._prepare(job, lease_token)
        except Exception as exc:
            return self.queue.fail_preparation(job_id, lease_token, str(exc))

    def _prepare(
        self,
        job: dict[str, Any],
        lease_token: str,
    ) -> dict[str, Any]:
        payload = job.get("payload")
        if not isinstance(payload, dict):
            raise RecorderError("artifact render request payload is invalid")
        artifact = self._resolve_artifact(payload)
        self._heartbeat(job["id"], lease_token, "Flashback replay verified")

        episode = resolve_episode(
            self.config.paths.captures,
            artifact.session_id,
        )
        output = self.config.paths.exports / (f"{artifact.session_id}-{artifact.player_uuid}-{artifact.connection_id}.dataset")
        if output.is_symlink():
            raise RecorderError("prepared dataset output may not be a symlink")

        if not output.exists():
            with self.snapshot(
                episode,
                self.config.paths.runtime,
                player_uuid=artifact.player_uuid,
                connection_id=artifact.connection_id,
            ) as source:
                self._heartbeat(
                    job["id"],
                    lease_token,
                    "Capture prefix snapshot verified",
                )
                self.exporter(
                    source.episode,
                    output,
                    players=[artifact.player_uuid],
                    connections=[artifact.connection_id],
                    first_tick=source.selection_start_tick,
                    last_tick=source.selection_end_tick,
                    source_snapshot=source.provenance,
                )
                selection_start_tick = source.selection_start_tick
                selection_end_tick = source.selection_end_tick
        elif not output.is_dir():
            raise RecorderError("prepared dataset output is not a directory")
        else:
            selection_start_tick = None
            selection_end_tick = None

        self._heartbeat(job["id"], lease_token, "Dataset exported; verifying")
        dataset_id = self.dataset_id(output.name)
        metadata = self.dataset_viewer.get_dataset_metadata(dataset_id)
        if selection_start_tick is None:
            selection_start_tick = metadata.selected_from_tick
            selection_end_tick = metadata.selected_to_tick
        connections = self.dataset_viewer.list_player_connections(dataset_id)
        matching = [connection for connection in connections if connection.player_uuid == artifact.player_uuid and connection.connection_id == artifact.connection_id]
        if (
            metadata.session_id != artifact.session_id
            or not isinstance(selection_start_tick, int)
            or not isinstance(selection_end_tick, int)
            or metadata.selected_from_tick != selection_start_tick
            or metadata.selected_to_tick != selection_end_tick
            or len(matching) != 1
        ):
            raise RecorderError("prepared dataset identity does not match the replay artifact")
        connection = matching[0]
        return self.queue.bind_dataset(
            job["id"],
            lease_token,
            dataset_id=dataset_id,
            start_tick=connection.first_tick,
            end_tick=connection.last_tick,
            selection_start_tick=selection_start_tick,
            selection_end_tick=selection_end_tick,
        )

    def _resolve_artifact(
        self,
        payload: dict[str, Any],
    ) -> ReplayArchiveArtifact:
        artifact_id = payload.get("source_artifact_id")
        result = self.artifact_catalog.scan()
        if result.truncated:
            raise RecorderError("replay artifact catalog is incomplete; resolve catalog issues")
        matches = [artifact for artifact in result.replay_archives if artifact.artifact_id == artifact_id]
        if len(matches) != 1:
            raise RecorderError("source Flashback replay artifact is unavailable")
        artifact = matches[0]
        expected = (
            payload.get("session_id"),
            payload.get("player_uuid"),
            payload.get("connection_id"),
        )
        actual = (
            artifact.session_id,
            artifact.player_uuid,
            artifact.connection_id,
        )
        if expected != actual or artifact.connection_id is None:
            raise RecorderError("source Flashback replay identity does not match the request")
        return artifact

    def _heartbeat(
        self,
        job_id: str,
        lease_token: str,
        message: str,
    ) -> None:
        self.queue.heartbeat_preparation(
            job_id,
            lease_token,
            message=message,
            lease_seconds=MAX_LEASE_SECONDS,
        )


__all__ = ["RenderPreparer"]
