from __future__ import annotations

import json
import queue
import sys
import tempfile
import unittest
from collections.abc import Callable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from minerec.render.control.broker import (
    build_render_task_message,
    consume_render_task_messages,
    dispatch_pending_render_jobs,
    publish_render_task_message,
)
from minerec.render.control.queue import RenderQueueStore


class RenderBrokerTest(unittest.TestCase):
    def _job_payload(self, dataset_id: str = "c" * 32) -> dict[str, object]:
        return {
            "dataset_id": dataset_id,
            "session_id": "session-a",
            "player_uuid": "00000000-0000-4000-8000-000000000021",
        }

    def test_render_task_message_is_path_free_and_process_agnostic(self) -> None:
        job = {
            "id": "00000000-0000-4000-8000-000000000020",
            "dataset_id": "c" * 32,
            "payload": self._job_payload(),
            "state": "queued",
        }

        message = build_render_task_message(job)
        encoded = json.dumps(message, sort_keys=True)

        self.assertEqual(1, message["schema_version"])
        self.assertEqual("render_job", message["kind"])
        self.assertNotIn("execution", message)
        self.assertEqual(job["id"], message["job_id"])
        self.assertEqual(job["payload"], message["payload"])
        self.assertNotIn("/srv/", encoded)

    def test_dispatcher_publishes_pending_queue_jobs_once(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = RenderQueueStore(root / "render-queue.sqlite3")
            payload = self._job_payload()
            job = store.create(payload)
            published: list[dict[str, object]] = []

            count = dispatch_pending_render_jobs(
                store,
                publish=published.append,
                limit=10,
            )

            self.assertEqual(1, count)
            self.assertEqual(job["id"], published[0]["job_id"])
            self.assertIsNotNone(store.get(job["id"])["published_at"])
            self.assertEqual(
                0,
                dispatch_pending_render_jobs(
                    store,
                    publish=published.append,
                    limit=10,
                ),
            )

    def test_publish_failure_leaves_job_pending(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = RenderQueueStore(Path(temporary) / "render-queue.sqlite3")
            job = store.create(self._job_payload())

            def fail(_message: dict[str, object]) -> None:
                raise RuntimeError("broker unavailable")

            with self.assertRaisesRegex(RuntimeError, "broker unavailable"):
                dispatch_pending_render_jobs(store, publish=fail)

            self.assertIsNone(store.get(job["id"])["published_at"])

    def test_rabbitmq_publisher_declares_durable_queue_and_persistent_json(self) -> None:
        calls: list[tuple[str, dict[str, object]]] = []

        class Properties:
            def __init__(self, **kwargs: object) -> None:
                self.kwargs = kwargs

        class Channel:
            def queue_declare(self, **kwargs: object) -> None:
                calls.append(("queue_declare", kwargs))

            def basic_publish(self, **kwargs: object) -> None:
                calls.append(("basic_publish", kwargs))

        class Connection:
            def __init__(self) -> None:
                self.channel_instance = Channel()
                self.closed = False

            def channel(self) -> Channel:
                return self.channel_instance

            def close(self) -> None:
                self.closed = True

        connection = Connection()
        message = {
            "schema_version": 1,
            "kind": "render_job",
            "job_id": "00000000-0000-4000-8000-000000000020",
            "dataset_id": "c" * 32,
            "payload": self._job_payload(),
        }

        publish_render_task_message(
            "amqp://guest:guest@rabbitmq:5672/%2F",
            "render.jobs",
            message,
            connection_factory=lambda _url: connection,
            properties_factory=Properties,
        )

        self.assertTrue(connection.closed)
        self.assertEqual(
            ("queue_declare", {"queue": "render.jobs", "durable": True}),
            calls[0],
        )
        publish = calls[1][1]
        self.assertEqual("", publish["exchange"])
        self.assertEqual("render.jobs", publish["routing_key"])
        self.assertTrue(publish["mandatory"])
        self.assertEqual(message, json.loads(publish["body"].decode("utf-8")))  # ty:ignore[unresolved-attribute]
        self.assertEqual(
            {"content_type": "application/json", "delivery_mode": 2},
            publish["properties"].kwargs,  # ty:ignore[unresolved-attribute]
        )

    def test_rabbitmq_consumer_processes_multiple_tasks_with_one_connection(self) -> None:
        calls: list[tuple[str, dict[str, object]]] = []
        messages = [
            {
                "schema_version": 1,
                "kind": "render_job",
                "job_id": job_id,
                "dataset_id": "c" * 32,
                "payload": self._job_payload(),
            }
            for job_id in (
                "00000000-0000-4000-8000-000000000020",
                "00000000-0000-4000-8000-000000000021",
            )
        ]
        messages[0]["execution"] = "per_process_worker"

        class Method:
            def __init__(self, delivery_tag: str) -> None:
                self.delivery_tag = delivery_tag

        class Channel:
            callback: object = None

            def __init__(self, connection: Connection) -> None:
                self.connection = connection

            def queue_declare(self, **kwargs: object) -> None:
                calls.append(("queue_declare", kwargs))

            def basic_qos(self, **kwargs: object) -> None:
                calls.append(("basic_qos", kwargs))

            def basic_consume(self, **kwargs: object) -> None:
                calls.append(("basic_consume", kwargs))
                self.callback = kwargs["on_message_callback"]

            def start_consuming(self) -> None:
                assert callable(self.callback)
                for index, message in enumerate(messages, 1):
                    self.callback(  # ty:ignore[call-top-callable]
                        self,
                        Method(f"delivery-{index}"),
                        object(),
                        json.dumps(message).encode(),
                    )
                    self.connection.callbacks.get(timeout=2)()

            def stop_consuming(self) -> None:
                calls.append(("stop_consuming", {}))

            def basic_ack(self, **kwargs: object) -> None:
                calls.append(("basic_ack", kwargs))

            def basic_nack(self, **kwargs: object) -> None:
                calls.append(("basic_nack", kwargs))

        class Connection:
            def __init__(self) -> None:
                self.callbacks: queue.Queue[Callable[[], None]] = queue.Queue()
                self.is_open = True

            def channel(self) -> Channel:
                return Channel(self)

            def add_callback_threadsafe(self, callback: Callable[[], None]) -> None:
                self.callbacks.put(callback)

            def close(self) -> None:
                self.is_open = False
                calls.append(("close", {}))

        handled: list[dict[str, object]] = []

        consume_render_task_messages(
            "amqp://guest:guest@rabbitmq:5672/%2F",
            "render.jobs",
            handle=handled.append,
            connection_factory=lambda _url: Connection(),
        )

        self.assertEqual(
            [{key: value for key, value in message.items() if key != "execution"} for message in messages],
            handled,
        )
        self.assertIn(("basic_qos", {"prefetch_count": 1}), calls)
        self.assertIn(("basic_ack", {"delivery_tag": "delivery-1"}), calls)
        self.assertIn(("basic_ack", {"delivery_tag": "delivery-2"}), calls)
        self.assertNotIn("basic_nack", [name for name, _kwargs in calls])
