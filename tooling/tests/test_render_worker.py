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
    RemoteRecorder,
    call_remote_json,
    create_job_workspace,
    download_replay,
    remove_job_workspace,
    rsync_download_command,
    rsync_upload_command,
    run_ephemeral_worker,
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


if __name__ == "__main__":
    unittest.main()
