from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from minerec.errors import RecorderError
from minerec.render.control.contract import (
    FULL_CLIENT_PRESENTATION_CAPABILITY_KEY,
    FULL_CLIENT_PRESENTATION_CONTRACT,
)
from minerec.workers.render import (
    LocalRecorder,
    _ClaimedJobError,
    _IncompatibleServerError,
    _register_worker,
    create_job_workspace,
    download_hud_sidecar,
    download_replay,
    remove_job_workspace,
    run_render_worker,
    upload_bundle,
)

SERVER_CAPABILITIES = {
    "structured_claim_failure": True,
    FULL_CLIENT_PRESENTATION_CAPABILITY_KEY: FULL_CLIENT_PRESENTATION_CONTRACT,
}


class LocalRecorderTest(unittest.TestCase):
    def test_restricts_paths_to_the_recorder_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            replay = root / "artifacts" / "replays" / "segment.zip"
            replay.parent.mkdir(parents=True)
            replay.write_bytes(b"replay")

            endpoint = LocalRecorder(root)

            self.assertEqual(replay.resolve(), endpoint.contains(replay))
            with self.assertRaisesRegex(RecorderError, "escapes"):
                endpoint.contains(root.parent / "secret.zip")
            with self.assertRaisesRegex(RecorderError, "must be absolute"):
                endpoint.contains("artifacts/replays/segment.zip")


class ReplayTransferTest(unittest.TestCase):
    def test_replay_download_uses_verified_local_content_addressed_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            replay = root / "artifacts" / "replays" / "segment.zip"
            replay.parent.mkdir(parents=True)
            replay_bytes = b"local replay bytes"
            replay.write_bytes(replay_bytes)
            digest = hashlib.sha256(replay_bytes).hexdigest()

            first = download_replay(
                LocalRecorder(root),
                remote_path=str(replay),
                expected_sha256=digest,
                expected_size=len(replay_bytes),
                cache_root=root / "cache",
            )
            second = download_replay(
                LocalRecorder(root),
                remote_path=str(replay),
                expected_sha256=digest,
                expected_size=len(replay_bytes),
                cache_root=root / "cache",
            )

            self.assertEqual(first, second)
            self.assertEqual(replay_bytes, first.read_bytes())

    def test_replay_download_rejects_tampered_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            replay = root / "artifacts" / "replays" / "segment.zip"
            replay.parent.mkdir(parents=True)
            replay.write_bytes(b"tampered")
            expected = b"expected"
            digest = hashlib.sha256(expected).hexdigest()

            with self.assertRaisesRegex(RecorderError, "does not match"):
                download_replay(
                    LocalRecorder(root),
                    remote_path=str(replay),
                    expected_sha256=digest,
                    expected_size=len(expected),
                    cache_root=root / "cache",
                )

    def test_structured_hud_download_is_content_addressed_and_verified(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sidecar = root / ".mc-recorder" / "render-rpc" / "attempts" / "a" / "hud" / "structured-hud.jsonl"
            sidecar.parent.mkdir(parents=True)
            sidecar_bytes = b'{"server_tick":10}\n'
            sidecar.write_bytes(sidecar_bytes)
            digest = hashlib.sha256(sidecar_bytes).hexdigest()

            first = download_hud_sidecar(
                LocalRecorder(root),
                remote_path=str(sidecar),
                expected_sha256=digest,
                expected_size=len(sidecar_bytes),
                cache_root=root / "cache",
            )
            second = download_hud_sidecar(
                LocalRecorder(root),
                remote_path=str(sidecar),
                expected_sha256=digest,
                expected_size=len(sidecar_bytes),
                cache_root=root / "cache",
            )

            self.assertEqual(first, second)
            self.assertEqual(sidecar_bytes, first.read_bytes())

    def test_upload_bundle_copies_only_regular_files_under_the_recorder_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle = root / "bundle"
            bundle.mkdir()
            (bundle / "manifest.json").write_text("{}", encoding="utf-8")
            (bundle / "frames").mkdir()
            (bundle / "frames" / "000001.rgb").write_bytes(b"rgb")

            destination = root / ".mc-recorder" / "render-rpc" / "uploads" / "attempt"
            upload_bundle(
                LocalRecorder(root),
                local_directory=bundle,
                remote_directory=str(destination),
            )

            self.assertEqual("{}", (destination / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(b"rgb", (destination / "frames" / "000001.rgb").read_bytes())

            with self.assertRaisesRegex(RecorderError, "escapes"):
                upload_bundle(
                    LocalRecorder(root),
                    local_directory=bundle,
                    remote_directory=str(root.parent / "outside"),
                )


class RenderWorkerTest(unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        patcher = mock.patch("minerec.workers.render.prepare_renderer_runtime")
        self.prepare_runtime = patcher.start()
        self.addCleanup(patcher.stop)

    def test_workspace_cleanup_requires_the_worker_ownership_marker(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            unrelated = root / "jobs" / "unrelated"
            unrelated.mkdir(parents=True)
            (unrelated / "important.txt").write_text("keep", encoding="utf-8")

            with self.assertRaisesRegex(RecorderError, "unowned"):
                remove_job_workspace(unrelated)
            self.assertTrue((unrelated / "important.txt").is_file())

            owned = create_job_workspace(
                root,
                "00000000-0000-4000-8000-000000000010",
                "00000000-0000-4000-8000-000000000011",
            )
            remove_job_workspace(owned)
            self.assertFalse(owned.exists())

    def test_registration_requires_matching_server_presentation_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            endpoint = LocalRecorder(Path(temporary))
            with (
                mock.patch(
                    "minerec.workers.render.call_recorder_json",
                    return_value={
                        "worker": {"id": "00000000-0000-4000-8000-000000000012"},
                        "server_capabilities": {},
                    },
                ),
                self.assertRaisesRegex(
                    _IncompatibleServerError,
                    "required full-client presentation contract",
                ),
            ):
                _register_worker(endpoint, "00000000-0000-4000-8000-000000000012")

    @mock.patch("minerec.workers.render._RemoteLease.stop")
    @mock.patch("minerec.workers.render._RemoteLease.start")
    @mock.patch("minerec.workers.render.upload_bundle")
    @mock.patch("minerec.workers.render.create_render_bundle")
    @mock.patch("minerec.workers.render.launch_render_job")
    @mock.patch("minerec.workers.render.materialize_portable_render_job")
    @mock.patch("minerec.workers.render.write_portable_render_request")
    @mock.patch("minerec.workers.render.download_replay")
    @mock.patch("minerec.workers.render.call_recorder_json")
    def test_worker_consumes_exact_job_renders_finalizes_and_exits(
        self,
        rpc_mock: mock.Mock,
        download: mock.Mock,
        write_request: mock.Mock,
        materialize: mock.Mock,
        launch: mock.Mock,
        create_bundle: mock.Mock,
        upload: mock.Mock,
        _lease_start: mock.Mock,
        _lease_stop: mock.Mock,
    ) -> None:
        job_id = "00000000-0000-4000-8000-000000000020"
        attempt_id = "00000000-0000-4000-8000-000000000021"
        segment_id = "00000000-0000-4000-8000-000000000022"
        worker_id = "00000000-0000-4000-8000-000000000023"
        request = {"source_replay": {"segment_id": segment_id}}
        recorder_root: Path | None = None

        def rpc(_endpoint: object, arguments: list[str], **_kwargs: object) -> dict[str, object]:
            action = arguments[-1]
            if action == "register":
                return {
                    "worker": {"id": worker_id},
                    "server_capabilities": SERVER_CAPABILITIES,
                }
            if action == "claim":
                assert recorder_root is not None
                return {
                    "claim": {
                        "job": {"id": job_id, "payload": {}},
                        "attempt": {"id": attempt_id, "lease_token": "lease-token"},
                    },
                    "sources": [
                        {
                            "segment_id": segment_id,
                            "segment_ordinal": 0,
                            "path": "/tmp/replay.zip",
                            "sha256": "a" * 64,
                            "size_bytes": 123,
                        }
                    ],
                    "upload_directory": str(recorder_root / "uploads" / "attempt"),
                }
            if action == "request":
                return {"request": request, "request_sha256": "b" * 64, "done": True}
            if action == "heartbeat":
                return {"worker": {"id": worker_id}}
            if action == "finalize":
                return {"job": {"id": job_id, "state": "complete"}}
            self.fail(f"unexpected RPC action {action}")

        portable = SimpleNamespace(
            sha256="b" * 64,
            data=request,
            request_id="00000000-0000-4000-8000-000000000024",
        )
        rpc_mock.side_effect = rpc
        write_request.return_value = portable
        materialize.return_value = SimpleNamespace(directory=Path("/render/job"))
        launch.return_value = {"status": "complete", "global_start_tick": 10}
        create_bundle.return_value = SimpleNamespace(
            request_id=portable.request_id,
            manifest=SimpleNamespace(
                sha256="c" * 64,
                path=Path("/render/bundle/manifest.json"),
            ),
            directory=Path("/render/bundle"),
        )
        download.return_value = Path("/cache/replay.zip")

        with tempfile.TemporaryDirectory() as temporary:
            recorder_root = Path(temporary).resolve()
            endpoint = LocalRecorder(recorder_root)
            result = run_render_worker(
                mock.Mock(),
                endpoint,
                job_id=job_id,
                cache_root=Path(temporary) / "cache",
                once=True,
            )

        self.assertEqual({"job": {"id": job_id, "state": "complete"}}, result)
        self.assertEqual(
            ["register", "claim", "request", "heartbeat", "finalize"],
            [call.args[1][-1] for call in rpc_mock.call_args_list],
        )
        claim_body = rpc_mock.call_args_list[1].kwargs["body"]
        self.assertEqual(job_id, claim_body["job_id"])
        upload.assert_called_once()
        self.prepare_runtime.assert_called_once()

    def test_render_worker_is_one_shot_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(RecorderError, "one-shot"):
                run_render_worker(
                    mock.Mock(),
                    LocalRecorder(Path(temporary)),
                    cache_root=Path(temporary) / "cache",
                )

    def test_exact_job_without_a_claim_requeues_the_rabbitmq_message(self) -> None:
        job_id = "00000000-0000-4000-8000-000000000030"
        worker_id = "00000000-0000-4000-8000-000000000031"

        def rpc(_endpoint: object, arguments: list[str], **_kwargs: object) -> dict[str, object]:
            action = arguments[-1]
            if action == "register":
                return {
                    "worker": {"id": worker_id},
                    "server_capabilities": SERVER_CAPABILITIES,
                }
            if action == "claim":
                return {
                    "claim": None,
                    "sources": [],
                    "upload_directory": None,
                    "reason": "replay_pending",
                    "deferred_job_cooldown_seconds": 30,
                }
            self.fail(f"unexpected RPC action {action}")

        with tempfile.TemporaryDirectory() as temporary:
            with (
                mock.patch("minerec.workers.render.call_recorder_json", side_effect=rpc),
                self.assertRaisesRegex(RecorderError, "not claimable"),
            ):
                run_render_worker(
                    mock.Mock(),
                    LocalRecorder(Path(temporary)),
                    job_id=job_id,
                    cache_root=Path(temporary) / "cache",
                    once=True,
                )

    def test_claimed_job_failure_stops_before_touching_later_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with (
                mock.patch("minerec.workers.render._register_worker"),
                mock.patch(
                    "minerec.workers.render._run_registered_worker_once",
                    side_effect=_ClaimedJobError("renderer is unavailable"),
                ),
                self.assertRaisesRegex(_ClaimedJobError, "renderer is unavailable"),
            ):
                run_render_worker(
                    mock.Mock(),
                    LocalRecorder(Path(temporary)),
                    cache_root=Path(temporary) / "cache",
                    once=True,
                )

    def test_preflight_failure_never_registers_or_claims(self) -> None:
        self.prepare_runtime.side_effect = RecorderError("assets unavailable")
        with tempfile.TemporaryDirectory() as temporary:
            with (
                mock.patch("minerec.workers.render._register_worker") as register,
                mock.patch("minerec.workers.render._run_registered_worker_once") as process,
                self.assertRaisesRegex(RecorderError, "assets unavailable"),
            ):
                run_render_worker(
                    mock.Mock(),
                    LocalRecorder(Path(temporary)),
                    cache_root=Path(temporary) / "cache",
                    once=True,
                )

        register.assert_not_called()
        process.assert_not_called()


if __name__ == "__main__":
    unittest.main()
