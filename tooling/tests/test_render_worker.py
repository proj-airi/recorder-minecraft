from __future__ import annotations

import hashlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from mc_recorder.errors import RecorderError
from mc_recorder.render_worker import (
    _ClaimedJobError,
    _IncompatibleServerError,
    RemoteRecorder,
    call_remote_json,
    create_job_workspace,
    download_replay,
    remove_job_workspace,
    rsync_download_command,
    rsync_upload_command,
    run_ephemeral_worker,
    run_render_worker,
    ssh_rpc_command,
)


class RemoteRecorderTest(unittest.TestCase):
    def test_accepts_an_ssh_alias_and_restricts_transfers_to_the_recorder_root(self) -> None:
        remote = RemoteRecorder.parse("recorder@mcdatacol", "/srv/mc-play-recorder")

        self.assertEqual(
            "/srv/mc-play-recorder/artifacts/replays/segment.zip",
            str(remote.contains("/srv/mc-play-recorder/artifacts/replays/segment.zip")),
        )
        with self.assertRaisesRegex(RecorderError, "escapes|unsafe"):
            remote.contains("/srv/mc-play-recorder/../secret")
        with self.assertRaisesRegex(RecorderError, "must be absolute"):
            remote.contains("artifacts/replays/segment.zip")

    def test_rejects_hosts_and_roots_that_could_be_interpreted_as_arguments(self) -> None:
        for host in ("-oProxyCommand=oops", "server;touch-pwned", "server name"):
            with self.subTest(host=host), self.assertRaisesRegex(RecorderError, "invalid SSH"):
                RemoteRecorder.parse(host, "/srv/mc-play-recorder")
        for root in ("/", "relative", "/srv/../etc", "/srv/path with space"):
            with self.subTest(root=root), self.assertRaisesRegex(RecorderError, "invalid remote"):
                RemoteRecorder.parse("mcdatacol", root)

    def test_rpc_command_is_a_fixed_argv_with_shell_quoted_remote_arguments(self) -> None:
        remote = RemoteRecorder.parse("mcdatacol", "/srv/mc-play-recorder")

        command = ssh_rpc_command(remote, "render-rpc", "claim", "--worker-id", "worker value")

        self.assertEqual(["ssh", "-o", "BatchMode=yes", "--", "mcdatacol"], command[:5])
        self.assertIn("'worker value'", command[5])
        self.assertIn("PYTHONPATH=/srv/mc-play-recorder/tooling/src", command[5])

    def test_rpc_requires_a_bounded_json_object_and_surfaces_remote_errors(self) -> None:
        remote = RemoteRecorder.parse("mcdatacol", "/srv/mc-play-recorder")
        calls: list[dict[str, object]] = []

        def success(*_args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
            calls.append(kwargs)
            return subprocess.CompletedProcess([], 0, stdout='{"job_id":"job"}\n', stderr="")

        response = call_remote_json(remote, ["render-rpc", "claim"], body={"one": 1}, runner=success)
        self.assertEqual({"job_id": "job"}, response)
        self.assertEqual('{"one":1}\n', calls[0]["input"])

        def invalid(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess([], 0, stdout="[]", stderr="")

        with self.assertRaisesRegex(RecorderError, "must be an object"):
            call_remote_json(remote, ["render-rpc", "claim"], runner=invalid)

        def failed(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess([], 23, stdout="", stderr="remote detail")

        with self.assertRaisesRegex(RecorderError, "remote detail"):
            call_remote_json(remote, ["render-rpc", "claim"], runner=failed)


class ReplayTransferTest(unittest.TestCase):
    def test_download_uses_a_content_addressed_cache_and_verifies_before_promotion(self) -> None:
        remote = RemoteRecorder.parse("mcdatacol", "/srv/mc-play-recorder")
        replay = b"stable replay bytes"
        digest = hashlib.sha256(replay).hexdigest()
        calls = 0

        def runner(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
            nonlocal calls
            calls += 1
            Path(argv[-1]).write_bytes(replay)
            return subprocess.CompletedProcess(argv, 0)

        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary) / "cache"
            first = download_replay(
                remote,
                remote_path="/srv/mc-play-recorder/artifacts/replays/segment.zip",
                expected_sha256=digest,
                expected_size=len(replay),
                cache_root=cache,
                runner=runner,
            )
            second = download_replay(
                remote,
                remote_path="/srv/mc-play-recorder/artifacts/replays/segment.zip",
                expected_sha256=digest,
                expected_size=len(replay),
                cache_root=cache,
                runner=runner,
            )

            self.assertEqual(first, second)
            self.assertEqual(replay, first.read_bytes())
            self.assertEqual(1, calls)

    def test_download_deletes_a_tampered_partial_and_never_promotes_it(self) -> None:
        remote = RemoteRecorder.parse("mcdatacol", "/srv/mc-play-recorder")
        expected = b"expected"
        digest = hashlib.sha256(expected).hexdigest()

        def runner(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
            Path(argv[-1]).write_bytes(b"tampered")
            return subprocess.CompletedProcess(argv, 0)

        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary) / "cache"
            with self.assertRaisesRegex(RecorderError, "does not match"):
                download_replay(
                    remote,
                    remote_path="/srv/mc-play-recorder/artifacts/replays/segment.zip",
                    expected_sha256=digest,
                    expected_size=len(expected),
                    cache_root=cache,
                    runner=runner,
                )
            self.assertFalse((cache / "replays" / f".{digest}.zip.inprogress").exists())
            self.assertFalse((cache / "replays" / f"{digest}.zip").exists())

    def test_rsync_commands_do_not_enable_arbitrary_remote_paths(self) -> None:
        remote = RemoteRecorder.parse("mcdatacol", "/srv/mc-play-recorder")
        with tempfile.TemporaryDirectory() as temporary:
            bundle = Path(temporary) / "bundle"
            bundle.mkdir()
            upload = rsync_upload_command(
                remote,
                bundle,
                "/srv/mc-play-recorder/.mc-recorder/render-uploads/job",
            )
            download = rsync_download_command(
                remote,
                "/srv/mc-play-recorder/artifacts/replays/segment.zip",
                Path(temporary) / "partial",
            )

        self.assertEqual("--", upload[-3])
        self.assertEqual(
            "mcdatacol:/srv/mc-play-recorder/.mc-recorder/render-uploads/job/",
            upload[-1],
        )
        self.assertEqual("--", download[-3])
        with self.assertRaisesRegex(RecorderError, "escapes"):
            rsync_download_command(remote, "/etc/passwd", Path("partial"))


class EphemeralWorkerTest(unittest.TestCase):
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

    @mock.patch("mc_recorder.render_worker._RemoteLease.stop")
    @mock.patch("mc_recorder.render_worker._RemoteLease.start")
    @mock.patch("mc_recorder.render_worker.upload_bundle")
    @mock.patch("mc_recorder.render_worker.create_render_bundle")
    @mock.patch("mc_recorder.render_worker.launch_render_job")
    @mock.patch("mc_recorder.render_worker.materialize_portable_render_job")
    @mock.patch("mc_recorder.render_worker.write_portable_render_request")
    @mock.patch("mc_recorder.render_worker.download_replay")
    @mock.patch("mc_recorder.render_worker.call_remote_json")
    def test_worker_claims_one_job_renders_uploads_finalizes_and_exits(
        self,
        remote_json: mock.Mock,
        download: mock.Mock,
        write_request: mock.Mock,
        materialize: mock.Mock,
        launch: mock.Mock,
        create_bundle: mock.Mock,
        upload: mock.Mock,
        _start: mock.Mock,
        _stop: mock.Mock,
    ) -> None:
        job_id = "00000000-0000-4000-8000-000000000020"
        attempt_id = "00000000-0000-4000-8000-000000000021"
        segment_id = "00000000-0000-4000-8000-000000000022"
        source_sha = "a" * 64
        request = {"source_replay": {"segment_id": segment_id}}

        def rpc(_remote: object, arguments: list[str], **_kwargs: object) -> dict[str, object]:
            action = arguments[-1]
            if action == "register":
                return {"worker": {"state": "ready"}}
            if action == "claim":
                return {
                    "claim": {
                        "job": {"id": job_id},
                        "attempt": {"id": attempt_id, "lease_token": "lease-token"},
                    },
                    "sources": [
                        {
                            "segment_id": segment_id,
                            "segment_ordinal": 0,
                            "path": "/srv/mc-play-recorder/.mc-recorder/render-plans/source.zip",
                            "sha256": source_sha,
                            "size_bytes": 123,
                        }
                    ],
                    "upload_directory": (
                        "/srv/mc-play-recorder/.mc-recorder/render-uploads/attempt"
                    ),
                }
            if action == "request":
                return {
                    "request": request,
                    "request_sha256": "b" * 64,
                    "segment_id": segment_id,
                    # A terminal source still carries a request that must be
                    # rendered. Only request=null, done=true means stop.
                    "done": True,
                }
            if action == "heartbeat":
                return {"job": {"state": "uploading"}}
            if action == "finalize":
                return {"job": {"id": job_id, "state": "complete"}}
            self.fail(f"unexpected RPC action {action}")

        remote_json.side_effect = rpc
        download.return_value = Path("/cache/replay.zip")
        portable = SimpleNamespace(
            sha256="b" * 64,
            data=request,
            request_id="00000000-0000-4000-8000-000000000023",
        )
        write_request.return_value = portable
        render_job = SimpleNamespace(directory=Path("/render/job"))
        materialize.return_value = render_job
        launch.return_value = {"status": "complete", "global_start_tick": 100}
        create_bundle.return_value = SimpleNamespace(request_id=portable.request_id)
        remote = RemoteRecorder.parse("mcdatacol", "/srv/mc-play-recorder")

        with tempfile.TemporaryDirectory() as temporary:
            result = run_ephemeral_worker(
                mock.Mock(),
                remote,
                job_id=job_id,
                cache_root=Path(temporary),
            )
            jobs_root = Path(temporary) / "jobs"
            self.assertEqual([], list(jobs_root.iterdir()))

        self.assertEqual("complete", result["job"]["state"])
        download.assert_called_once()
        launch.assert_called_once_with(mock.ANY, render_job)
        upload.assert_called_once()
        actions = [call.args[1][-1] for call in remote_json.call_args_list]
        self.assertEqual(
            ["register", "claim", "request", "heartbeat", "finalize"],
            actions,
        )
        registration = remote_json.call_args_list[0].kwargs["body"]
        self.assertIs(
            registration["capabilities"]["portable_request_no_gui"],
            True,
        )
        self.assertIs(
            registration["capabilities"]["structured_claim_failure"],
            True,
        )

    def test_worker_does_not_download_an_older_segment_already_covered(self) -> None:
        job_id = "00000000-0000-4000-8000-000000000030"
        attempt_id = "00000000-0000-4000-8000-000000000031"
        newest = "00000000-0000-4000-8000-000000000032"
        older = "00000000-0000-4000-8000-000000000033"
        request = {"source_replay": {"segment_id": newest}}
        request_count = 0

        def rpc(_remote: object, arguments: list[str], **_kwargs: object) -> dict[str, object]:
            nonlocal request_count
            action = arguments[-1]
            if action == "register":
                return {"worker": {"state": "ready"}}
            if action == "claim":
                return {
                    "claim": {
                        "job": {"id": job_id},
                        "attempt": {"id": attempt_id, "lease_token": "lease-token"},
                    },
                    "sources": [
                        {
                            "segment_id": newest,
                            "segment_ordinal": 2,
                            "path": f"/srv/mc-play-recorder/.mc-recorder/{newest}.zip",
                            "sha256": "a" * 64,
                            "size_bytes": 123,
                        },
                        {
                            "segment_id": older,
                            "segment_ordinal": 1,
                            "path": f"/srv/mc-play-recorder/.mc-recorder/{older}.zip",
                            "sha256": "b" * 64,
                            "size_bytes": 456,
                        },
                    ],
                    "upload_directory": "/srv/mc-play-recorder/.mc-recorder/uploads/attempt",
                }
            if action == "request":
                request_count += 1
                if request_count == 1:
                    return {
                        "request": request,
                        "request_sha256": "c" * 64,
                        "segment_id": newest,
                        "done": False,
                    }
                return {
                    "request": None,
                    "request_sha256": None,
                    "segment_id": older,
                    "done": True,
                }
            if action == "heartbeat":
                return {"job": {"state": "uploading"}}
            if action == "finalize":
                return {"job": {"id": job_id, "state": "complete"}}
            self.fail(f"unexpected RPC action {action}")

        portable = SimpleNamespace(
            sha256="c" * 64,
            data=request,
            request_id="00000000-0000-4000-8000-000000000034",
        )
        render_job = SimpleNamespace(directory=Path("/render/job"))
        remote = RemoteRecorder.parse("mcdatacol", "/srv/mc-play-recorder")
        with (
            tempfile.TemporaryDirectory() as temporary,
            mock.patch("mc_recorder.render_worker.call_remote_json", side_effect=rpc),
            mock.patch("mc_recorder.render_worker._RemoteLease.start"),
            mock.patch("mc_recorder.render_worker._RemoteLease.stop"),
            mock.patch(
                "mc_recorder.render_worker.download_replay",
                return_value=Path("/cache/newest.zip"),
            ) as download,
            mock.patch(
                "mc_recorder.render_worker.write_portable_render_request",
                return_value=portable,
            ),
            mock.patch(
                "mc_recorder.render_worker.materialize_portable_render_job",
                return_value=render_job,
            ),
            mock.patch(
                "mc_recorder.render_worker.launch_render_job",
                return_value={"status": "complete", "global_start_tick": 0},
            ),
            mock.patch(
                "mc_recorder.render_worker.create_render_bundle",
                return_value=SimpleNamespace(request_id=portable.request_id),
            ),
            mock.patch("mc_recorder.render_worker.upload_bundle"),
        ):
            result = run_ephemeral_worker(
                mock.Mock(), remote, job_id=job_id, cache_root=Path(temporary)
            )

        self.assertEqual("complete", result["job"]["state"])
        download.assert_called_once()
        self.assertEqual(newest, request["source_replay"]["segment_id"])


class PersistentWorkerTest(unittest.TestCase):
    def test_presence_heartbeat_spans_the_persistent_loop(self) -> None:
        presence = mock.Mock()
        with (
            mock.patch("mc_recorder.render_worker._WorkerPresence", return_value=presence),
            mock.patch("mc_recorder.render_worker._register_worker"),
            mock.patch(
                "mc_recorder.render_worker._run_registered_worker_once",
                return_value=SimpleNamespace(result=None, reason=None),
            ),
            self.assertRaises(KeyboardInterrupt),
        ):
            run_render_worker(
                mock.Mock(),
                RemoteRecorder.parse("mcdatacol", "/srv/mc-play-recorder"),
                cache_root=Path("/cache"),
                waiter=lambda _delay: (_ for _ in ()).throw(KeyboardInterrupt()),
            )

        presence.start.assert_called_once_with()
        presence.stop.assert_called_once_with()

    def test_registers_one_persistent_process_identity(self) -> None:
        worker_id = "00000000-0000-4000-8000-000000000070"

        def rpc(
            _remote: object, arguments: list[str], **_kwargs: object
        ) -> dict[str, object]:
            if arguments[-1] == "register":
                return {
                    "worker": {"id": worker_id},
                    "server_capabilities": {"structured_claim_failure": True},
                }
            if arguments[-1] == "claim":
                return {"claim": None, "sources": [], "upload_directory": None}
            self.fail(f"unexpected RPC action {arguments[-1]}")

        with (
            mock.patch(
                "mc_recorder.render_worker.ephemeral_worker_id",
                return_value=worker_id,
            ),
            mock.patch(
                "mc_recorder.render_worker.call_remote_json",
                side_effect=rpc,
            ) as remote_json,
            self.assertRaises(KeyboardInterrupt),
        ):
            run_render_worker(
                mock.Mock(),
                RemoteRecorder.parse("mcdatacol", "/srv/mc-play-recorder"),
                cache_root=Path("/cache"),
                waiter=lambda _delay: (_ for _ in ()).throw(KeyboardInterrupt()),
            )

        registration = remote_json.call_args_list[0].kwargs["body"]
        self.assertEqual(worker_id, registration["worker_id"])
        self.assertTrue(registration["capabilities"]["persistent"])
        self.assertFalse(registration["capabilities"]["ephemeral"])
        self.assertTrue(
            registration["capabilities"]["structured_claim_failure"]
        )
        claim = remote_json.call_args_list[1].kwargs["body"]
        self.assertEqual(worker_id, claim["worker_id"])

    def test_persistent_worker_rejects_a_server_without_safe_claim_failures(self) -> None:
        waiter = mock.Mock()

        def rpc(
            _remote: object, arguments: list[str], **_kwargs: object
        ) -> dict[str, object]:
            if arguments[-1] == "register":
                return {"worker": {"state": "online"}}
            self.fail(f"persistent worker should not call {arguments[-1]}")

        with (
            mock.patch(
                "mc_recorder.render_worker.call_remote_json",
                side_effect=rpc,
            ) as remote_json,
            self.assertRaisesRegex(
                _IncompatibleServerError, "update the server tooling or use --once"
            ),
        ):
            run_render_worker(
                mock.Mock(),
                RemoteRecorder.parse("mcdatacol", "/srv/mc-play-recorder"),
                cache_root=Path("/cache"),
                waiter=waiter,
            )

        self.assertEqual(["register"], [call.args[1][-1] for call in remote_json.call_args_list])
        waiter.assert_not_called()

    def test_lost_claim_response_stops_without_retrying_the_queue(self) -> None:
        waiter = mock.Mock()

        def rpc(
            _remote: object, arguments: list[str], **_kwargs: object
        ) -> dict[str, object]:
            if arguments[-1] == "register":
                return {
                    "worker": {"state": "online"},
                    "server_capabilities": {"structured_claim_failure": True},
                }
            if arguments[-1] == "claim":
                raise RecorderError("SSH timed out after sending the request")
            self.fail(f"unexpected RPC action {arguments[-1]}")

        with (
            mock.patch(
                "mc_recorder.render_worker.call_remote_json",
                side_effect=rpc,
            ) as remote_json,
            mock.patch("mc_recorder.render_worker._WorkerPresence.start"),
            mock.patch("mc_recorder.render_worker._WorkerPresence.stop"),
            self.assertRaisesRegex(_ClaimedJobError, "claim outcome is unknown"),
        ):
            run_render_worker(
                mock.Mock(),
                RemoteRecorder.parse("mcdatacol", "/srv/mc-play-recorder"),
                cache_root=Path("/cache"),
                waiter=waiter,
            )

        self.assertEqual(
            ["register", "claim"],
            [call.args[1][-1] for call in remote_json.call_args_list],
        )
        waiter.assert_not_called()

    def test_malformed_response_after_claim_is_failed_and_stops(self) -> None:
        worker_id = "00000000-0000-4000-8000-000000000090"
        job_id = "00000000-0000-4000-8000-000000000091"
        attempt_id = "00000000-0000-4000-8000-000000000092"

        def rpc(
            _remote: object, arguments: list[str], **_kwargs: object
        ) -> dict[str, object]:
            action = arguments[-1]
            if action == "register":
                return {
                    "worker": {"id": worker_id},
                    "server_capabilities": {"structured_claim_failure": True},
                }
            if action == "claim":
                return {
                    "claim": {
                        "job": {"id": job_id},
                        "attempt": {
                            "id": attempt_id,
                            "lease_token": "lease-token",
                        },
                    },
                    "sources": [{"segment_id": "not-a-uuid"}],
                    "upload_directory": "/srv/mc-play-recorder/uploads",
                }
            if action == "fail":
                return {"job": {"id": job_id, "state": "failed"}}
            self.fail(f"unexpected RPC action {action}")

        with (
            mock.patch(
                "mc_recorder.render_worker.ephemeral_worker_id",
                return_value=worker_id,
            ),
            mock.patch(
                "mc_recorder.render_worker.call_remote_json",
                side_effect=rpc,
            ) as remote_json,
            mock.patch("mc_recorder.render_worker._WorkerPresence.start"),
            mock.patch("mc_recorder.render_worker._WorkerPresence.stop"),
            self.assertRaisesRegex(_ClaimedJobError, "invalid response"),
        ):
            run_render_worker(
                mock.Mock(),
                RemoteRecorder.parse("mcdatacol", "/srv/mc-play-recorder"),
                cache_root=Path("/cache"),
            )

        self.assertEqual(
            ["register", "claim", "fail"],
            [call.args[1][-1] for call in remote_json.call_args_list],
        )

    def test_reuses_one_registration_while_processing_jobs_sequentially(self) -> None:
        worker_id = "00000000-0000-4000-8000-000000000040"
        first = {"job": {"id": "00000000-0000-4000-8000-000000000041"}}
        second = {"job": {"id": "00000000-0000-4000-8000-000000000042"}}
        polls = [
            SimpleNamespace(result=first, reason=None),
            SimpleNamespace(result=second, reason=None),
            SimpleNamespace(result=None, reason=None),
        ]
        results: list[dict[str, object]] = []
        waits: list[float] = []

        def stop_after_idle(delay: float) -> None:
            waits.append(delay)
            raise KeyboardInterrupt

        with (
            mock.patch(
                "mc_recorder.render_worker.ephemeral_worker_id",
                return_value=worker_id,
            ),
            mock.patch("mc_recorder.render_worker._register_worker") as register,
            mock.patch(
                "mc_recorder.render_worker._run_registered_worker_once",
                side_effect=polls,
            ) as process,
            self.assertRaises(KeyboardInterrupt),
        ):
            run_render_worker(
                mock.Mock(),
                RemoteRecorder.parse("mcdatacol", "/srv/mc-play-recorder"),
                cache_root=Path("/cache"),
                on_result=results.append,
                waiter=stop_after_idle,
            )

        register.assert_called_once_with(mock.ANY, worker_id, persistent=True)
        self.assertEqual([first, second], results)
        self.assertEqual([10.0], waits)
        self.assertEqual(3, process.call_count)
        self.assertEqual(
            {worker_id},
            {call.kwargs["worker_id"] for call in process.call_args_list},
        )

    def test_replay_pending_immediately_scans_for_later_ready_jobs(self) -> None:
        waits: list[float] = []

        def stop(delay: float) -> None:
            waits.append(delay)
            raise KeyboardInterrupt

        with (
            mock.patch("mc_recorder.render_worker._register_worker"),
            mock.patch(
                "mc_recorder.render_worker._run_registered_worker_once",
                side_effect=[
                    SimpleNamespace(
                        result=None,
                        reason="replay_pending",
                        deferred_job_cooldown_seconds=30,
                    ),
                    SimpleNamespace(result=None, reason=None),
                ],
            ) as process,
            self.assertRaises(KeyboardInterrupt),
        ):
            run_render_worker(
                mock.Mock(),
                RemoteRecorder.parse("mcdatacol", "/srv/mc-play-recorder"),
                cache_root=Path("/cache"),
                poll_interval=4,
                waiter=stop,
            )

        self.assertEqual([4.0], waits)
        self.assertEqual(2, process.call_count)

    def test_legacy_server_pending_response_sleeps_instead_of_hot_looping(self) -> None:
        waits: list[float] = []

        def stop(delay: float) -> None:
            waits.append(delay)
            raise KeyboardInterrupt

        with (
            mock.patch("mc_recorder.render_worker._register_worker"),
            mock.patch(
                "mc_recorder.render_worker._run_registered_worker_once",
                return_value=SimpleNamespace(
                    result=None,
                    reason="replay_pending",
                    deferred_job_cooldown_seconds=None,
                ),
            ) as process,
            self.assertRaises(KeyboardInterrupt),
        ):
            run_render_worker(
                mock.Mock(),
                RemoteRecorder.parse("mcdatacol", "/srv/mc-play-recorder"),
                cache_root=Path("/cache"),
                poll_interval=6,
                waiter=stop,
            )

        self.assertEqual([6.0], waits)
        process.assert_called_once()

    def test_claimed_job_failure_stops_before_touching_later_jobs(self) -> None:
        waiter = mock.Mock()
        on_error = mock.Mock()
        with (
            mock.patch("mc_recorder.render_worker._register_worker") as register,
            mock.patch(
                "mc_recorder.render_worker._run_registered_worker_once",
                side_effect=_ClaimedJobError("renderer is unavailable"),
            ) as process,
            self.assertRaisesRegex(_ClaimedJobError, "renderer is unavailable"),
        ):
            run_render_worker(
                mock.Mock(),
                RemoteRecorder.parse("mcdatacol", "/srv/mc-play-recorder"),
                cache_root=Path("/cache"),
                waiter=waiter,
                on_error=on_error,
            )

        register.assert_called_once()
        process.assert_called_once()
        waiter.assert_not_called()
        on_error.assert_not_called()

    def test_server_claim_preparation_failure_is_fail_stop(self) -> None:
        failed_job = "00000000-0000-4000-8000-000000000075"
        waiter = mock.Mock()
        with (
            mock.patch("mc_recorder.render_worker._register_worker"),
            mock.patch("mc_recorder.render_worker._WorkerPresence.start"),
            mock.patch("mc_recorder.render_worker._WorkerPresence.stop"),
            mock.patch(
                "mc_recorder.render_worker.call_remote_json",
                return_value={
                    "claim": None,
                    "sources": [],
                    "upload_directory": None,
                    "failed_job": {"id": failed_job, "state": "failed"},
                    "reason": "claim_failed",
                    "error": "server render disk is full",
                },
            ),
            self.assertRaisesRegex(_ClaimedJobError, "disk is full"),
        ):
            run_render_worker(
                mock.Mock(),
                RemoteRecorder.parse("mcdatacol", "/srv/mc-play-recorder"),
                cache_root=Path("/cache"),
                waiter=waiter,
            )

        waiter.assert_not_called()

    def test_malformed_server_claim_failure_is_still_fail_stop(self) -> None:
        waiter = mock.Mock()
        with (
            mock.patch("mc_recorder.render_worker._register_worker"),
            mock.patch("mc_recorder.render_worker._WorkerPresence.start"),
            mock.patch("mc_recorder.render_worker._WorkerPresence.stop"),
            mock.patch(
                "mc_recorder.render_worker.call_remote_json",
                return_value={
                    "claim": None,
                    "sources": [],
                    "upload_directory": None,
                    "reason": "claim_failed",
                    "error": "bad\u0000error",
                },
            ),
            self.assertRaisesRegex(
                _ClaimedJobError, "claimed job failure with invalid details"
            ),
        ):
            run_render_worker(
                mock.Mock(),
                RemoteRecorder.parse("mcdatacol", "/srv/mc-play-recorder"),
                cache_root=Path("/cache"),
                waiter=waiter,
            )

        waiter.assert_not_called()

    def test_real_claim_failure_is_fenced_cleaned_and_stops_the_loop(self) -> None:
        worker_id = "00000000-0000-4000-8000-000000000080"
        job_id = "00000000-0000-4000-8000-000000000081"
        attempt_id = "00000000-0000-4000-8000-000000000082"
        segment_id = "00000000-0000-4000-8000-000000000083"
        request = {"source_replay": {"segment_id": segment_id}}

        def rpc(
            _remote: object, arguments: list[str], **_kwargs: object
        ) -> dict[str, object]:
            action = arguments[-1]
            if action == "register":
                return {
                    "worker": {"id": worker_id},
                    "server_capabilities": {"structured_claim_failure": True},
                }
            if action == "claim":
                return {
                    "claim": {
                        "job": {"id": job_id},
                        "attempt": {
                            "id": attempt_id,
                            "lease_token": "lease-token",
                        },
                    },
                    "sources": [
                        {
                            "segment_id": segment_id,
                            "segment_ordinal": 0,
                            "path": f"/srv/mc-play-recorder/.mc-recorder/{segment_id}.zip",
                            "sha256": "a" * 64,
                            "size_bytes": 123,
                        }
                    ],
                    "upload_directory": "/srv/mc-play-recorder/.mc-recorder/uploads/attempt",
                }
            if action == "request":
                return {
                    "request": request,
                    "request_sha256": "b" * 64,
                    "done": True,
                }
            if action == "fail":
                return {"job": {"id": job_id, "state": "failed"}}
            self.fail(f"unexpected RPC action {action}")

        portable = SimpleNamespace(
            sha256="b" * 64,
            data=request,
            request_id="00000000-0000-4000-8000-000000000084",
        )
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        cache = Path(temporary.name)
        with (
            mock.patch(
                "mc_recorder.render_worker.ephemeral_worker_id",
                return_value=worker_id,
            ),
            mock.patch(
                "mc_recorder.render_worker.call_remote_json",
                side_effect=rpc,
            ) as remote_json,
            mock.patch("mc_recorder.render_worker._WorkerPresence.start"),
            mock.patch("mc_recorder.render_worker._WorkerPresence.stop"),
            mock.patch("mc_recorder.render_worker._RemoteLease.start"),
            mock.patch("mc_recorder.render_worker._RemoteLease.stop"),
            mock.patch(
                "mc_recorder.render_worker.download_replay",
                return_value=Path("/cache/replay.zip"),
            ),
            mock.patch(
                "mc_recorder.render_worker.write_portable_render_request",
                return_value=portable,
            ),
            mock.patch(
                "mc_recorder.render_worker.materialize_portable_render_job",
                return_value=SimpleNamespace(directory=Path("/render/job")),
            ),
            mock.patch(
                "mc_recorder.render_worker.launch_render_job",
                side_effect=RecorderError("Java runtime is unavailable"),
            ),
            self.assertRaisesRegex(_ClaimedJobError, "Java runtime is unavailable"),
        ):
            run_render_worker(
                mock.Mock(),
                RemoteRecorder.parse("mcdatacol", "/srv/mc-play-recorder"),
                cache_root=cache,
            )

        actions = [call.args[1][-1] for call in remote_json.call_args_list]
        self.assertEqual(["register", "claim", "request", "fail"], actions)
        self.assertEqual([], list((cache / "jobs").iterdir()))

    def test_interrupt_during_claimed_job_cleans_up_without_failing_it(self) -> None:
        worker_id = "00000000-0000-4000-8000-0000000000a0"
        job_id = "00000000-0000-4000-8000-0000000000a1"
        attempt_id = "00000000-0000-4000-8000-0000000000a2"
        segment_id = "00000000-0000-4000-8000-0000000000a3"

        def rpc(
            _remote: object, arguments: list[str], **_kwargs: object
        ) -> dict[str, object]:
            action = arguments[-1]
            if action == "register":
                return {
                    "worker": {"id": worker_id},
                    "server_capabilities": {"structured_claim_failure": True},
                }
            if action == "claim":
                return {
                    "claim": {
                        "job": {"id": job_id},
                        "attempt": {
                            "id": attempt_id,
                            "lease_token": "lease-token",
                        },
                    },
                    "sources": [
                        {
                            "segment_id": segment_id,
                            "segment_ordinal": 0,
                            "path": f"/srv/mc-play-recorder/replays/{segment_id}.zip",
                            "sha256": "a" * 64,
                            "size_bytes": 123,
                        }
                    ],
                    "upload_directory": "/srv/mc-play-recorder/uploads/attempt",
                }
            if action == "request":
                return {"request": {}, "request_sha256": "b" * 64, "done": False}
            self.fail(f"interrupt must not call {action}")

        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary)
            with (
                mock.patch(
                    "mc_recorder.render_worker.ephemeral_worker_id",
                    return_value=worker_id,
                ),
                mock.patch(
                    "mc_recorder.render_worker.call_remote_json",
                    side_effect=rpc,
                ) as remote_json,
                mock.patch("mc_recorder.render_worker._WorkerPresence.start") as presence_start,
                mock.patch("mc_recorder.render_worker._WorkerPresence.stop") as presence_stop,
                mock.patch("mc_recorder.render_worker._RemoteLease.start") as lease_start,
                mock.patch("mc_recorder.render_worker._RemoteLease.stop") as lease_stop,
                mock.patch(
                    "mc_recorder.render_worker.download_replay",
                    side_effect=KeyboardInterrupt,
                ),
                self.assertRaises(KeyboardInterrupt),
            ):
                run_render_worker(
                    mock.Mock(),
                    RemoteRecorder.parse("mcdatacol", "/srv/mc-play-recorder"),
                    cache_root=cache,
                )

            self.assertEqual([], list((cache / "jobs").iterdir()))

        self.assertEqual(
            ["register", "claim", "request"],
            [call.args[1][-1] for call in remote_json.call_args_list],
        )
        presence_start.assert_called_once_with()
        presence_stop.assert_called_once_with()
        lease_start.assert_called_once_with()
        lease_stop.assert_called_once_with()

    def test_cleanup_failure_cannot_escape_the_claimed_job_fail_stop(self) -> None:
        worker_id = "00000000-0000-4000-8000-0000000000b0"
        job_id = "00000000-0000-4000-8000-0000000000b1"
        attempt_id = "00000000-0000-4000-8000-0000000000b2"
        segment_id = "00000000-0000-4000-8000-0000000000b3"

        def rpc(
            _remote: object, arguments: list[str], **_kwargs: object
        ) -> dict[str, object]:
            action = arguments[-1]
            if action == "register":
                return {
                    "worker": {"id": worker_id},
                    "server_capabilities": {"structured_claim_failure": True},
                }
            if action == "claim":
                return {
                    "claim": {
                        "job": {"id": job_id},
                        "attempt": {
                            "id": attempt_id,
                            "lease_token": "lease-token",
                        },
                    },
                    "sources": [
                        {
                            "segment_id": segment_id,
                            "segment_ordinal": 0,
                            "path": f"/srv/mc-play-recorder/replays/{segment_id}.zip",
                            "sha256": "a" * 64,
                            "size_bytes": 123,
                        }
                    ],
                    "upload_directory": "/srv/mc-play-recorder/uploads/attempt",
                }
            if action == "request":
                return {"request": {}, "request_sha256": "b" * 64, "done": False}
            if action == "fail":
                return {"job": {"id": job_id, "state": "failed"}}
            self.fail(f"unexpected RPC action {action}")

        with tempfile.TemporaryDirectory() as temporary:
            with (
                mock.patch(
                    "mc_recorder.render_worker.ephemeral_worker_id",
                    return_value=worker_id,
                ),
                mock.patch(
                    "mc_recorder.render_worker.call_remote_json",
                    side_effect=rpc,
                ) as remote_json,
                mock.patch("mc_recorder.render_worker._WorkerPresence.start"),
                mock.patch("mc_recorder.render_worker._WorkerPresence.stop"),
                mock.patch("mc_recorder.render_worker._RemoteLease.start"),
                mock.patch("mc_recorder.render_worker._RemoteLease.stop"),
                mock.patch(
                    "mc_recorder.render_worker.download_replay",
                    side_effect=RecorderError("download failed"),
                ),
                mock.patch(
                    "mc_recorder.render_worker.remove_job_workspace",
                    side_effect=RecorderError("workspace is no longer owned"),
                ),
                self.assertRaisesRegex(_ClaimedJobError, "download failed") as raised,
            ):
                run_render_worker(
                    mock.Mock(),
                    RemoteRecorder.parse("mcdatacol", "/srv/mc-play-recorder"),
                    cache_root=Path(temporary),
                )

        self.assertEqual(
            ["register", "claim", "request", "fail"],
            [call.args[1][-1] for call in remote_json.call_args_list],
        )
        notes = getattr(raised.exception, "__notes__", [])
        self.assertTrue(any("cleanup also failed" in note for note in notes))

    def test_errors_back_off_reregister_and_continue(self) -> None:
        worker_id = "00000000-0000-4000-8000-000000000050"
        completed = {"job": {"id": "00000000-0000-4000-8000-000000000051"}}
        errors: list[tuple[str, float]] = []
        waits: list[float] = []

        def wait_or_stop(delay: float) -> None:
            waits.append(delay)
            if len(waits) == 3:
                raise KeyboardInterrupt

        with (
            mock.patch(
                "mc_recorder.render_worker.ephemeral_worker_id",
                return_value=worker_id,
            ),
            mock.patch("mc_recorder.render_worker._register_worker") as register,
            mock.patch(
                "mc_recorder.render_worker._run_registered_worker_once",
                side_effect=[
                    RecorderError("network one"),
                    RecorderError("network two"),
                    SimpleNamespace(result=completed, reason=None),
                    SimpleNamespace(result=None, reason=None),
                ],
            ),
            self.assertRaises(KeyboardInterrupt),
        ):
            run_render_worker(
                mock.Mock(),
                RemoteRecorder.parse("mcdatacol", "/srv/mc-play-recorder"),
                cache_root=Path("/cache"),
                on_error=lambda message, delay: errors.append((message, delay)),
                waiter=wait_or_stop,
            )

        self.assertEqual(
            [("network one", 2.0), ("network two", 4.0)],
            errors,
        )
        self.assertEqual([2.0, 4.0, 10.0], waits)
        self.assertEqual(3, register.call_count)
        self.assertEqual(
            {worker_id},
            {call.args[1] for call in register.call_args_list},
        )

    def test_selected_job_is_always_one_shot(self) -> None:
        job_id = "00000000-0000-4000-8000-000000000060"
        completed = {"job": {"id": job_id, "state": "complete"}}
        with (
            mock.patch("mc_recorder.render_worker._register_worker") as register,
            mock.patch(
                "mc_recorder.render_worker._run_registered_worker_once",
                return_value=SimpleNamespace(result=completed, reason=None),
            ) as process,
        ):
            result = run_render_worker(
                mock.Mock(),
                RemoteRecorder.parse("mcdatacol", "/srv/mc-play-recorder"),
                job_id=job_id,
                cache_root=Path("/cache"),
            )

        self.assertEqual(completed, result)
        register.assert_called_once_with(mock.ANY, mock.ANY, persistent=False)
        self.assertEqual(job_id, process.call_args.kwargs["job_id"])

    def test_poll_interval_is_bounded_below_the_worker_stale_threshold(self) -> None:
        remote = RemoteRecorder.parse("mcdatacol", "/srv/mc-play-recorder")
        for invalid in (0, 31, True, "10"):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(
                RecorderError, "poll interval"
            ):
                run_render_worker(
                    mock.Mock(),
                    remote,
                    cache_root=Path("/cache"),
                    poll_interval=invalid,  # type: ignore[arg-type]
                    once=True,
                )


if __name__ == "__main__":
    unittest.main()
