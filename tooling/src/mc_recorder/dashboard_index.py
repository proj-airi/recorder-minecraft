from __future__ import annotations

import threading
import time

from .dataset_viewer import DatasetCatalogResult, DatasetViewer


class DatasetIndex:
    """Continuously refresh the verified catalog without blocking HTTP requests."""

    def __init__(self, viewer: DatasetViewer, *, refresh_seconds: float = 5.0):
        self.viewer = viewer
        self.refresh_seconds = refresh_seconds
        self._catalog = DatasetCatalogResult((), ())
        self._indexing = True
        self._error: str | None = None
        self._refreshed_at: float | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name="mc-recorder-dataset-index",
            daemon=True,
        )
        self._thread.start()

    def snapshot(self) -> tuple[DatasetCatalogResult, bool, str | None, float | None]:
        with self._lock:
            return self._catalog, self._indexing, self._error, self._refreshed_at

    def rejection_for(self, directory_name: str) -> str | None:
        catalog, _indexing, _error, _refreshed_at = self.snapshot()
        return next(
            (issue.message for issue in catalog.rejected if issue.name == directory_name),
            None,
        )

    def close(self) -> None:
        self._stop.set()
        self._wake.set()
        self._thread.join()

    def request_refresh(self) -> None:
        self._wake.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            with self._lock:
                self._indexing = True
            try:
                catalog = self.viewer.catalog()
            except Exception as exc:  # background boundary: retain the last verified catalog
                with self._lock:
                    self._error = f"{exc.__class__.__name__}: {exc}"[:2048]
            else:
                with self._lock:
                    self._catalog = catalog
                    self._error = None
                    self._refreshed_at = time.time()
            finally:
                with self._lock:
                    self._indexing = False
            self._wake.wait(self.refresh_seconds)
            self._wake.clear()
