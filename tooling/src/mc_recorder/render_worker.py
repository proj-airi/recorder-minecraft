from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import tempfile
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Sequence

from .config import RecorderConfig
from .errors import RecorderError
from .render_job import launch_render_job
from .render_transfer import (
    create_render_bundle,
    materialize_portable_render_job,
    write_portable_render_request,
)


MAX_RPC_BYTES = 8 * 1024 * 1024
_SSH_HOST = re.compile(
    r"^(?:[A-Za-z0-9][A-Za-z0-9_.-]{0,63}@)?[A-Za-z0-9][A-Za-z0-9_.-]{0,253}$"
)
_REMOTE_COMPONENT = re.compile(r"^[A-Za-z0-9._-]+$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_WORKSPACE_MARKER = ".mc-recorder-ephemeral-worker.json"
_WORKER_HEARTBEAT_SECONDS = 20.0
_LEASE_SECONDS = 120


@dataclass(frozen=True)
class RemoteRecorder:
    ssh_host: str
    root: PurePosixPath

    @classmethod
    def parse(cls, ssh_host: str, root: str) -> "RemoteRecorder":
        if not _SSH_HOST.fullmatch(ssh_host) or ssh_host.startswith("-"):
            raise RecorderError(f"invalid SSH host or alias: {ssh_host!r}")
        parsed = PurePosixPath(root)
        if (
            not parsed.is_absolute()
            or str(parsed) == "/"
            or any(part in {"", ".", ".."} or not _REMOTE_COMPONENT.fullmatch(part) for part in parsed.parts[1:])
        ):
            raise RecorderError(f"invalid remote recorder root: {root!r}")
        return cls(ssh_host=ssh_host, root=parsed)

    @property
    def config(self) -> PurePosixPath:
        return self.root / "recorder.toml"

    @property
    def python_source(self) -> PurePosixPath:
        return self.root / "tooling" / "src"

    def contains(self, path: str | PurePosixPath) -> PurePosixPath:
        candidate = PurePosixPath(path)
        if not candidate.is_absolute():
            raise RecorderError("remote transfer path must be absolute")
        try:
            candidate.relative_to(self.root)
        except ValueError as exc:
            raise RecorderError("remote transfer path escapes the recorder root") from exc
        if any(part in {"", ".", ".."} or not _REMOTE_COMPONENT.fullmatch(part) for part in candidate.parts[1:]):
            raise RecorderError("remote transfer path contains an unsafe component")
        return candidate


def _remote_cli(remote: RemoteRecorder, *arguments: str) -> str:
    command = [
        "/usr/bin/env",
        f"PYTHONPATH={remote.python_source}",
        "/usr/bin/python3",
        "-m",
        "mc_recorder",
        "--config",
        str(remote.config),
        *arguments,
    ]
    return shlex.join(command)


def ssh_rpc_command(remote: RemoteRecorder, *arguments: str) -> list[str]:
    return [
        "ssh",
        "-o",
        "BatchMode=yes",
        "--",
        remote.ssh_host,
        _remote_cli(remote, *arguments),
    ]


def call_remote_json(
    remote: RemoteRecorder,
    arguments: Sequence[str],
    *,
    body: dict[str, Any] | None = None,
    timeout_seconds: int = 60,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, Any]:
    if timeout_seconds < 1:
        raise RecorderError("remote RPC timeout must be positive")
    payload = None if body is None else json.dumps(body, separators=(",", ":"), sort_keys=True) + "\n"
    try:
        result = runner(
            ssh_rpc_command(remote, *arguments),
            input=payload,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_seconds,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RecorderError(f"remote render RPC failed: {exc}") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "remote command failed").strip()
        raise RecorderError(f"remote render RPC exited with {result.returncode}: {detail[:2048]}")
    raw = result.stdout.encode("utf-8")
    if len(raw) > MAX_RPC_BYTES:
        raise RecorderError("remote render RPC response exceeds the size limit")
    try:
        value = json.loads(result.stdout)
    except (ValueError, RecursionError) as exc:
        raise RecorderError("remote render RPC returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise RecorderError("remote render RPC response must be an object")
    return value


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
    return (
        actual == expected_sha256
        and (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    )


def rsync_download_command(
    remote: RemoteRecorder,
    remote_path: str,
    local_partial: Path,
) -> list[str]:
    source = remote.contains(remote_path)
    return [
        "rsync",
        "-a",
        "--partial",
        "--append",
        "--",
        f"{remote.ssh_host}:{source}",
        str(local_partial),
    ]


def download_replay(
    remote: RemoteRecorder,
    *,
    remote_path: str,
    expected_sha256: str,
    expected_size: int,
    cache_root: Path,
    runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
) -> Path:
    digest = expected_sha256.lower()
    if _SHA256.fullmatch(digest) is None:
        raise RecorderError("render source has an invalid SHA-256")
    if expected_size <= 0 or expected_size > 2**63 - 1:
        raise RecorderError("render source has an invalid byte size")
    root = _safe_cache_root(cache_root) / "replays"
    root.mkdir(mode=0o700, exist_ok=True)
    destination = root / f"{digest}.zip"
    if _verified_replay(destination, digest, expected_size):
        return destination
    if destination.exists() or destination.is_symlink():
        if destination.is_dir():
            raise RecorderError(f"render replay cache entry is not a file: {destination}")
        destination.unlink()
    partial = root / f".{digest}.zip.inprogress"
    if partial.is_symlink() or (partial.exists() and not partial.is_file()):
        raise RecorderError(f"render replay partial is unsafe: {partial}")
    try:
        result = runner(rsync_download_command(remote, remote_path, partial), check=False)
    except OSError as exc:
        raise RecorderError(f"could not launch rsync: {exc}") from exc
    if result.returncode != 0:
        raise RecorderError(f"replay download failed with rsync exit code {result.returncode}")
    if not _verified_replay(partial, digest, expected_size):
        try:
            partial.unlink()
        except OSError:
            pass
        raise RecorderError("downloaded replay does not match its server SHA-256 and size")
    os.replace(partial, destination)
    return destination


def rsync_upload_command(
    remote: RemoteRecorder,
    local_directory: Path,
    remote_directory: str,
) -> list[str]:
    destination = remote.contains(remote_directory)
    source = local_directory.expanduser().resolve()
    if source.is_symlink() or not source.is_dir():
        raise RecorderError(f"render bundle is not a regular directory: {source}")
    return [
        "rsync",
        "-a",
        "--partial",
        "--checksum",
        "--",
        f"{source}/",
        f"{remote.ssh_host}:{destination}/",
    ]


def upload_bundle(
    remote: RemoteRecorder,
    *,
    local_directory: Path,
    remote_directory: str,
    runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
) -> None:
    try:
        result = runner(
            rsync_upload_command(remote, local_directory, remote_directory),
            check=False,
        )
    except OSError as exc:
        raise RecorderError(f"could not launch rsync: {exc}") from exc
    if result.returncode != 0:
        raise RecorderError(f"render upload failed with rsync exit code {result.returncode}")


def ephemeral_worker_id() -> str:
    return str(uuid.uuid4())


def default_worker_cache() -> Path:
    root = os.environ.get("XDG_CACHE_HOME")
    return (Path(root).expanduser() if root else Path.home() / ".cache") / "mc-recorder"


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
    if (
        not isinstance(value, dict)
        or value.get("owner") != "mc-recorder"
        or value.get("kind") != "ephemeral-render-workspace"
        or requested.parent.name != "jobs"
    ):
        raise RecorderError(f"refusing to remove an unowned render workspace: {requested}")
    shutil.rmtree(requested)


def _canonical_uuid(value: object, label: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError) as exc:
        raise RecorderError(f"invalid {label}: {value!r}") from exc


def _bounded_string(value: object, label: str, maximum: int = 2048) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > maximum
        or any(ord(character) < 32 for character in value)
    ):
        raise RecorderError(f"remote render response has an invalid {label}")
    return value


class _RemoteLease:
    def __init__(
        self,
        remote: RemoteRecorder,
        *,
        worker_id: str,
        attempt_id: str,
        lease_token: str,
    ):
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
            return call_remote_json(
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
            return call_remote_json(
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
    name = socket.gethostname().strip() or "ephemeral-mac-renderer"
    return name[:80]


def _claim_fields(response: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    claim = response.get("claim")
    if not isinstance(claim, dict):
        raise RecorderError("remote claim response lacks a claim object")
    job = claim.get("job")
    attempt = claim.get("attempt")
    if not isinstance(job, dict) or not isinstance(attempt, dict):
        raise RecorderError("remote claim response lacks job or attempt identity")
    return job, attempt


def _source_rows(response: dict[str, Any]) -> list[dict[str, Any]]:
    raw_sources = response.get("sources")
    if not isinstance(raw_sources, list) or not raw_sources:
        raise RecorderError("remote render job has no exact replay sources")
    sources: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in raw_sources:
        if not isinstance(raw, dict):
            raise RecorderError("remote render source is not an object")
        segment_id = _canonical_uuid(raw.get("segment_id"), "replay segment ID")
        ordinal = raw.get("segment_ordinal")
        size = raw.get("size_bytes")
        digest = raw.get("sha256")
        path = raw.get("path")
        if segment_id in seen:
            raise RecorderError("remote render response repeats a replay segment")
        if not isinstance(ordinal, int) or isinstance(ordinal, bool) or ordinal < 0:
            raise RecorderError("remote render source has an invalid ordinal")
        if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
            raise RecorderError("remote render source has an invalid byte size")
        if not isinstance(digest, str) or _SHA256.fullmatch(digest) is None:
            raise RecorderError("remote render source has an invalid SHA-256")
        if not isinstance(path, str):
            raise RecorderError("remote render source has no transfer path")
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


def run_ephemeral_worker(
    config: RecorderConfig,
    remote: RemoteRecorder,
    *,
    job_id: str | None = None,
    cache_root: Path | None = None,
    keep_workspace: bool = False,
) -> dict[str, Any] | None:
    """Claim, render, upload, and finalize one queued job, then exit."""

    selected_job = _canonical_uuid(job_id, "render job ID") if job_id is not None else None
    worker_id = ephemeral_worker_id()
    cache = default_worker_cache() if cache_root is None else cache_root
    call_remote_json(
        remote,
        ["render-rpc", "register"],
        body={
            "worker_id": worker_id,
            "name": _worker_name(),
            "capabilities": {
                "ephemeral": True,
                "fps": [20],
                "renderer": "minecraft-java-gui",
                "portable_request_no_gui": True,
                "voxel_capture": True,
            },
        },
    )
    claim_body: dict[str, Any] = {
        "worker_id": worker_id,
        "lease_seconds": _LEASE_SECONDS,
    }
    if selected_job is not None:
        claim_body["job_id"] = selected_job
    claimed = call_remote_json(
        remote,
        ["render-rpc", "claim"],
        body=claim_body,
        timeout_seconds=300,
    )
    if claimed.get("claim") is None:
        return None
    job, attempt = _claim_fields(claimed)
    claimed_job_id = _canonical_uuid(job.get("id"), "claimed render job ID")
    attempt_id = _canonical_uuid(attempt.get("id"), "render attempt ID")
    lease_token = _bounded_string(attempt.get("lease_token"), "render lease token", 256)
    sources = _source_rows(claimed)
    upload_directory = claimed.get("upload_directory")
    if not isinstance(upload_directory, str):
        raise RecorderError("remote render claim lacks its upload directory")
    remote.contains(upload_directory)

    lease = _RemoteLease(
        remote,
        worker_id=worker_id,
        attempt_id=attempt_id,
        lease_token=lease_token,
    )
    lease.start()
    workspace: Path | None = None
    try:
        workspace = create_job_workspace(cache, claimed_job_id, attempt_id)
        bundles_root = workspace / "bundles"
        bundles_root.mkdir()
        newer_cutoff: int | None = None
        processed = 0
        for index, source in enumerate(sources, 1):
            segment_id = str(source["segment_id"])
            lease.set_phase("downloading", f"Preparing replay {index}/{len(sources)}")
            request_response = lease.call(
                "request",
                {
                    "worker_id": worker_id,
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
                raise RecorderError("remote render request response lacks the portable request")
            lease.set_phase("downloading", f"Downloading replay {index}/{len(sources)}")
            local_replay = download_replay(
                remote,
                remote_path=str(source["path"]),
                expected_sha256=str(source["sha256"]),
                expected_size=int(source["size_bytes"]),
                cache_root=cache,
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
            )
            lease.set_phase("rendering", f"Rendering replay {index}/{len(sources)}")
            result = launch_render_job(config, render_job)
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
                "worker_id": worker_id,
                "attempt_id": attempt_id,
                "lease_token": lease_token,
            },
            timeout_seconds=24 * 60 * 60,
        )
        if keep_workspace:
            assert workspace is not None
            final = {**final, "local_workspace": str(workspace)}
        return final
    except Exception as exc:
        lease.stop()
        try:
            lease.call(
                "fail",
                {
                    "worker_id": worker_id,
                    "attempt_id": attempt_id,
                    "lease_token": lease_token,
                    "error": str(exc)[:2048] or type(exc).__name__,
                },
            )
        except RecorderError:
            pass
        if isinstance(exc, RecorderError):
            raise
        raise RecorderError(f"ephemeral render worker failed: {exc}") from exc
    finally:
        lease.stop()
        if not keep_workspace and workspace is not None:
            remove_job_workspace(workspace)
