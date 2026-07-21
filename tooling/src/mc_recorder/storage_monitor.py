from __future__ import annotations

import os
import signal
import sys
import time
from pathlib import Path

from .errors import RecorderError
from .storage import enforce_quota, human_bytes

_stopping = False


def _stop(_signum: int, _frame: object) -> None:
    global _stopping
    _stopping = True


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def main() -> int:
    root = Path(os.environ.get("MC_RECORDER_CAPTURE_ROOT", "/captures"))
    try:
        quota_bytes = int(os.environ["MC_RECORDER_QUOTA_BYTES"])
        warn_percent = int(os.environ.get("MC_RECORDER_WARN_PERCENT", "80"))
        interval = int(os.environ.get("MC_RECORDER_CHECK_INTERVAL", "60"))
    except (KeyError, ValueError) as exc:
        print(f"storage-monitor: invalid environment: {exc}", file=sys.stderr, flush=True)
        return 2
    evict = _env_bool("MC_RECORDER_EVICT_OLDEST", True)
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    previous_status: str | None = None

    while not _stopping:
        try:
            report = enforce_quota(
                root, quota_bytes=quota_bytes, warn_percent=warn_percent, evict_oldest=evict
            )
        except RecorderError as exc:
            print(f"storage-monitor: ERROR: {exc}", file=sys.stderr, flush=True)
            report = None
        if report is not None and (report.status != "ok" or previous_status != report.status):
            prefix = "WARNING" if report.status in {"warning", "full"} else "INFO"
            print(
                f"storage-monitor: {prefix}: {report.message}; "
                f"used={human_bytes(report.after_bytes)} quota={human_bytes(report.quota_bytes)}",
                flush=True,
            )
            for item in report.evicted:
                print(
                    f"storage-monitor: EVICTED: {item.source_kind} {item.source_path} "
                    f"({human_bytes(item.size_bytes)})",
                    flush=True,
                )
            previous_status = report.status
        deadline = time.monotonic() + max(interval, 1)
        while not _stopping and time.monotonic() < deadline:
            time.sleep(min(1.0, deadline - time.monotonic()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
