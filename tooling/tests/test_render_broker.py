from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from mc_recorder.protocol.render.broker import (
    build_render_task_message,
    consume_one_render_task_message,
    dispatch_mod_emitted_render_jobs,
    publish_render_task_message,
)
from mc_recorder.protocol.render.queue import RenderQueueStore


class RenderBrokerTest(unittest.TestCase):
    def _job_payload(self, recording_id: str = "0123456789abcdef01234567") -> dict[str, object]:
        return {
            "recording_id": recording_id,
            "session_id": "session-a",
            "player_uuid": "00000000-0000-4000-8000-000000000021",
        }

    def test_render_task_message_is_path_free_and_process_scoped(self) -> None:
        job = {
            "id": "00000000-0000-4000-8000-000000000020",
            "recording_id": "0123456789abcdef01234567",
            "payload": self._job_payload(),
            "state": "queued",
        }

        message = build_render_task_message(job)
        encoded = json.dumps(message, sort_keys=True)

        self.assertEqual(1, message["schema_version"])
        self.assertEqual("render_job", message["kind"])
        self.assertEqual("per_process_worker", message["execution"])
        self.assertEqual(job["id"], message["job_id"])
        self.assertEqual(job["payload"], message["payload"])
        self.assertNotIn("/srv/", encoded)

    def test_dispatcher_uses_mod_render_ready_spool_and_deletes_after_publish(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = RenderQueueStore(root / "render-queue.sqlite3")
            payload = self._job_payload()
            payload["connection_id"] = "00000000-0000-4000-8000-000000000021"
            job = store.create(payload)
            ready = root / "control" / "render-ready" / f"{payload['connection_id']}.json"
            ready.parent.mkdir(parents=True)
            ready.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "kind": "render_ready",
                        "session_id": payload["session_id"],
                        "player_uuid": payload["player_uuid"],
                        "connection_id": payload["connection_id"],
                        "segments": [],
                    }
                ),
                encoding="utf-8",
            )
            published: list[dict[str, object]] = []

            count = dispatch_mod_emitted_render_jobs(
                store,
                root / "control",
                publish=published.append,
                limit=10,
            )

            self.assertEqual(1, count)
            self.assertEqual(job["id"], published[0]["job_id"])
            self.assertFalse(ready.exists())

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
            "execution": "per_process_worker",
            "job_id": "00000000-0000-4000-8000-000000000020",
            "recording_id": "0123456789abcdef01234567",
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

    def test_rabbitmq_consumer_acks_successful_task(self) -> None:
        calls: list[tuple[str, dict[str, object]]] = []
        message = {
            "schema_version": 1,
            "kind": "render_job",
            "execution": "per_process_worker",
            "job_id": "00000000-0000-4000-8000-000000000020",
            "recording_id": "0123456789abcdef01234567",
            "payload": self._job_payload(),
        }

        class Method:
            delivery_tag = "delivery-1"

        class Channel:
            def queue_declare(self, **kwargs: object) -> None:
                calls.append(("queue_declare", kwargs))

            def basic_get(self, **kwargs: object) -> tuple[Method, object, bytes]:
                calls.append(("basic_get", kwargs))
                return Method(), object(), json.dumps(message).encode("utf-8")

            def basic_ack(self, **kwargs: object) -> None:
                calls.append(("basic_ack", kwargs))

            def basic_nack(self, **kwargs: object) -> None:
                calls.append(("basic_nack", kwargs))

        class Connection:
            def channel(self) -> Channel:
                return Channel()

            def close(self) -> None:
                calls.append(("close", {}))

        handled: list[dict[str, object]] = []

        result = consume_one_render_task_message(
            "amqp://guest:guest@rabbitmq:5672/%2F",
            "render.jobs",
            handle=handled.append,
            connection_factory=lambda _url: Connection(),
        )

        self.assertTrue(result)
        self.assertEqual([message], handled)
        self.assertIn(("basic_ack", {"delivery_tag": "delivery-1"}), calls)
        self.assertNotIn("basic_nack", [name for name, _kwargs in calls])
