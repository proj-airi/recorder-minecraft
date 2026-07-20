from __future__ import annotations

import io
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from mc_recorder import cli
from mc_recorder.errors import RecorderError


class _Input:
    def __init__(self, raw: bytes):
        self.buffer = io.BytesIO(raw)


class RenderCliTest(unittest.TestCase):
    def test_internal_rpc_accepts_only_a_bounded_json_object(self) -> None:
        with mock.patch.object(cli.sys, "stdin", _Input(b'{"worker_id":"one"}\n')):
            self.assertEqual({"worker_id": "one"}, cli._read_render_rpc_body())

        for raw in (b"[]\n", b'{"number":NaN}\n', b"{" + b" " * (64 * 1024)):
            with self.subTest(length=len(raw)), mock.patch.object(
                cli.sys, "stdin", _Input(raw)
            ), self.assertRaises(RecorderError):
                cli._read_render_rpc_body()

    def test_render_rpc_dispatches_stdin_and_prints_one_json_response(self) -> None:
        config = object()
        body = {
            "worker_id": "11111111-1111-4111-8111-111111111111",
            "name": "one-shot",
            "capabilities": {},
        }
        output = io.StringIO()
        with (
            mock.patch.object(cli, "load_config", return_value=config),
            mock.patch.object(cli, "dispatch_render_rpc", return_value={"worker": {"state": "ready"}}) as dispatch,
            mock.patch.object(cli.sys, "stdin", _Input(json.dumps(body).encode())),
            mock.patch.object(cli.sys, "stdout", output),
        ):
            code = cli.run(["render-rpc", "register"])

        self.assertEqual(0, code)
        dispatch.assert_called_once_with(config, "register", body)
        self.assertEqual({"worker": {"state": "ready"}}, json.loads(output.getvalue()))

    def test_render_worker_claims_at_most_one_job_and_exits(self) -> None:
        config = object()
        remote = object()
        output = io.StringIO()
        job_id = "22222222-2222-4222-8222-222222222222"
        with (
            mock.patch.object(cli, "load_config", return_value=config),
            mock.patch.object(cli.RemoteRecorder, "parse", return_value=remote) as parse,
            mock.patch.object(
                cli,
                "run_ephemeral_worker",
                return_value={"job": {"id": job_id, "state": "complete"}},
            ) as worker,
            mock.patch.object(cli.sys, "stdout", output),
        ):
            code = cli.run(
                [
                    "render-worker",
                    "--host",
                    "mcdatacol",
                    "--remote-root",
                    "/srv/mc-play-recorder",
                    "--job",
                    job_id,
                ]
            )

        self.assertEqual(0, code)
        parse.assert_called_once_with("mcdatacol", "/srv/mc-play-recorder")
        worker.assert_called_once_with(
            config,
            remote,
            job_id=job_id,
            cache_root=None,
            keep_workspace=False,
        )
        self.assertIn(f"RGB render job {job_id} is complete", output.getvalue())

    def test_finalize_attaches_then_completes_the_server_queue_job(self) -> None:
        job_id = "33333333-3333-4333-8333-333333333333"
        verifying = {"id": job_id, "state": "verifying"}
        attaching = {"id": job_id, "state": "attaching"}
        completed = {"id": job_id, "state": "complete"}
        finalized = {"job": verifying, "imports": [{"status": "complete"}]}
        queue = mock.Mock()
        queue.set_server_phase.return_value = attaching
        queue.complete.return_value = completed
        attachment = SimpleNamespace(
            partial=False,
            as_json=lambda: {"dataset_id": "a" * 32, "rgb_sample_count": 20},
        )
        config = SimpleNamespace(paths=SimpleNamespace(runtime=Path("/runtime")))

        with (
            mock.patch.object(cli, "RenderQueueStore", return_value=queue) as store,
            mock.patch.object(cli, "attach_imported_renders", return_value=attachment) as attach,
        ):
            result = cli._attach_finalized_render(config, finalized)

        store.assert_called_once_with(Path("/runtime/render-queue.sqlite3"))
        queue.set_server_phase.assert_called_once_with(
            job_id,
            "attaching",
            progress={"message": "Attaching verified RGB to the dataset"},
        )
        attach.assert_called_once_with(config, attaching, finalized["imports"])
        queue.complete.assert_called_once_with(
            job_id,
            {"dataset_id": "a" * 32, "rgb_sample_count": 20},
            partial=False,
        )
        self.assertEqual(completed, result["job"])

    def test_finalize_failure_marks_the_queue_job_failed(self) -> None:
        job_id = "44444444-4444-4444-8444-444444444444"
        job = {"id": job_id, "state": "attaching"}
        finalized = {"job": job, "imports": [{"status": "complete"}]}
        queue = mock.Mock()
        config = SimpleNamespace(paths=SimpleNamespace(runtime=Path("/runtime")))
        failure = RecorderError("dataset manifest was tampered")

        with (
            mock.patch.object(cli, "RenderQueueStore", return_value=queue),
            mock.patch.object(cli, "attach_imported_renders", side_effect=failure),
            self.assertRaisesRegex(RecorderError, "tampered"),
        ):
            cli._attach_finalized_render(config, finalized)

        queue.fail.assert_called_once_with(job_id, str(failure))
        queue.complete.assert_not_called()


if __name__ == "__main__":
    unittest.main()
