from __future__ import annotations

import signal
import sys
import time

from minerec.config import load_storage_monitor_env
from minerec.errors import RecorderError
from minerec.processing.capture.storage import enforce_quota, human_bytes

_stopping = False


def _stop(_signum: int, _frame: object) -> None:
    global _stopping
    _stopping = True


def main() -> int:
    try:
        env = load_storage_monitor_env()
    except RecorderError as exc:
        print(f"storage-monitor: invalid environment: {exc}", file=sys.stderr, flush=True)
        return 2
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    previous_status: str | None = None

    while not _stopping:
        try:
            report = enforce_quota(env.capture_root, quota_bytes=env.quota_bytes, warn_percent=env.warn_percent, evict_oldest=env.evict_oldest)
        except RecorderError as exc:
            print(f"storage-monitor: ERROR: {exc}", file=sys.stderr, flush=True)
            report = None
        if report is not None and (report.status != "ok" or previous_status != report.status):
            prefix = "WARNING" if report.status in {"warning", "full"} else "INFO"
            print(
                f"storage-monitor: {prefix}: {report.message}; used={human_bytes(report.after_bytes)} quota={human_bytes(report.quota_bytes)}",
                flush=True,
            )
            for item in report.evicted:
                print(
                    f"storage-monitor: EVICTED: {item.source_kind} {item.source_path} ({human_bytes(item.size_bytes)})",
                    flush=True,
                )
            previous_status = report.status
        deadline = time.monotonic() + max(env.check_interval_seconds, 1)
        while not _stopping and time.monotonic() < deadline:
            time.sleep(min(1.0, deadline - time.monotonic()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
