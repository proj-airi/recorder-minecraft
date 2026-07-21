from __future__ import annotations

import base64
import hashlib
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from mc_recorder.config import initialize, load_config
from mc_recorder.dashboard_http import (
    DashboardApplication,
    DashboardHandler,
    DashboardHTTPServer,
    serve_dashboard,
)
from mc_recorder.dataset_viewer import DatasetValidationError, VerifiedArtifact
from mc_recorder.errors import RecorderError


def _write_viewer_dataset(exports: Path) -> None:
    exports.mkdir(parents=True, exist_ok=True)
    directory = exports / "http-smoke.dataset"
    directory.mkdir()
    sample = {
        "schema_version": 1,
        "sample_key": {
            "session_id": "session-http",
            "server_tick": 20,
            "player_uuid": "player-http",
            "connection_id": "connection-http",
        },
        "session_id": "session-http",
        "epoch_index": 0,
        "server_tick": 20,
        "player_uuid": "player-http",
        "connection_id": "connection-http",
        "state": {
            "player_name": "HTTP Player",
            "dimension": "minecraft:overworld",
            "position": {"x": 1, "y": 64, "z": 2},
        },
        "action": {"ordered_packets": [], "reconstructed_control": None},
        "next_state": {"player_name": "HTTP Player"},
        "next_server_tick": 21,
        "peers": {"state": [], "next_state": []},
        "modalities": {
            "rgb": {"available": False, "valid": False, "reference": None, "reason": "missing"},
            "voxels": {
                "available": False,
                "valid": False,
                "reference": None,
                "reason": "missing",
            },
        },
        "transition_valid": True,
        "transition_invalid_reasons": [],
        "source": {},
        "source_manifest_sha256": "0" * 64,
    }
    streams = {
        "samples.jsonl": (json.dumps(sample, separators=(",", ":")) + "\n").encode(),
        "states.jsonl": b"",
        "actions.jsonl": b"",
        "modalities.jsonl": b"",
    }
    for name, data in streams.items():
        (directory / name).write_bytes(data)
    manifest = {
        "schema_version": 1,
        "owner": "mc-recorder",
        "format": "mc-recorder-jsonl-v1",
        "created_at": "2026-07-21T00:00:00+00:00",
        "session_id": "session-http",
        "source": {"sealed_epochs": 1, "active_epochs_skipped": 0},
        "selection": {"players": [], "from_tick": None, "to_tick": None},
        "modalities": {},
        "files": {
            name: {"size_bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
            for name, data in streams.items()
        },
    }
    (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


class DashboardHTTPTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.config = load_config(initialize(self.root / "recorder.toml", accept_eula=True))
        self.application = DashboardApplication(self.config, "operator", "correct horse")
        try:
            self.server = DashboardHTTPServer(("127.0.0.1", 0), self.application)
        except PermissionError:
            self.application.close()
            self.temporary.cleanup()
            self.skipTest("sandbox does not allow binding an ephemeral loopback port")
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        token = base64.b64encode(b"operator:correct horse").decode("ascii")
        self.auth = {"Authorization": f"Basic {token}"}

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.application.close()
        self.temporary.cleanup()

    def test_requires_basic_auth_for_api_and_static_assets(self) -> None:
        for path in ("/", "/api/v1/status"):
            with self.subTest(path=path):
                with self.assertRaises(urllib.error.HTTPError) as raised:
                    urllib.request.urlopen(self.base + path)
                self.assertEqual(401, raised.exception.code)
                self.assertIn("Basic", raised.exception.headers["WWW-Authenticate"])
                raised.exception.close()

        wrong = base64.b64encode(b"operator:wrong password").decode("ascii")
        request = urllib.request.Request(
            self.base + "/api/v1/status",
            headers={"Authorization": f"Basic {wrong}"},
        )
        with self.assertRaises(urllib.error.HTTPError) as raised:
            urllib.request.urlopen(request)
        self.assertEqual(401, raised.exception.code)
        self.assertEqual("no-store", raised.exception.headers["Cache-Control"])
        raised.exception.close()

        response = self._request("/")
        self.assertIn(b"Recorder control room", response.read())
        self.assertEqual("no-store", response.headers["Cache-Control"])

    def test_status_returns_csrf_and_mutations_enforce_it(self) -> None:
        status = json.load(self._request("/api/v1/status"))
        self.assertTrue(status["csrf_token"])
        self.assertEqual("unprepared", status["server"]["state"])

        with self.assertRaises(urllib.error.HTTPError) as raised:
            self._request("/api/v1/server/start", method="POST", body=b"{}")
        self.assertEqual(403, raised.exception.code)
        raised.exception.close()

        headers = {
            "X-MC-Recorder-CSRF": status["csrf_token"],
            "Content-Type": "application/json",
            "Origin": "http://attacker.invalid",
        }
        with self.assertRaises(urllib.error.HTTPError) as raised:
            self._request("/api/v1/server/start", method="POST", body=b"{}", headers=headers)
        self.assertEqual(403, raised.exception.code)
        raised.exception.close()

        headers.pop("Origin")
        with self.assertRaises(urllib.error.HTTPError) as raised:
            self._request("/api/v1/server/start", method="POST", body=b"{}", headers=headers)
        self.assertEqual(403, raised.exception.code)
        raised.exception.close()

        self.application.service.start_server_job = lambda: {
            "id": "00000000-0000-4000-8000-000000000001",
            "kind": "server_start",
            "state": "queued",
        }
        headers["Origin"] = self.base
        response = self._request("/api/v1/server/start", method="POST", body=b"{}", headers=headers)
        self.assertEqual(202, response.status)

    def test_rejects_unknown_and_traversal_routes(self) -> None:
        for path in ("/missing", "/api/v1/datasets/../../recorder.toml"):
            with self.subTest(path=path), self.assertRaises(urllib.error.HTTPError) as raised:
                self._request(path)
            self.assertEqual(404, raised.exception.code)
            raised.exception.close()

    def test_authenticates_unsupported_methods(self) -> None:
        for method in ("HEAD", "PUT", "DELETE", "PATCH", "OPTIONS", "PROPFIND"):
            with self.subTest(method=method):
                request = urllib.request.Request(self.base + "/", method=method)
                with self.assertRaises(urllib.error.HTTPError) as raised:
                    urllib.request.urlopen(request)
                self.assertEqual(401, raised.exception.code)
                self.assertEqual("no-store", raised.exception.headers["Cache-Control"])
                raised.exception.close()

                with self.assertRaises(urllib.error.HTTPError) as raised:
                    self._request("/", method=method)
                self.assertEqual(405, raised.exception.code)
                self.assertEqual("no-store", raised.exception.headers["Cache-Control"])
                raised.exception.close()

    def test_indexes_and_serves_opaque_dataset_and_sample_routes(self) -> None:
        _write_viewer_dataset(self.config.paths.exports)
        self.application.dataset_index.request_refresh()
        deadline = time.monotonic() + 2
        catalog = {"datasets": []}
        while time.monotonic() < deadline and not catalog["datasets"]:
            catalog = json.load(self._request("/api/v1/datasets"))
            if not catalog["datasets"]:
                time.sleep(0.01)
        self.assertEqual(1, len(catalog["datasets"]))
        dataset_id = catalog["datasets"][0]["id"]
        self.assertRegex(dataset_id, r"^[0-9a-f]{32}$")

        metadata = json.load(self._request(f"/api/v1/datasets/{dataset_id}"))
        self.assertEqual("session-http", metadata["session_id"])
        page = json.load(
            self._request(f"/api/v1/datasets/{dataset_id}/samples?limit=1")
        )
        sample_id = page["samples"][0]["sample_id"]
        self.assertRegex(sample_id, r"^[0-9a-f]{32}$")
        detail = json.load(
            self._request(f"/api/v1/datasets/{dataset_id}/samples/{sample_id}")
        )
        self.assertEqual(20, detail["record"]["server_tick"])
        self.assertNotIn("reference", detail["record"]["modalities"]["rgb"])
        with self.assertRaises(urllib.error.HTTPError) as raised:
            self._request(f"/api/v1/datasets/{dataset_id}/samples/{sample_id}/frame")
        self.assertEqual(404, raised.exception.code)
        raised.exception.close()

        with self.assertRaises(urllib.error.HTTPError) as raised:
            self._request(
                f"/api/v1/datasets/{dataset_id}/samples?from_tick=9223372036854775808"
            )
        self.assertEqual(400, raised.exception.code)
        self.assertEqual("no-store", raised.exception.headers["Cache-Control"])
        raised.exception.close()

    def test_render_queue_routes_are_authenticated_scoped_and_bounded(self) -> None:
        status = json.load(self._request("/api/v1/status"))
        headers = {
            "X-MC-Recorder-CSRF": status["csrf_token"],
            "Content-Type": "application/json",
            "Origin": self.base,
        }
        recording_id = "a" * 24
        queued = {
            "id": "11111111-1111-4111-8111-111111111111",
            "recording_id": recording_id,
            "kind": "render_rgb",
            "state": "queued",
        }
        with mock.patch.object(
            self.application.service,
            "create_render_job",
            return_value=queued,
        ) as create:
            response = self._request(
                f"/api/v1/recordings/{recording_id}/render",
                method="POST",
                body=json.dumps({"width": 1280, "height": 720, "fps": 20}).encode(),
                headers=headers,
            )
            self.assertEqual(202, response.status)
            self.assertEqual("queued", json.load(response)["state"])
            create.assert_called_once_with(
                recording_id,
                width=1280,
                height=720,
                fps=20,
                no_gui=False,
                replace_legacy_rgb=False,
            )

        with mock.patch.object(
            self.application.service,
            "create_render_job",
            return_value=queued,
        ) as create_no_gui:
            response = self._request(
                f"/api/v1/recordings/{recording_id}/render",
                method="POST",
                body=json.dumps({"no_gui": True}).encode(),
                headers=headers,
            )
            self.assertEqual(202, response.status)
            create_no_gui.assert_called_once_with(
                recording_id,
                width=640,
                height=360,
                fps=20,
                no_gui=True,
                replace_legacy_rgb=False,
            )

        with mock.patch.object(
            self.application.service,
            "create_render_job",
            return_value=queued,
        ) as replace_legacy:
            response = self._request(
                f"/api/v1/recordings/{recording_id}/render",
                method="POST",
                body=json.dumps({"replace_legacy_rgb": True}).encode(),
                headers=headers,
            )
            self.assertEqual(202, response.status)
            replace_legacy.assert_called_once_with(
                recording_id,
                width=640,
                height=360,
                fps=20,
                no_gui=False,
                replace_legacy_rgb=True,
            )

        with self.assertRaises(urllib.error.HTTPError) as raised:
            self._request(
                f"/api/v1/recordings/{recording_id}/render",
                method="POST",
                body=json.dumps({"no_gui": 0}).encode(),
                headers=headers,
            )
        self.assertEqual(400, raised.exception.code)
        self.assertIn("no_gui must be a boolean", raised.exception.read().decode())
        raised.exception.close()

        with self.assertRaises(urllib.error.HTTPError) as raised:
            self._request(
                f"/api/v1/recordings/{recording_id}/render",
                method="POST",
                body=json.dumps({"replace_legacy_rgb": 1}).encode(),
                headers=headers,
            )
        self.assertEqual(400, raised.exception.code)
        self.assertIn("replace_legacy_rgb must be a boolean", raised.exception.read().decode())
        raised.exception.close()

        with self.assertRaises(urllib.error.HTTPError) as raised:
            self._request(
                f"/api/v1/recordings/{recording_id}/render",
                method="POST",
                body=json.dumps({"width": 640, "replay_path": "/etc/passwd"}).encode(),
                headers=headers,
            )
        self.assertEqual(400, raised.exception.code)
        self.assertIn("unsupported render setting", raised.exception.read().decode())
        raised.exception.close()

        deeply_nested = b'{"width":' + b"[" * 2000 + b"0" + b"]" * 2000 + b"}"
        with self.assertRaises(urllib.error.HTTPError) as raised:
            self._request(
                f"/api/v1/recordings/{recording_id}/render",
                method="POST",
                body=deeply_nested,
                headers=headers,
            )
        self.assertEqual(400, raised.exception.code)
        raised.exception.close()

        payload = {"recording_id": recording_id, "render": {"width": 640, "height": 360, "fps": 20}}
        job = self.application.service.render_queue.create(payload)
        worker = self.application.service.render_queue.register_worker("Ephemeral Mac")
        jobs = json.load(self._request("/api/v1/render-jobs"))
        workers = json.load(self._request("/api/v1/render-workers"))
        self.assertEqual(job["id"], jobs["jobs"][0]["id"])
        self.assertEqual(worker["id"], workers["workers"][0]["id"])
        self.assertNotIn("lease_token", json.dumps(jobs))

        cancel = self._request(
            f"/api/v1/render-jobs/{job['id']}/cancel",
            method="POST",
            body=b"{}",
            headers=headers,
        )
        self.assertEqual("canceled", json.load(cancel)["state"])
        with mock.patch.object(
            self.application.service,
            "retry_render_job",
            return_value={**job, "id": "22222222-2222-4222-8222-222222222222"},
        ) as retry_render:
            retry = self._request(
                f"/api/v1/render-jobs/{job['id']}/retry",
                method="POST",
                body=b"{}",
                headers=headers,
            )
            self.assertEqual("queued", json.load(retry)["state"])
            retry_render.assert_called_once_with(job["id"])

    def _request(
        self,
        path: str,
        *,
        method: str = "GET",
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
    ):
        request = urllib.request.Request(
            self.base + path,
            data=body,
            method=method,
            headers={**self.auth, **(headers or {})},
        )
        return urllib.request.urlopen(request)


class DashboardServeConfigurationTest(unittest.TestCase):
    def test_rgb_recheck_uses_a_bounded_read(self) -> None:
        path = mock.MagicMock()
        handle = mock.MagicMock()
        handle.__enter__.return_value.read.return_value = b"changed!!"
        path.open.return_value = handle
        artifact = VerifiedArtifact(
            path=path,
            size_bytes=8,
            sha256=hashlib.sha256(b"expected").hexdigest(),
            media_type="image/png",
        )

        with self.assertRaisesRegex(DatasetValidationError, "changed after validation"):
            DashboardHandler._read_verified_artifact(artifact)

        handle.__enter__.return_value.read.assert_called_once_with(9)

    def test_rejects_colon_in_basic_auth_username_before_binding(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = load_config(
                initialize(Path(temporary) / "recorder.toml", accept_eula=True)
            )
            with mock.patch.dict(
                os.environ,
                {
                    "MC_RECORDER_DASHBOARD_USERNAME": "operator:admin",
                    "MC_RECORDER_DASHBOARD_PASSWORD": "password:with:colons",
                },
                clear=False,
            ):
                with self.assertRaisesRegex(RecorderError, "may not contain ':'"):
                    serve_dashboard(config)


if __name__ == "__main__":
    unittest.main()
