from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import socket
import tempfile
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

from minerec.config import RecorderConfig, load_runtime_env
from minerec.errors import RecorderError
from minerec.processing.render.hud import MAX_HUD_SIDECAR_BYTES, validate_hud_sidecar_envelope
from minerec.processing.render.job import launch_render_job, prepare_renderer_runtime
from minerec.render.control.contract import (
    FULL_CLIENT_PRESENTATION_CAPABILITY_KEY,
    FULL_CLIENT_PRESENTATION_CONTRACT,
)
from minerec.render.control.transfer import (
    create_render_bundle,
    materialize_portable_render_job,
    write_portable_render_request,
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_WORKSPACE_MARKER = ".mc-recorder-ephemeral-worker.json"
_WORKER_HEARTBEAT_SECONDS = 20.0
_LEASE_SECONDS = 120
_STRUCTURED_CLAIM_FAILURE_CAPABILITY = "structured_claim_failure"


@dataclass(frozen=True)
class LocalRecorder:
    root: Path
    config: RecorderConfig | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "root", self.root.expanduser().resolve())
        if self.root.is_symlink() or not self.root.exists() or not self.root.is_dir():
            raise RecorderError(f"local recorder root is not a directory: {self.root}")

    def contains(self, path: str | Path) -> Path:
        candidate = Path(path).expanduser()
        if not candidate.is_absolute():
            raise RecorderError("local transfer path must be absolute")
        resolved = candidate.resolve()
        try:
            resolved.relative_to(self.root)
        except ValueError as exc:
            raise RecorderError("local transfer path escapes the recorder root") from exc
        if resolved.is_symlink():
            raise RecorderError(f"local transfer path may not be a symlink: {resolved}")
        return resolved


@dataclass(frozen=True)
class _WorkerPoll:
    result: dict[str, Any] | None
    reason: str | None = None
    deferred_job_cooldown_seconds: int | None = None


class _ClaimedJobError(RecorderError):
    """A claimed job failed; stop rather than damaging later queued jobs."""


class _ClaimOutcomeUnknownError(_ClaimedJobError):
    """The worker cannot know whether the server claimed the selected job."""


class _IncompatibleServerError(RecorderError):
    """The recorder runtime cannot safely support this render worker."""


def requeue_render_task_error(error: Exception) -> bool:
    return not isinstance(error, _ClaimedJobError) or isinstance(error, _ClaimOutcomeUnknownError)


def call_recorder_json(
    endpoint: LocalRecorder,
    arguments: Sequence[str],
    *,
    body: dict[str, Any] | None = None,
    timeout_seconds: int = 60,
) -> dict[str, Any]:
    del timeout_seconds
    if endpoint.config is None:
        raise RecorderError("local render RPC requires a recorder config")
    if len(arguments) != 2 or arguments[0] != "render-rpc":
        raise RecorderError("local render RPC only supports render-rpc actions")
    from minerec.processing.render.attach import attach_imported_renders
    from minerec.render.control.queue import RenderQueueStore
    from minerec.render.control.rpc import dispatch_render_rpc

    result = dispatch_render_rpc(endpoint.config, arguments[1], body or {})
    if arguments[1] != "finalize":
        return result
    job = result.get("job")
    imports = result.get("imports")
    if not isinstance(job, dict) or not isinstance(imports, list):
        raise RecorderError("render finalization lacks its queue job or canonical imports")
    state = job.get("state")
    if state in {"complete", "partial"}:
        return result
    if state not in {"verifying", "attaching"}:
        raise RecorderError(f"render finalization cannot attach while job is {state!r}")
    job_id = job.get("id")
    if not isinstance(job_id, str):
        raise RecorderError("render finalization lacks its queue job ID")
    queue = RenderQueueStore(endpoint.config.paths.runtime / "render-queue.sqlite3")
    if state == "verifying":
        job = queue.set_server_phase(
            job_id,
            "attaching",
            progress={"message": "Attaching verified RGB to the dataset"},
        )
    try:
        attachment = attach_imported_renders(endpoint.config, job, imports)
        attachment_json = attachment.as_json()
        completed = queue.complete(job_id, attachment_json, partial=attachment.partial)
    except Exception as exc:
        try:
            queue.fail(job_id, str(exc))
        except Exception:
            pass
        if isinstance(exc, RecorderError):
            raise
        raise RecorderError(f"could not attach the imported RGB dataset: {exc}") from exc
    return {**result, "job": completed, "attachment": attachment_json}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_cache_root(path: Path) -> Path:
    requested = path.expanduser()
    if requested.is_symlink():
        raise RecorderError(f"render cache root may not be a symlink: {requested}")
    requested.mkdir(parents=True, exist_ok=True)
    resolved = requested.resolve()
    if not resolved.is_dir():
        raise RecorderError(f"render cache root is not a directory: {resolved}")
    return resolved


def _verified_replay(path: Path, expected_sha256: str, expected_size: int) -> bool:
    if path.is_symlink() or not path.is_file():
        return False
    before = path.stat()
    if before.st_size != expected_size:
        return False
    actual = _sha256(path)
    after = path.stat()
    return actual == expected_sha256 and (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)


def _cache_verified_local_file(
    source: Path,
    *,
    expected_sha256: str,
    expected_size: int,
    cache_root: Path,
    cache_subdir: str,
    suffix: str,
) -> Path:
    if not source.is_file():
        raise RecorderError(f"local render source is not a file: {source}")
    root = _safe_cache_root(cache_root) / cache_subdir
    root.mkdir(mode=0o700, exist_ok=True)
    destination = root / f"{expected_sha256}{suffix}"
    if _verified_replay(destination, expected_sha256, expected_size):
        return destination
    if destination.exists() or destination.is_symlink():
        if destination.is_dir():
            raise RecorderError(f"render cache entry is not a file: {destination}")
        destination.unlink()
    if not _verified_replay(source, expected_sha256, expected_size):
        raise RecorderError("local render source does not match its SHA-256 and size")
    try:
        os.link(source, destination)
    except OSError:
        shutil.copy2(source, destination)
    if not _verified_replay(destination, expected_sha256, expected_size):
        destination.unlink(missing_ok=True)
        raise RecorderError("cached local render source does not match its SHA-256 and size")
    return destination


def download_replay(
    remote: LocalRecorder,
    *,
    remote_path: str,
    expected_sha256: str,
    expected_size: int,
    cache_root: Path,
) -> Path:
    digest = expected_sha256.lower()
    if _SHA256.fullmatch(digest) is None:
        raise RecorderError("render source has an invalid SHA-256")
    if expected_size <= 0 or expected_size > 2**63 - 1:
        raise RecorderError("render source has an invalid byte size")
    return _cache_verified_local_file(
        remote.contains(remote_path),
        expected_sha256=digest,
        expected_size=expected_size,
        cache_root=cache_root,
        cache_subdir="replays",
        suffix=".zip",
    )


def download_hud_sidecar(
    remote: LocalRecorder,
    *,
    remote_path: str,
    expected_sha256: str,
    expected_size: int,
    cache_root: Path,
) -> Path:
    digest = expected_sha256.lower()
    if _SHA256.fullmatch(digest) is None:
        raise RecorderError("structured HUD sidecar has an invalid SHA-256")
    if expected_size <= 0 or expected_size > MAX_HUD_SIDECAR_BYTES:
        raise RecorderError("structured HUD sidecar has an invalid byte size")
    return _cache_verified_local_file(
        remote.contains(remote_path),
        expected_sha256=digest,
        expected_size=expected_size,
        cache_root=cache_root,
        cache_subdir="structured-hud",
        suffix=".jsonl",
    )


def upload_bundle(
    remote: LocalRecorder,
    *,
    local_directory: Path,
    remote_directory: str,
) -> None:
    source = local_directory.expanduser().resolve()
    if source.is_symlink() or not source.is_dir():
        raise RecorderError(f"render bundle is not a regular directory: {source}")
    destination = remote.contains(remote_directory)
    if destination.exists() and not destination.is_dir():
        raise RecorderError(f"local render upload destination is not a directory: {destination}")
    destination.mkdir(parents=True, exist_ok=True)
    for child in source.iterdir():
        target = destination / child.name
        if child.is_dir():
            shutil.copytree(child, target, dirs_exist_ok=True)
        elif child.is_file() and not child.is_symlink():
            shutil.copy2(child, target)
        else:
            raise RecorderError(f"render bundle contains an unsafe entry: {child}")


def ephemeral_worker_id() -> str:
    return str(uuid.uuid4())


def default_worker_cache() -> Path:
    return load_runtime_env().worker_cache_root


def create_job_workspace(cache_root: Path, job_id: str, attempt_id: str) -> Path:
    canonical_job = _canonical_uuid(job_id, "render job ID")
    canonical_attempt = _canonical_uuid(attempt_id, "render attempt ID")
    root = _safe_cache_root(cache_root) / "jobs"
    root.mkdir(mode=0o700, exist_ok=True)
    workspace = Path(tempfile.mkdtemp(prefix=f"{canonical_job}-", dir=root))
    marker = {
        "owner": "mc-recorder",
        "kind": "ephemeral-render-workspace",
        "job_id": canonical_job,
        "attempt_id": canonical_attempt,
    }
    (workspace / _WORKSPACE_MARKER).write_text(
        json.dumps(marker, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    return workspace


def remove_job_workspace(path: Path) -> None:
    requested = path.expanduser()
    if requested.is_symlink() or not requested.exists():
        return
    if not requested.is_dir():
        raise RecorderError(f"render job workspace is not a directory: {requested}")
    marker = requested / _WORKSPACE_MARKER
    try:
        value = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RecorderError(f"refusing to remove an unowned render workspace: {requested}") from exc
    if not isinstance(value, dict) or value.get("owner") != "mc-recorder" or value.get("kind") != "ephemeral-render-workspace" or requested.parent.name != "jobs":
        raise RecorderError(f"refusing to remove an unowned render workspace: {requested}")
    shutil.rmtree(requested)


def _canonical_uuid(value: object, label: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError) as exc:
        raise RecorderError(f"invalid {label}: {value!r}") from exc


def _bounded_string(value: object, label: str, maximum: int = 2048) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum or any(ord(character) < 32 for character in value):
        raise RecorderError(f"render response has an invalid {label}")
    return value


class _RemoteLease:
    def __init__(
        self,
        remote: LocalRecorder,
        *,
        worker_id: str,
        attempt_id: str,
        lease_token: str,
    ) -> None:
        self.remote = remote
        self.worker_id = _canonical_uuid(worker_id, "render worker ID")
        self.attempt_id = _canonical_uuid(attempt_id, "render attempt ID")
        self.lease_token = _bounded_string(lease_token, "render lease token", 256)
        self._phase = "downloading"
        self._message: str | None = None
        self._state_lock = threading.Lock()
        self._rpc_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._heartbeat_loop,
            name="mc-recorder-render-heartbeat",
            daemon=True,
        )
        self._last_error: str | None = None

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=30)

    def set_phase(self, phase: str, message: str | None = None) -> None:
        if phase not in {"downloading", "rendering", "uploading"}:
            raise RecorderError(f"invalid local render phase: {phase}")
        with self._state_lock:
            self._phase = phase
            self._message = message[:256] if message else None

    def call(self, action: str, body: dict[str, Any], *, timeout_seconds: int = 60) -> dict[str, Any]:
        with self._rpc_lock:
            return call_recorder_json(
                self.remote,
                ["render-rpc", action],
                body=body,
                timeout_seconds=timeout_seconds,
            )

    def heartbeat(self) -> dict[str, Any]:
        with self._rpc_lock:
            with self._state_lock:
                phase = self._phase
                message = self._message
            return call_recorder_json(
                self.remote,
                ["render-rpc", "heartbeat"],
                body={
                    "worker_id": self.worker_id,
                    "attempt_id": self.attempt_id,
                    "lease_token": self.lease_token,
                    "phase": phase,
                    "message": message,
                    "lease_seconds": _LEASE_SECONDS,
                },
                timeout_seconds=30,
            )

    def _heartbeat_loop(self) -> None:
        while not self._stop.wait(_WORKER_HEARTBEAT_SECONDS):
            try:
                self.heartbeat()
            except RecorderError as exc:
                self._last_error = str(exc)
            else:
                self._last_error = None


def _worker_name() -> str:
    name = socket.gethostname().strip() or "minecraft-gui-renderer"
    return name[:80]


def _register_worker(
    remote: LocalRecorder,
    worker_id: str,
) -> None:
    registration = call_recorder_json(
        remote,
        ["render-rpc", "register"],
        body={
            "worker_id": worker_id,
            "name": _worker_name(),
            "capabilities": {
                "ephemeral": True,
                "persistent": False,
                "fps": [20],
                "renderer": "minecraft-java-gui",
                "portable_request_no_gui": True,
                FULL_CLIENT_PRESENTATION_CAPABILITY_KEY: FULL_CLIENT_PRESENTATION_CONTRACT,
                _STRUCTURED_CLAIM_FAILURE_CAPABILITY: True,
            },
        },
    )
    server_capabilities = registration.get("server_capabilities")
    if not isinstance(server_capabilities, dict) or server_capabilities.get(FULL_CLIENT_PRESENTATION_CAPABILITY_KEY) != FULL_CLIENT_PRESENTATION_CONTRACT:
        raise _IncompatibleServerError("mc-recorder does not support the required full-client presentation contract; update the server tooling")


def _claim_fields(response: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    claim = response.get("claim")
    if not isinstance(claim, dict):
        raise RecorderError("render claim response lacks a claim object")
    job = claim.get("job")
    attempt = claim.get("attempt")
    if not isinstance(job, dict) or not isinstance(attempt, dict):
        raise RecorderError("render claim response lacks job or attempt identity")
    return job, attempt


def _source_rows(response: dict[str, Any]) -> list[dict[str, Any]]:
    raw_sources = response.get("sources")
    if not isinstance(raw_sources, list) or not raw_sources:
        raise RecorderError("render job has no exact replay sources")
    sources: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in raw_sources:
        if not isinstance(raw, dict):
            raise RecorderError("render source is not an object")
        segment_id = _canonical_uuid(raw.get("segment_id"), "replay segment ID")
        ordinal = raw.get("segment_ordinal")
        size = raw.get("size_bytes")
        digest = raw.get("sha256")
        path = raw.get("path")
        if segment_id in seen:
            raise RecorderError("render response repeats a replay segment")
        if not isinstance(ordinal, int) or isinstance(ordinal, bool) or ordinal < 0:
            raise RecorderError("render source has an invalid ordinal")
        if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
            raise RecorderError("render source has an invalid byte size")
        if not isinstance(digest, str) or _SHA256.fullmatch(digest) is None:
            raise RecorderError("render source has an invalid SHA-256")
        if not isinstance(path, str):
            raise RecorderError("render source has no transfer path")
        seen.add(segment_id)
        sources.append(
            {
                **raw,
                "segment_id": segment_id,
                "segment_ordinal": ordinal,
                "size_bytes": size,
                "sha256": digest,
                "path": path,
            }
        )
    return sorted(sources, key=lambda item: int(item["segment_ordinal"]), reverse=True)


def _structured_hud_response(response: dict[str, Any], job: dict[str, Any]) -> dict[str, Any] | None:
    raw = response.get("structured_hud")
    payload = job.get("payload")
    render = payload.get("render") if isinstance(payload, dict) else None
    requires_hud = isinstance(render, dict) and render.get("presentation_contract") == FULL_CLIENT_PRESENTATION_CONTRACT
    if raw is None:
        if requires_hud:
            raise RecorderError("remote full-client render claim has no structured HUD sidecar")
        return None
    if not isinstance(raw, dict) or "path" not in raw:
        raise RecorderError("render claim has an invalid structured HUD sidecar")
    path = raw.get("path")
    if not isinstance(path, str):
        raise RecorderError("structured HUD sidecar has no transfer path")
    envelope = validate_hud_sidecar_envelope(
        {key: value for key, value in raw.items() if key != "path"},
        "structured HUD sidecar",
    )
    if isinstance(payload, dict) and (
        envelope["dataset_id"] != payload.get("dataset_id")
        or envelope["session_id"] != payload.get("session_id")
        or envelope["player_uuid"] != payload.get("player_uuid")
        or envelope["connection_id"] != payload.get("connection_id")
        or envelope["first_tick"] != payload.get("start_tick")
        or envelope["last_tick"] != payload.get("end_tick")
    ):
        raise RecorderError("structured HUD sidecar does not match its claimed job")
    if not requires_hud:
        raise RecorderError("structured HUD sidecar lacks the current full-client presentation contract")
    return {**envelope, "path": path}


def _run_registered_worker_once(
    config: RecorderConfig,
    remote: LocalRecorder,
    *,
    worker_id: str,
    job_id: str | None = None,
    cache_root: Path,
    keep_workspace: bool = False,
) -> _WorkerPoll:
    """Claim and fully process at most one job for an already registered worker."""

    selected_job = _canonical_uuid(job_id, "render job ID") if job_id is not None else None
    canonical_worker = _canonical_uuid(worker_id, "render worker ID")
    claim_body: dict[str, Any] = {
        "worker_id": canonical_worker,
        "lease_seconds": _LEASE_SECONDS,
    }
    if selected_job is not None:
        claim_body["job_id"] = selected_job
    try:
        claimed = call_recorder_json(
            remote,
            ["render-rpc", "claim"],
            body=claim_body,
            timeout_seconds=300,
        )
    except RecorderError as exc:
        raise _ClaimOutcomeUnknownError("render worker stopped because the claim outcome is unknown; inspect the queue before restarting") from exc
    if claimed.get("claim") is None:
        reason = claimed.get("reason")
        if reason == "claim_failed":
            try:
                error = _bounded_string(claimed.get("error"), "claimed job failure", 2048)
                failed_job = claimed.get("failed_job")
                if not isinstance(failed_job, dict):
                    raise RecorderError("render claim lacks its failed job")
                failed_job_id = _canonical_uuid(failed_job.get("id"), "failed render job ID")
            except RecorderError as exc:
                raise _ClaimedJobError("server reported a claimed job failure with invalid details; inspect the queue before restarting") from exc
            raise _ClaimedJobError(f"server failed claimed job {failed_job_id} before transfer: {error}")
        if reason is not None and reason != "replay_pending":
            raise RecorderError(f"render claim returned an unsupported reason: {reason!r}")
        deferred_cooldown = claimed.get("deferred_job_cooldown_seconds")
        if deferred_cooldown is not None and (reason != "replay_pending" or not isinstance(deferred_cooldown, int) or isinstance(deferred_cooldown, bool) or not 1 <= deferred_cooldown <= 300):
            raise RecorderError("render claim returned an invalid deferred-job cooldown")
        if selected_job is not None:
            suffix = f": {reason}" if isinstance(reason, str) else ""
            raise RecorderError(f"RabbitMQ-selected render job {selected_job} is not claimable{suffix}")
        return _WorkerPoll(None, reason, deferred_cooldown)
    claimed_job_id: str | None = None
    attempt_id: str | None = None
    lease_token: str | None = None
    try:
        job, attempt = _claim_fields(claimed)
        claimed_job_id = _canonical_uuid(job.get("id"), "claimed render job ID")
        attempt_id = _canonical_uuid(attempt.get("id"), "render attempt ID")
        lease_token = _bounded_string(attempt.get("lease_token"), "render lease token", 256)
        sources = _source_rows(claimed)
        structured_hud = _structured_hud_response(claimed, job)
        upload_directory = claimed.get("upload_directory")
        if not isinstance(upload_directory, str):
            raise RecorderError("render claim lacks its upload directory")
        remote.contains(upload_directory)
    except Exception as exc:
        # A non-null claim means the server has already leased queue state even
        # when the remainder of its response is malformed or incompatible.
        # Recover any trustworthy fencing identity, fail that attempt when
        # possible, and always stop before another claim.
        raw_claim = claimed.get("claim")
        if isinstance(raw_claim, dict):
            raw_job = raw_claim.get("job")
            raw_attempt = raw_claim.get("attempt")
            if claimed_job_id is None and isinstance(raw_job, dict):
                try:
                    claimed_job_id = _canonical_uuid(raw_job.get("id"), "claimed render job ID")
                except RecorderError:
                    pass
            if isinstance(raw_attempt, dict):
                if attempt_id is None:
                    try:
                        attempt_id = _canonical_uuid(raw_attempt.get("id"), "render attempt ID")
                    except RecorderError:
                        pass
                if lease_token is None:
                    try:
                        lease_token = _bounded_string(
                            raw_attempt.get("lease_token"),
                            "render lease token",
                            256,
                        )
                    except RecorderError:
                        pass
        detail = (str(exc) or type(exc).__name__)[:2048]
        if attempt_id is not None and lease_token is not None:
            try:
                call_recorder_json(
                    remote,
                    ["render-rpc", "fail"],
                    body={
                        "worker_id": canonical_worker,
                        "attempt_id": attempt_id,
                        "lease_token": lease_token,
                        "error": f"invalid claimed render response: {detail}"[:2048],
                    },
                )
            except RecorderError:
                pass
        identity = claimed_job_id or "unknown"
        raise _ClaimedJobError(f"render worker stopped after claimed job {identity} returned an invalid response: {detail}") from exc

    assert claimed_job_id is not None
    assert attempt_id is not None
    assert lease_token is not None

    lease = _RemoteLease(
        remote,
        worker_id=canonical_worker,
        attempt_id=attempt_id,
        lease_token=lease_token,
    )
    lease.start()
    workspace: Path | None = None
    primary_error: BaseException | None = None
    try:
        workspace = create_job_workspace(cache_root, claimed_job_id, attempt_id)
        bundles_root = workspace / "bundles"
        bundles_root.mkdir()
        local_hud: Path | None = None
        if structured_hud is not None:
            lease.set_phase("downloading", "Downloading verified structured HUD state")
            local_hud = download_hud_sidecar(
                remote,
                remote_path=str(structured_hud["path"]),
                expected_sha256=str(structured_hud["sha256"]),
                expected_size=int(structured_hud["size_bytes"]),
                cache_root=cache_root,
            )
        newer_cutoff: int | None = None
        processed = 0
        for index, source in enumerate(sources, 1):
            segment_id = str(source["segment_id"])
            lease.set_phase("downloading", f"Preparing replay {index}/{len(sources)}")
            request_response = lease.call(
                "request",
                {
                    "worker_id": canonical_worker,
                    "attempt_id": attempt_id,
                    "lease_token": lease_token,
                    "segment_id": segment_id,
                    "newer_cutoff": newer_cutoff,
                },
                timeout_seconds=300,
            )
            request_value = request_response.get("request")
            if request_value is None and request_response.get("done") is True:
                break
            if not isinstance(request_value, dict):
                raise RecorderError("render request response lacks the portable request")
            lease.set_phase("downloading", f"Downloading replay {index}/{len(sources)}")
            local_replay = download_replay(
                remote,
                remote_path=str(source["path"]),
                expected_sha256=str(source["sha256"]),
                expected_size=int(source["size_bytes"]),
                cache_root=cache_root,
            )
            request_path = workspace / "requests" / f"{segment_id}.json"
            portable = write_portable_render_request(request_path, request_value)
            if portable.sha256 != request_response.get("request_sha256"):
                raise RecorderError("portable request SHA-256 does not match the server response")
            if portable.data["source_replay"]["segment_id"] != segment_id:
                raise RecorderError("portable request selected the wrong replay segment")
            render_job = materialize_portable_render_job(
                portable,
                local_replay,
                workspace / "renders" / segment_id,
                structured_hud=local_hud,
            )
            lease.set_phase("rendering", f"Rendering replay {index}/{len(sources)}")
            result = launch_render_job(config, render_job, offline=True)
            bundle = create_render_bundle(
                render_job,
                bundles_root / segment_id,
                portable,
            )
            processed += 1
            if result.get("status") == "complete":
                first_tick = result.get("global_start_tick")
                if not isinstance(first_tick, int) or isinstance(first_tick, bool) or first_tick < 0:
                    raise RecorderError("renderer returned an invalid effective global start tick")
                newer_cutoff = first_tick if newer_cutoff is None else min(newer_cutoff, first_tick)
            elif result.get("status") != "no_coverage":
                raise RecorderError("renderer returned an unsupported terminal status")
            if bundle.request_id != portable.request_id:
                raise RecorderError("render bundle request identity changed during creation")

        if processed == 0:
            raise RecorderError("render plan completed without processing a replay segment")
        lease.set_phase("uploading", f"Uploading {processed} render bundle(s)")
        upload_bundle(
            remote,
            local_directory=bundles_root,
            remote_directory=upload_directory,
        )
        lease.heartbeat()
        lease.stop()
        final = lease.call(
            "finalize",
            {
                "worker_id": canonical_worker,
                "attempt_id": attempt_id,
                "lease_token": lease_token,
            },
            timeout_seconds=24 * 60 * 60,
        )
        if keep_workspace:
            assert workspace is not None
            final = {**final, "local_workspace": str(workspace)}
        return _WorkerPoll(final)
    except Exception as exc:
        lease.stop()
        try:
            lease.call(
                "fail",
                {
                    "worker_id": canonical_worker,
                    "attempt_id": attempt_id,
                    "lease_token": lease_token,
                    "error": str(exc)[:2048] or type(exc).__name__,
                },
            )
        except RecorderError:
            pass
        detail = str(exc) or type(exc).__name__
        failure = _ClaimedJobError(f"render worker stopped after job {claimed_job_id} failed: {detail}")
        primary_error = failure
        raise failure from exc
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        lease.stop()
        if not keep_workspace and workspace is not None:
            try:
                remove_job_workspace(workspace)
            except Exception as cleanup_exc:
                detail = str(cleanup_exc) or type(cleanup_exc).__name__
                if primary_error is not None:
                    primary_error.add_note(f"render workspace cleanup also failed: {detail}")
                else:
                    raise _ClaimedJobError(f"render worker stopped after job {claimed_job_id} cleanup failed: {detail}") from cleanup_exc


def run_ephemeral_worker(
    config: RecorderConfig,
    remote: LocalRecorder,
    *,
    job_id: str | None = None,
    cache_root: Path | None = None,
    keep_workspace: bool = False,
) -> dict[str, Any] | None:
    """Register, process at most one queued job, and exit.

    This compatibility wrapper preserves the original one-shot API. The CLI's
    default worker uses :func:`run_render_worker` so one process identity is
    reused while polling and processing multiple jobs.
    """

    selected_job = _canonical_uuid(job_id, "render job ID") if job_id is not None else None
    worker_id = ephemeral_worker_id()
    cache = default_worker_cache() if cache_root is None else cache_root
    prepare_renderer_runtime(config)
    _register_worker(remote, worker_id)
    return _run_registered_worker_once(
        config,
        remote,
        worker_id=worker_id,
        job_id=selected_job,
        cache_root=cache,
        keep_workspace=keep_workspace,
    ).result


def run_render_worker(
    config: RecorderConfig,
    remote: LocalRecorder,
    *,
    job_id: str | None = None,
    cache_root: Path | None = None,
    keep_workspace: bool = False,
    once: bool = False,
    on_result: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any] | None:
    """Register, process at most one RabbitMQ-selected render job, and exit."""

    if once is not True:
        raise RecorderError("render worker is one-shot; pass once=True")
    selected_job = _canonical_uuid(job_id, "render job ID") if job_id is not None else None
    worker_id = ephemeral_worker_id()
    cache = default_worker_cache() if cache_root is None else cache_root
    prepare_renderer_runtime(config)
    _register_worker(remote, worker_id)
    poll = _run_registered_worker_once(
        config,
        remote,
        worker_id=worker_id,
        job_id=selected_job,
        cache_root=cache,
        keep_workspace=keep_workspace,
    )
    if poll.result is not None and on_result is not None:
        on_result(poll.result)
    return poll.result
