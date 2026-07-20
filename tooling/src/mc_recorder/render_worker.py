from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Sequence

from .errors import RecorderError


MAX_RPC_BYTES = 8 * 1024 * 1024
_SSH_HOST = re.compile(
    r"^(?:[A-Za-z0-9][A-Za-z0-9_.-]{0,63}@)?[A-Za-z0-9][A-Za-z0-9_.-]{0,253}$"
)
_REMOTE_COMPONENT = re.compile(r"^[A-Za-z0-9._-]+$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


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


def remove_job_workspace(path: Path) -> None:
    requested = path.expanduser()
    if requested.is_symlink() or not requested.exists():
        return
    if not requested.is_dir():
        raise RecorderError(f"render job workspace is not a directory: {requested}")
    shutil.rmtree(requested)
