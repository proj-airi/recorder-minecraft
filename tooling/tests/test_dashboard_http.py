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
from http import HTTPStatus
from pathlib import Path
from types import MappingProxyType
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
from mc_recorder.scene_store import SceneIdentity, SceneStoreBuilder


def _write_viewer_dataset(exports: Path) -> None:
    exports.mkdir(parents=True, exist_ok=True)
    directory = exports / "http-smoke.dataset"
    directory.mkdir()
    scene_directory = directory / "scene"
    scene_directory.mkdir()
    scene_path = scene_directory / "scene-v1.sqlite3"
    identity = SceneIdentity("session-http", "player-http", "connection-http")
    sources = (
        {
            "segment_id": "segment-http",
            "segment_ordinal": 0,
            "path": "/sealed/http-replay.zip",
            "sha256": "ab" * 32,
            "size_bytes": 123,
            "format": "flashback",
        },
    )
    result = {
        "schema_version": 1,
        "result_type": "mc-recorder-scene-extraction-result-v1",
        "status": "complete",
        "job_id": "job-http-test",
        "session_id": identity.session_id,
        "player_uuid": identity.player_uuid,
        "connection_id": identity.connection_id,
        "global_start_tick": 20,
        "global_end_tick": 20,
        "scope": "client_visible",
        "metadata_policy": "full_packet_metadata",
        "flashback_capture_contract": "client_visible_scene_v1",
        "source_replays": list(sources),
        "subject_poses": {
            "format": "mc-recorder-subject-poses-v1",
            "path": "/verified/http-job/subject-poses.jsonl",
            "sha256": "12" * 32,
            "size_bytes": 100,
            "record_count": 1,
            "first_tick": 20,
            "last_tick": 20,
            "source_epochs": [{
                "epoch_index": 0,
                "events_sha256": "34" * 32,
                "events_size_bytes": 1000,
                "record_count": 20,
            }],
        },
        "stream": {
            "format": "mc-recorder-scene-stream-v1",
            "path": "/verified/http-test-stream",
            "frames_index": "frames.jsonl",
            "changes_index": "changes.jsonl",
            "blobs_directory": "blobs",
            "frame_count": 1,
            "change_count": 1,
            "blob_count": 1,
            "blob_bytes": 1,
            "frames_sha256": "cd" * 32,
            "frames_size_bytes": 1,
            "changes_sha256": "ef" * 32,
            "changes_size_bytes": 1,
        },
        "ignored_packet_counts": {},
        "covered_tick_count": 1,
    }
    builder = SceneStoreBuilder(
        identity,
        start_tick=20,
        end_tick=20,
        source_replays=sources,
        provenance={
            "scope": "client_visible",
            "metadata_policy": "full_packet_metadata",
            "result": result,
        },
    )
    try:
        builder.set_section(
            20,
            "minecraft:overworld",
            (0, 4, 0),
            ({"name": "minecraft:grass_block"},),
            (0,) * 4096,
        )
        builder.set_entity(
            20,
            "cow-http",
            {
                "dimension": "minecraft:overworld",
                "type_id": "minecraft:cow",
                "network_id": 3,
                "position": [1.5, 64.0, 2.5],
                "aabb": [1.0, 63.5, 2.0, 2.0, 65.0, 3.0],
            },
        )
        builder.set_block_entity(
            20,
            "minecraft:overworld",
            (2, 64, 2),
            {"type_id": "minecraft:chest"},
        )
        builder.add_frame(
            20,
            frame_id="scene-frame-http-20",
            replay_tick=20,
            dimension="minecraft:overworld",
            subject_position=(1.0, 64.0, 2.0),
        )
        builder.publish(scene_path, expected_ticks=(20,))
    finally:
        builder.close()
    sample = {
        "schema_version": 2,
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
            "player_uuid": "player-http",
            "player_name": "HTTP Player",
            "connection_id": "connection-http",
            "dimension": "minecraft:overworld",
            "position": {"x": 1, "y": 64, "z": 2},
        },
        "action": {
            "ordered_packets": [
                {"action_type": "swing", "payload": {"hand": "main_hand"}},
                {"action_type": "use", "payload": {"hand": "off_hand"}},
            ],
            "reconstructed_control": {
                "server_tick": 21,
                "action_type": "control_state",
                "payload": {
                    "forward": True,
                    "backward": False,
                    "left": False,
                    "right": True,
                    "jump": False,
                    "sneak": False,
                    "sprint": True,
                    "camera_yaw": 90.0,
                    "camera_pitch": 5.0,
                    "camera_delta_yaw": 2.0,
                    "camera_delta_pitch": -1.0,
                    "selected_slot": 2,
                },
            },
        },
        "next_state": {"player_name": "HTTP Player"},
        "next_server_tick": 21,
        "peers": {"state": [], "next_state": []},
        "modalities": {
            "rgb": {"available": False, "valid": False, "reference": None, "reason": "missing"},
            "scene": {
                "available": True,
                "valid": True,
                "coverage_complete": True,
                "reference": "scene/scene-v1.sqlite3",
                "frame_id": "scene-frame-http-20",
                "reason": None,
            },
        },
        "transition_valid": True,
        "transition_invalid_reasons": [],
        "source": {},
        "source_manifest_sha256": "0" * 64,
    }
    state = {
        **sample["state"],
        "schema_version": 2,
        "source_schema_version": 1,
        "session_id": sample["session_id"],
        "epoch_index": sample["epoch_index"],
        "server_tick": sample["server_tick"],
        "sequence": 1,
        "recorded_at_ns": 1,
        "player_uuid": sample["player_uuid"],
        "connection_id": sample["connection_id"],
        "source": {},
    }
    modality = {
        "schema_version": 2,
        "session_id": sample["session_id"],
        "epoch_index": sample["epoch_index"],
        "server_tick": sample["server_tick"],
        "player_uuid": sample["player_uuid"],
        "connection_id": sample["connection_id"],
        "scene": sample["modalities"]["scene"],
        "rgb": sample["modalities"]["rgb"],
        "source": {},
    }
    streams = {
        "samples.jsonl": (json.dumps(sample, separators=(",", ":")) + "\n").encode(),
        "states.jsonl": (json.dumps(state, separators=(",", ":")) + "\n").encode(),
        "actions.jsonl": b"",
        "modalities.jsonl": (json.dumps(modality, separators=(",", ":")) + "\n").encode(),
    }
    for name, data in streams.items():
        (directory / name).write_bytes(data)
    manifest = {
        "schema_version": 2,
        "owner": "mc-recorder",
        "format": "mc-recorder-jsonl-v2",
        "created_at": "2026-07-21T00:00:00+00:00",
        "session_id": "session-http",
        "source": {"sealed_epochs": 1, "active_epochs_skipped": 0},
        "selection": {
            "players": [],
            "from_tick": None,
            "to_tick": None,
            "scene_attachment": None,
        },
        "modalities": {
            "samples": {
                "available": True,
                "records": 1,
                "file": "samples.jsonl",
            },
            "state": {
                "available": True,
                "records": 1,
                "file": "states.jsonl",
            },
            "actions": {
                "available": True,
                "records": 0,
                "file": "actions.jsonl",
            },
            "rgb": {
                "availability": "per-sample",
                "records_attached": 0,
                "index": "modalities.jsonl",
            },
            "scene": {
                "availability": "per-sample",
                "records_attached": 1,
                "index": "modalities.jsonl",
                "store": "scene/scene-v1.sqlite3",
            }
        },
        "files": {
            name: {"size_bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
            for name, data in streams.items()
        },
    }
    scene_data = scene_path.read_bytes()
    manifest["files"]["scene/scene-v1.sqlite3"] = {
        "size_bytes": len(scene_data),
        "sha256": hashlib.sha256(scene_data).hexdigest(),
    }
    manifest["selection"]["scene_attachment"] = {
        "format": "mc-recorder-scene-store-v1",
        "path": "/source/http-scene.sqlite3",
        "sha256": hashlib.sha256(scene_data).hexdigest(),
        "size_bytes": len(scene_data),
        "session_id": identity.session_id,
        "player_uuid": identity.player_uuid,
        "connection_id": identity.connection_id,
        "global_start_tick": 20,
        "global_end_tick": 20,
        "frame_count": 1,
        "scope": "client_visible",
        "metadata_policy": "full_packet_metadata",
        "result": result,
        "sensitive": True,
        "source_replays": list(sources),
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
        body = response.read()
        self.assertIn(b"<title>MC Recorder</title>", body)
        self.assertIn(b'<div id="app">', body)
        self.assertIn(b'type="module"', body)
        self.assertIn(b"requires JavaScript.", body)
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
        self.assertEqual(1, metadata["scene_samples"])
        self.assertEqual("client_visible", metadata["scene_scope"])
        self.assertEqual(
            "full_packet_metadata", metadata["scene_metadata_policy"]
        )
        self.assertIs(metadata["scene_sensitive"], True)
        trajectory = json.load(
            self._request(
                f"/api/v1/datasets/{dataset_id}/trajectory?max_points=10"
            )
        )
        self.assertEqual(1, trajectory["total_points"])
        self.assertEqual(20, trajectory["tracks"][0]["points"][0]["server_tick"])
        page = json.load(
            self._request(f"/api/v1/datasets/{dataset_id}/samples?limit=1")
        )
        sample_id = page["samples"][0]["sample_id"]
        self.assertRegex(sample_id, r"^[0-9a-f]{32}$")
        self.assertTrue(page["samples"][0]["scene_available"])
        detail = json.load(
            self._request(f"/api/v1/datasets/{dataset_id}/samples/{sample_id}")
        )
        self.assertEqual(20, detail["record"]["server_tick"])
        self.assertNotIn("reference", detail["record"]["modalities"]["rgb"])
        self.assertNotIn("reference", detail["record"]["modalities"]["scene"])
        self.assertEqual(
            sample_id, detail["record"]["modalities"]["scene"]["artifact_id"]
        )
        self.assertEqual(
            "client_visible", detail["record"]["modalities"]["scene"]["scope"]
        )
        self.assertEqual(
            "full_packet_metadata",
            detail["record"]["modalities"]["scene"]["metadata_policy"],
        )
        self.assertIs(
            detail["record"]["modalities"]["scene"]["sensitive"], True
        )
        with self.assertRaises(urllib.error.HTTPError) as raised:
            self._request(f"/api/v1/datasets/{dataset_id}/samples/{sample_id}/frame")
        self.assertEqual(404, raised.exception.code)
        raised.exception.close()

        scene_slice = json.load(
            self._request(
                f"/api/v1/datasets/{dataset_id}/samples/{sample_id}/scene-slice"
                "?axis=y&radius=4"
            )
        )
        self.assertEqual((64, 9, 9), (
            scene_slice["coordinate"],
            scene_slice["width"],
            scene_slice["height"],
        ))
        self.assertEqual(81, len(scene_slice["cells"]))
        self.assertGreaterEqual(len(scene_slice["palette"]), 1)
        self.assertEqual("client_visible", scene_slice["scope"])
        self.assertEqual("full_packet_metadata", scene_slice["metadata_policy"])
        self.assertIs(scene_slice["sensitive"], True)
        self.assertTrue(any(cell["color"] for cell in scene_slice["cells"]))
        self.assertTrue(
            all("block_state" not in cell for cell in scene_slice["cells"])
        )
        self.assertTrue(
            all(
                cell["palette_index"] is None
                if not cell["covered"]
                else 0 <= cell["palette_index"] < len(scene_slice["palette"])
                for cell in scene_slice["cells"]
            )
        )
        self.assertEqual("minecraft:cow", scene_slice["entities"][0]["type_id"])
        self.assertEqual(
            "minecraft:chest", scene_slice["block_entities"][0]["type_id"]
        )
        self.assertEqual(
            {"row": 2.5, "column": 1.5},
            {
                key: scene_slice["entities"][0]["projection"][key]
                for key in ("row", "column")
            },
        )
        overridden = json.load(
            self._request(
                f"/api/v1/datasets/{dataset_id}/samples/{sample_id}/scene-slice"
                "?axis=x&coordinate=1&radius=1"
            )
        )
        self.assertEqual(("x", 1, 3, 3), (
            overridden["axis"],
            overridden["coordinate"],
            overridden["width"],
            overridden["height"],
        ))
        scene_page = json.load(
            self._request(
                f"/api/v1/datasets/{dataset_id}/samples?modality=scene"
            )
        )
        self.assertEqual(1, scene_page["total"])
        for invalid_radius in (0, 65):
            with self.subTest(radius=invalid_radius), self.assertRaises(
                urllib.error.HTTPError
            ) as raised:
                self._request(
                    f"/api/v1/datasets/{dataset_id}/samples/{sample_id}/scene-slice"
                    f"?radius={invalid_radius}"
                )
            self.assertEqual(400, raised.exception.code)
            raised.exception.close()

        for query in (
            f"coordinate={10**100}",
            f"coordinate={-(10**100)}",
            f"radius={10**100}",
        ):
            with self.subTest(query=query), self.assertRaises(
                urllib.error.HTTPError
            ) as raised:
                self._request(
                    f"/api/v1/datasets/{dataset_id}/samples/{sample_id}/scene-slice"
                    f"?{query}"
                )
            self.assertEqual(400, raised.exception.code)
            self.assertEqual("no-store", raised.exception.headers["Cache-Control"])
            raised.exception.close()

        with self.assertRaises(urllib.error.HTTPError) as raised:
            self._request(
                f"/api/v1/datasets/{dataset_id}/samples/{sample_id}/voxel-slice"
            )
        self.assertEqual(404, raised.exception.code)
        raised.exception.close()

        with self.assertRaises(urllib.error.HTTPError) as raised:
            self._request(
                f"/api/v1/datasets/{dataset_id}/samples?modality=voxels"
            )
        self.assertEqual(400, raised.exception.code)
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
    def test_serializes_scene_slice_colors_and_projected_summaries(self) -> None:
        block_state = MappingProxyType({"name": "minecraft:stone"})
        value = {
            "tick": 20,
            "dimension": "minecraft:overworld",
            "axis": "y",
            "coordinate": 64,
            "row_axis": "z",
            "column_axis": "x",
            "row_origin": 1,
            "column_origin": 0,
            "width": 2,
            "height": 1,
            "palette": (block_state,),
            "cells": (
                MappingProxyType(
                    {
                        "world_position": (0, 64, 1),
                        "covered": True,
                        "palette_index": 0,
                    }
                ),
                MappingProxyType(
                    {
                        "world_position": (1, 64, 1),
                        "covered": False,
                        "palette_index": None,
                    }
                ),
            ),
            "entities": (
                MappingProxyType(
                    {
                        "type_id": "minecraft:pig",
                        "position": (0.5, 64.0, 1.5),
                        "aabb": (0.1, 63.5, 1.1, 0.9, 64.5, 1.9),
                    }
                ),
            ),
            "block_entities": (
                MappingProxyType(
                    {
                        "type_id": "minecraft:chest",
                        "position": (1, 64, 1),
                    }
                ),
            ),
            "coverage_complete": False,
        }

        with mock.patch.object(
            DashboardHandler,
            "_block_color",
            wraps=DashboardHandler._block_color,
        ) as block_color:
            payload = DashboardHandler._scene_slice_payload(value)

        block_color.assert_called_once_with({"name": "minecraft:stone"})
        self.assertEqual((2, 1), (payload["width"], payload["height"]))
        self.assertEqual(2, len(payload["cells"]))
        self.assertEqual(0, payload["cells"][0]["palette_index"])
        self.assertEqual(3, len(payload["cells"][0]["color"]))
        self.assertIsNone(payload["cells"][1]["color"])
        self.assertNotIn("block_state", payload["cells"][0])
        self.assertEqual(
            {"row": 1.5, "column": 0.5},
            {
                key: payload["entities"][0]["projection"][key]
                for key in ("row", "column")
            },
        )
        self.assertEqual(
            {"row": 1.0, "column": 1.0},
            payload["block_entities"][0]["projection"],
        )

    def test_rejects_oversized_json_response_before_writing(self) -> None:
        handler = object.__new__(DashboardHandler)
        with mock.patch(
            "mc_recorder.dashboard_http.MAX_JSON_RESPONSE_BYTES", 64
        ), mock.patch.object(handler, "_bytes") as write_response:
            with self.assertRaisesRegex(
                DatasetValidationError, "serialized JSON response exceeds 64 bytes"
            ):
                handler._json(HTTPStatus.OK, {"payload": "x" * 128})

        write_response.assert_not_called()

    def test_rejects_invalid_scene_slice_palette_references(self) -> None:
        value = {
            "width": 1,
            "height": 1,
            "palette": [],
            "cells": [
                {
                    "world_position": [0, 64, 0],
                    "covered": True,
                    "palette_index": 0,
                }
            ],
            "row_axis": "z",
            "column_axis": "x",
            "entities": [],
            "block_entities": [],
        }

        with self.assertRaisesRegex(
            DatasetValidationError, "invalid palette entry"
        ):
            DashboardHandler._scene_slice_payload(value)

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
