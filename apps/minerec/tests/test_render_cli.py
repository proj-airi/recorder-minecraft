from __future__ import annotations

import hashlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest import mock

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from minerec import cli
from minerec.errors import RecorderError
from minerec.processing.capture.storage import enforce_quota


class _Input:
    def __init__(self, raw: bytes) -> None:
        self.buffer = io.BytesIO(raw)


class RenderCliTest(unittest.TestCase):
    def test_scene_prepare_pins_epochs_against_retention(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            captures = root / "captures"
            episode = captures / "session-a"
            epoch = episode / "epochs" / "epoch-000000"
            epoch.mkdir(parents=True)
            events = b"{}\n"
            (epoch / "events.jsonl").write_bytes(events)
            (epoch / "manifest.json").write_text(
                json.dumps(
                    {
                        "sealed": True,
                        "record_count": 1,
                        "events_bytes": len(events),
                        "events_sha256": hashlib.sha256(events).hexdigest(),
                    }
                ),
                encoding="utf-8",
            )
            config = SimpleNamespace(
                paths=SimpleNamespace(
                    captures=captures,
                    runtime=root / "runtime",
                )
            )
            prepared = SimpleNamespace(manifest=root / "prepared" / "scene-job.json")
            reports = []

            def prepare(*_args: Any, **kwargs: Any) -> SimpleNamespace:
                self.assertEqual((epoch.resolve(),), kwargs["pinned_epoch_paths"])
                reports.append(
                    enforce_quota(
                        captures,
                        quota_bytes=1,
                        warn_percent=80,
                        evict_oldest=True,
                    )
                )
                self.assertTrue(epoch.is_dir())
                return prepared

            with (
                mock.patch.object(cli, "load_config", return_value=config),
                mock.patch.object(cli, "resolve_episode", return_value=episode),
                mock.patch.object(cli, "prepare_scene_job", side_effect=prepare),
            ):
                code = cli.run(
                    [
                        "scene",
                        "extract",
                        "session-a",
                        "--player",
                        "00000000-0000-4000-8000-000000000001",
                        "--connection",
                        "00000000-0000-4000-8000-000000000002",
                        "--output",
                        str(root / "scene.sqlite3"),
                        "--prepare-only",
                    ]
                )

            self.assertEqual(0, code)
            self.assertEqual("full", reports[0].status)
            self.assertEqual((), reports[0].evicted)
            self.assertTrue(epoch.is_dir())
            after = enforce_quota(
                captures,
                quota_bytes=1,
                warn_percent=80,
                evict_oldest=True,
            )
            self.assertEqual(1, len(after.evicted))
            self.assertFalse(epoch.exists())

    def test_render_no_gui_flag_is_forwarded_as_an_opt_out(self) -> None:
        config = SimpleNamespace(
            paths=SimpleNamespace(
                runtime=Path("/runtime"),
                captures=Path("/captures"),
                replays=Path("/replays"),
                exports=Path("/exports"),
            )
        )
        prepared = SimpleNamespace(manifest=Path("/render-job/render-job.json"))
        output = io.StringIO()
        with (
            mock.patch.object(cli, "load_config", return_value=config),
            mock.patch.object(cli, "operation_lock"),
            mock.patch.object(cli, "resolve_episode", return_value=Path("/episode")),
            mock.patch.object(cli, "resolve_replay", return_value=Path("/replay.zip")),
            mock.patch.object(cli, "prepare_render_job", return_value=prepared) as prepare,
            mock.patch.object(cli.sys, "stdout", output),
        ):
            code = cli.run(
                [
                    "render",
                    "session-a",
                    "--player",
                    "00000000-0000-4000-8000-000000000001",
                    "--output",
                    "/render-job",
                    "--no-gui",
                    "--prepare-only",
                ]
            )

        self.assertEqual(0, code)
        self.assertTrue(prepare.call_args.kwargs["no_gui"])

    def test_internal_rpc_accepts_only_a_bounded_json_object(self) -> None:
        with mock.patch.object(cli.sys, "stdin", _Input(b'{"worker_id":"one"}\n')):
            self.assertEqual({"worker_id": "one"}, cli._read_render_rpc_body())

        for raw in (b"[]\n", b'{"number":NaN}\n', b"{" + b" " * (64 * 1024)):
            with self.subTest(length=len(raw)), mock.patch.object(cli.sys, "stdin", _Input(raw)), self.assertRaises(RecorderError):
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

    def test_render_worker_consumes_one_rabbitmq_task_with_local_endpoint(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = mock.Mock()
            config.paths.base = Path(temporary)
            output = io.StringIO()
            with (
                mock.patch.object(cli, "load_config", return_value=config),
                mock.patch.object(cli, "consume_one_render_task_message", return_value=True) as consume,
                mock.patch.object(cli, "run_render_worker", return_value={"job": {"state": "complete"}}) as worker,
                mock.patch.object(cli, "LocalRecorder", wraps=cli.LocalRecorder) as local_recorder,
                mock.patch("sys.stdout", output),
            ):
                code = cli.run(
                    [
                        "render-worker",
                        "--rabbitmq-url",
                        "amqp://guest:guest@rabbitmq:5672/%2F",
                        "--queue",
                        "render.jobs",
                    ]
                )
                handle = consume.call_args.kwargs["handle"]
                handle({"job_id": "00000000-0000-4000-8000-000000000020"})

            self.assertEqual(0, code)
            consume.assert_called_once()
            local_recorder.assert_called_once_with(Path(temporary), config)
            worker.assert_called_once()
            self.assertEqual(
                "00000000-0000-4000-8000-000000000020",
                worker.call_args.kwargs["job_id"],
            )
            self.assertTrue(worker.call_args.kwargs["once"])
            self.assertIn("Consumed 1 render task", output.getvalue())

    def test_render_dispatcher_publishes_once_to_rabbitmq(self) -> None:
        config = mock.Mock()
        config.paths.runtime = Path("/runtime")
        queue = mock.Mock()
        output = io.StringIO()
        with (
            mock.patch.object(cli, "load_config", return_value=config),
            mock.patch.object(cli, "RenderQueueStore", return_value=queue) as store,
            mock.patch.object(
                cli,
                "dispatch_pending_render_jobs",
                return_value=2,
            ) as dispatch,
            mock.patch.object(cli, "publish_render_task_message") as publish,
            mock.patch("sys.stdout", output),
        ):
            code = cli.run(
                [
                    "render-dispatcher",
                    "--rabbitmq-url",
                    "amqp://guest:guest@rabbitmq:5672/%2F",
                    "--queue",
                    "render.jobs",
                    "--once",
                ]
            )

            self.assertEqual(0, code)
            store.assert_called_once_with(config.paths.runtime / "render-queue.sqlite3")
            dispatch.assert_called_once()
            self.assertIs(dispatch.call_args.args[0], queue)
            publish_callback = dispatch.call_args.kwargs["publish"]
            publish_callback({"job_id": "job"})
            publish.assert_called_once_with(
                "amqp://guest:guest@rabbitmq:5672/%2F",
                "render.jobs",
                {"job_id": "job"},
            )
            self.assertIn("Published 2 render task", output.getvalue())

    def test_render_preparer_runs_one_scan_and_exits(self) -> None:
        config = mock.Mock()
        preparer = mock.Mock()
        preparer.run_once.return_value = {"id": "job", "state": "queued"}
        output = io.StringIO()
        with (
            mock.patch.object(cli, "load_config", return_value=config),
            mock.patch.object(
                cli,
                "RenderPreparer",
                return_value=preparer,
            ) as constructor,
            mock.patch("sys.stdout", output),
        ):
            code = cli.run(["render-preparer", "--once"])

        self.assertEqual(0, code)
        constructor.assert_called_once_with(config)
        preparer.run_once.assert_called_once_with()
        self.assertIn("Prepared 1 render dataset", output.getvalue())

    def test_render_worker_requires_rabbitmq_url(self) -> None:
        error = io.StringIO()
        with (
            mock.patch.object(cli, "load_config", return_value=object()),
            mock.patch.object(cli, "run_render_worker") as worker,
            mock.patch.object(cli.sys, "stderr", error),
        ):
            code = cli.main(["render-worker"])

        self.assertEqual(2, code)
        worker.assert_not_called()
        self.assertIn("MC_RECORDER_RABBITMQ_URL", error.getvalue())

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
            result = cli._attach_finalized_render(config, finalized)  # ty:ignore[invalid-argument-type]

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
            cli._attach_finalized_render(config, finalized)  # ty:ignore[invalid-argument-type]

        queue.fail.assert_called_once_with(job_id, str(failure))
        queue.complete.assert_not_called()


if __name__ == "__main__":
    unittest.main()
