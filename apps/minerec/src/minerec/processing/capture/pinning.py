from __future__ import annotations

import fcntl
import json
import os
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO, Iterator

from minerec.errors import RecorderError


@contextmanager
def pin_sealed_epochs(session: Path) -> Iterator[tuple[Path, ...]]:
    """Hold shared locks on all epoch manifests declared sealed."""

    if session.is_symlink() or not session.is_dir():
        raise RecorderError(f"session is not a safe directory: {session}")
    epochs = session.resolve() / "epochs"
    if epochs.is_symlink() or not epochs.is_dir():
        raise RecorderError(f"session has no safe epochs directory: {session}")
    handles: list[BinaryIO] = []
    pinned: list[Path] = []
    try:
        for candidate in sorted(epochs.iterdir()):
            manifest = candidate / "manifest.json"
            if candidate.is_symlink() or not candidate.is_dir() or manifest.is_symlink() or not manifest.is_file():
                continue
            try:
                declared = json.loads(manifest.read_text(encoding="utf-8"))
            except OSError, json.JSONDecodeError:
                continue
            if not isinstance(declared, dict) or declared.get("sealed") is not True:
                continue
            try:
                handle = manifest.open("rb")
                fcntl.flock(handle.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)
                opened = os.fstat(handle.fileno())
                current_path = manifest.stat()
                handle.seek(0)
                current = json.load(handle)
            except (OSError, BlockingIOError, json.JSONDecodeError) as exc:
                if "handle" in locals() and not handle.closed:
                    handle.close()
                raise RecorderError(f"could not pin sealed epoch: {candidate}") from exc
            identity = (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns)
            path_identity = (current_path.st_dev, current_path.st_ino, current_path.st_size, current_path.st_mtime_ns)
            if identity != path_identity or not isinstance(current, dict) or current.get("sealed") is not True:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                handle.close()
                raise RecorderError(f"sealed epoch changed while being pinned: {candidate}")
            handles.append(handle)
            pinned.append(candidate.resolve())
        yield tuple(pinned)
    finally:
        for handle in reversed(handles):
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            finally:
                handle.close()


__all__ = ["pin_sealed_epochs"]
