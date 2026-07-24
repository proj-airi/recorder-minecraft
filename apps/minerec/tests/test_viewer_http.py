from __future__ import annotations

import http.client
import sqlite3
import sys
import tempfile
import threading
import unittest
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from minerec.errors import RecorderError
from minerec.serve.viewer.server import (
    MAX_JSON_RESPONSE_BYTES,
    MAX_TICK_JSON_RESPONSE_BYTES,
    ViewerApplication,
    ViewerHTTPServer,
    _byte_ranges,
    _json_body,
)
from minerec.serve.viewer.service import ViewerBundle


class ByteRangeTest(unittest.TestCase):
    def test_bounds_part_count_and_suffix(self) -> None:
        self.assertEqual([(95, 99)], _byte_ranges("bytes=-5", 100))
        with self.assertRaisesRegex(Exception, "1 to 8"):
            _byte_ranges("bytes=" + ",".join("0-0" for _ in range(9)), 100)

    def test_trajectory_sampling_preserves_endpoints_and_discontinuity_sides(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            scene = Path(temporary) / "scene.sqlite3"
            with sqlite3.connect(scene) as database:
                database.execute(
                    """
                    CREATE TABLE player_states (
                        server_tick INTEGER PRIMARY KEY,
                        dimension TEXT NOT NULL,
                        position_x REAL NOT NULL,
                        position_y REAL NOT NULL,
                        position_z REAL NOT NULL,
                        entity_instance_id TEXT NOT NULL
                    )
                    """
                )
                rows = []
                for ordinal in range(20):
                    tick = 100 + ordinal if ordinal < 10 else 190 + ordinal
                    dimension = "minecraft:overworld" if ordinal < 10 else "minecraft:the_nether"
                    rows.append((tick, dimension, float(ordinal), 64.0, 0.0, "subject"))
                database.executemany("INSERT INTO player_states VALUES (?, ?, ?, ?, ?, ?)", rows)
                database.commit()
            bundle = object.__new__(ViewerBundle)
            bundle.opened = SimpleNamespace(scene_path=scene)

            result = bundle.trajectory(max_points=6)

            self.assertEqual(20, result["total_points"])
            self.assertLessEqual(result["returned_points"], 6)
            returned_ticks = [point["tick"] for point in result["points"]]
            self.assertEqual(100, returned_ticks[0])
            self.assertEqual(209, returned_ticks[-1])
            self.assertIn(109, returned_ticks)
            self.assertIn(200, returned_ticks)
            self.assertTrue(result["truncated"])

    def test_tick_response_bound_exceeds_the_scene_player_blob_limit(self) -> None:
        maximum_scene_blob_bytes = 64 * 1024 * 1024
        canonical_wrapper_bytes = len(b'{"value":""}')
        payload = {
            "state": {
                "payload": {
                    "value": "x" * (maximum_scene_blob_bytes - canonical_wrapper_bytes),
                }
            }
        }
        with self.assertRaisesRegex(RecorderError, "safe size"):
            _json_body(payload)
        body = _json_body(payload, maximum_bytes=MAX_TICK_JSON_RESPONSE_BYTES)
        self.assertGreater(MAX_TICK_JSON_RESPONSE_BYTES, maximum_scene_blob_bytes)
        self.assertGreater(len(body), maximum_scene_blob_bytes)


class _StubService:
    def __init__(self, media: Path) -> None:
        self.media = media
        self.committed: list[str] = []
        self.discarded: list[str] = []

    def close(self) -> None:
        return

    def summary(self, *, staged_import_id: str | None = None) -> dict[str, object]:
        return {"bundle_id": "bundle-test", "staged_import_id": staged_import_id}

    @contextmanager
    def render_lease(self, bundle_id: str) -> Iterator[Path]:
        if bundle_id != "bundle-test":
            raise RecorderError("requested render does not belong to the active bundle")
        yield self.media

    def tick(self, tick: int, **_query: Any) -> dict[str, object]:
        return {"tick": tick}

    def actions(self, **_query: Any) -> dict[str, object]:
        return {"actions": []}

    def trajectory(self, **_query: Any) -> dict[str, object]:
        return {"points": [], "total_points": 0, "returned_points": 0, "truncated": False}

    def scene_slice(self, **_query: Any) -> dict[str, object]:
        return {"cells": []}

    def replays(self) -> dict[str, object]:
        return {"replays": []}

    def timeline(self, **_query: Any) -> dict[str, object]:
        return {"frames": [], "next_frame": None}

    def commit_staged(self, staged_import_id: str) -> dict[str, object]:
        self.committed.append(staged_import_id)
        return {"bundle_id": "bundle-test"}

    def discard_staged(self, staged_import_id: str) -> None:
        self.discarded.append(staged_import_id)


class ViewerHTTPTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        static = root / "static"
        static.mkdir()
        (static / "index.html").write_text("viewer", encoding="utf-8")
        media = root / "fpv.mp4"
        media.write_bytes(bytes(range(100)))
        self.application = ViewerApplication("secret-token", static_root=static)
        self.application.service.close()
        self.application.service = _StubService(media)  # type: ignore[assignment]
        try:
            self.server = ViewerHTTPServer(("127.0.0.1", 0), self.application)
        except PermissionError:
            self.application.close()
            self.temporary.cleanup()
            self.skipTest("sandbox forbids loopback listeners")
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.host, self.port = self.server.server_address

    def tearDown(self) -> None:
        self.server.shutdown()
        self.thread.join(timeout=5)
        self.server.server_close()
        self.application.close()
        self.temporary.cleanup()

    def _request(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        body: bytes | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        connection = http.client.HTTPConnection(self.host, self.port, timeout=5)
        try:
            connection.request(method, path, body=body, headers=headers or {})
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def test_requires_launch_token_and_exact_host_origin(self) -> None:
        status, _headers, _body = self._request("GET", "/api/v1/viewer/bundle")
        self.assertEqual(401, status)

        status, _headers, _body = self._request(
            "GET",
            "/api/v1/viewer/bundle",
            headers={"Host": "attacker.invalid", "X-Minerec-Viewer-Token": "secret-token"},
        )
        self.assertEqual(421, status)

        status, headers, body = self._request(
            "GET",
            "/api/v1/viewer/bundle",
            headers={"X-Minerec-Viewer-Token": "secret-token"},
        )
        self.assertEqual(200, status)
        self.assertIn(b"bundle-test", body)
        self.assertNotIn("Access-Control-Allow-Origin", headers)
        self.assertIn("default-src 'none'", headers["Content-Security-Policy"])

        status, _headers, _body = self._request(
            "POST",
            "/api/v1/viewer/import",
            headers={
                "X-Minerec-Viewer-Token": "secret-token",
                "Origin": "http://attacker.invalid",
                "Content-Length": "1",
            },
            body=b"x",
        )
        self.assertEqual(403, status)

    def test_routes_staged_reads_commit_and_discard(self) -> None:
        status, _headers, body = self._request(
            "GET",
            "/api/v1/viewer/bundle",
            headers={
                "X-Minerec-Viewer-Token": "secret-token",
                "X-Minerec-Viewer-Staged-Import": "staged-import-id-123",
            },
        )
        self.assertEqual(200, status)
        self.assertIn(b"staged-import-id-123", body)

        mutation_headers = {
            "X-Minerec-Viewer-Token": "secret-token",
            "Origin": f"http://{self.host}:{self.port}",
            "Content-Length": "0",
        }
        status, _headers, _body = self._request(
            "POST",
            "/api/v1/viewer/imports/staged-import-id-123/commit",
            headers=mutation_headers,
            body=b"",
        )
        self.assertEqual(200, status)
        status, _headers, _body = self._request(
            "POST",
            "/api/v1/viewer/imports/staged-import-id-456/discard",
            headers=mutation_headers,
            body=b"",
        )
        self.assertEqual(200, status)
        self.assertEqual(["staged-import-id-123"], self.application.service.committed)
        self.assertEqual(["staged-import-id-456"], self.application.service.discarded)

    def test_serves_single_suffix_and_multipart_media_ranges(self) -> None:
        base = "/api/v1/viewer/render/fpv?token=secret-token&bundle_id=bundle-test"
        status, _headers, _body = self._request(
            "GET",
            "/api/v1/viewer/render/fpv?token=secret-token&bundle_id=stale-bundle",
        )
        self.assertEqual(400, status)

        status, headers, body = self._request("GET", base, headers={"Range": "bytes=10-19"})
        self.assertEqual(206, status)
        self.assertEqual("bytes 10-19/100", headers["Content-Range"])
        self.assertEqual(bytes(range(10, 20)), body)

        status, headers, body = self._request("GET", base, headers={"Range": "bytes=-5"})
        self.assertEqual(206, status)
        self.assertEqual(bytes(range(95, 100)), body)

        status, headers, body = self._request(
            "GET",
            base,
            headers={"Range": "bytes=0-2,97-99"},
        )
        self.assertEqual(206, status)
        self.assertTrue(headers["Content-Type"].startswith("multipart/byteranges;"))
        self.assertIn(bytes(range(3)), body)
        self.assertIn(bytes(range(97, 100)), body)

        status, headers, body = self._request("GET", base, headers={"Range": "bytes=500-600"})
        self.assertEqual(416, status)
        self.assertEqual("bytes */100", headers["Content-Range"])
        self.assertEqual(b"", body)


if __name__ == "__main__":
    unittest.main()
