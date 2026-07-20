from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from mc_recorder.errors import RecorderError
from mc_recorder.render_worker import (
    RemoteRecorder,
    call_remote_json,
    download_replay,
    rsync_download_command,
    rsync_upload_command,
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


if __name__ == "__main__":
    unittest.main()
