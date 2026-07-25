from __future__ import annotations

import fcntl
import json
import os
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterator

from minerec.errors import RecorderError


@contextmanager
def operation_lock(runtime: Path, operation: str) -> Iterator[None]:
    """Serialize host-side processor mutations."""

    runtime.mkdir(parents=True, exist_ok=True)
    path = runtime / "operation.lock"
    handle = path.open("a+", encoding="utf-8")
    try:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            handle.seek(0)
            owner = handle.read(2048).strip()
            detail = f" ({owner})" if owner else ""
            raise RecorderError(f"another mc-recorder operation is already running{detail}") from exc

        handle.seek(0)
        handle.truncate()
        json.dump(
            {
                "operation": operation,
                "pid": os.getpid(),
                "started_at": datetime.now(UTC).isoformat(),
            },
            handle,
            sort_keys=True,
        )
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
        yield
    finally:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()
