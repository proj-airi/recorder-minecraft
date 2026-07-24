from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest import mock

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from minerec import cli
from minerec.processing.capture.storage import enforce_quota


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

    def test_render_worker_runs_a_persistent_rabbitmq_consumer(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = mock.Mock()
            config.paths.base = Path(temporary)
            consumer = mock.Mock()
            recorder = mock.Mock()

            def consume(*_args: object, **kwargs: object) -> None:
                handle = cast(Callable[[dict[str, object]], None], kwargs["handle"])
                handle({"job_id": "00000000-0000-4000-8000-000000000020"})

            with (
                mock.patch.object(cli, "load_config", return_value=config),
                mock.patch.object(cli, "consume_render_task_messages", side_effect=consume) as consume_messages,
                mock.patch.object(cli, "RenderConsumer", return_value=consumer) as consumer_type,
                mock.patch.object(cli, "dashboard_recorder", return_value=recorder) as recorder_factory,
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

            self.assertEqual(0, code)
            consume_messages.assert_called_once()
            self.assertIn("requeue_on_error", consume_messages.call_args.kwargs)
            recorder_factory.assert_called_once_with(config)
            consumer_type.assert_called_once_with(
                config,
                recorder,
                cache_root=None,
                keep_workspace=False,
            )
            self.assertEqual(
                "00000000-0000-4000-8000-000000000020",
                consumer.process.call_args.args[0],
            )
            consumer.close.assert_called_once_with()

    def test_render_dispatcher_publishes_once_to_rabbitmq(self) -> None:
        config = mock.Mock()
        config.paths.runtime = Path("/runtime")
        queue = mock.Mock()
        with (
            mock.patch.object(cli, "load_config", return_value=config),
            mock.patch.object(cli, "RenderQueueStore", return_value=queue) as store,
            mock.patch.object(
                cli,
                "dispatch_pending_render_jobs",
                return_value=2,
            ) as dispatch,
            mock.patch.object(cli, "publish_render_task_message") as publish,
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

    def test_render_preparer_runs_one_scan_and_exits(self) -> None:
        config = mock.Mock()
        preparer = mock.Mock()
        preparer.run_once.return_value = {"id": "job", "state": "queued"}
        with (
            mock.patch.object(cli, "load_config", return_value=config),
            mock.patch.object(
                cli,
                "RenderPreparer",
                return_value=preparer,
            ) as constructor,
        ):
            code = cli.run(["render-preparer", "--once"])

        self.assertEqual(0, code)
        constructor.assert_called_once_with(config)
        preparer.run_once.assert_called_once_with()

    def test_render_worker_requires_rabbitmq_url(self) -> None:
        with (
            mock.patch.object(cli, "load_config", return_value=object()),
            mock.patch.object(cli, "RenderConsumer") as consumer,
        ):
            code = cli.main(["render-worker"])

        self.assertEqual(2, code)
        consumer.assert_not_called()


if __name__ == "__main__":
    unittest.main()
