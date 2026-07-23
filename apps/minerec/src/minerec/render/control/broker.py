from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from minerec.errors import RecorderError
from minerec.render.control.queue import RenderQueueStore

RENDER_TASK_SCHEMA_VERSION = 1


def _pika_connection_factory(url: str) -> Any:  # noqa: ANN401 - pika connection type is optional at import time.
    try:
        import pika  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RecorderError("RabbitMQ support requires the 'pika' Python package") from exc
    return pika.BlockingConnection(pika.URLParameters(url))


def _pika_properties_factory(**kwargs: object) -> Any:  # noqa: ANN401 - pika properties type is optional at import time.
    try:
        import pika  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RecorderError("RabbitMQ support requires the 'pika' Python package") from exc
    return pika.BasicProperties(**kwargs)


def build_render_task_message(job: dict[str, Any]) -> dict[str, Any]:
    job_id = job.get("id")
    dataset_id = job.get("dataset_id")
    payload = job.get("payload")
    if not isinstance(job_id, str) or not job_id:
        raise RecorderError("render task job id must be a non-empty string")
    if not isinstance(dataset_id, str) or not dataset_id:
        raise RecorderError("render task dataset id must be a non-empty string")
    if not isinstance(payload, dict):
        raise RecorderError("render task payload must be a JSON object")
    message = {
        "schema_version": RENDER_TASK_SCHEMA_VERSION,
        "kind": "render_job",
        "execution": "per_process_worker",
        "job_id": job_id,
        "dataset_id": dataset_id,
        "payload": payload,
    }
    try:
        json.dumps(message, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError, RecursionError) as exc:
        raise RecorderError("render task message is not JSON serializable") from exc
    return message


def publish_render_task_message(
    url: str,
    queue_name: str,
    message: dict[str, Any],
    *,
    connection_factory: Callable[[str], Any] = _pika_connection_factory,
    properties_factory: Callable[..., Any] = _pika_properties_factory,
) -> None:
    if not isinstance(url, str) or not url:
        raise RecorderError("RabbitMQ URL must be a non-empty string")
    if not isinstance(queue_name, str) or not queue_name:
        raise RecorderError("RabbitMQ render task queue must be a non-empty string")
    body = json.dumps(
        message,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    connection = connection_factory(url)
    try:
        channel = connection.channel()
        channel.queue_declare(queue=queue_name, durable=True)
        channel.basic_publish(
            exchange="",
            routing_key=queue_name,
            body=body,
            properties=properties_factory(
                content_type="application/json",
                delivery_mode=2,
            ),
            mandatory=True,
        )
    finally:
        connection.close()


def _validate_render_task_message(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RecorderError("RabbitMQ render task must be a JSON object")
    if value.get("schema_version") != RENDER_TASK_SCHEMA_VERSION:
        raise RecorderError("RabbitMQ render task has an unsupported schema version")
    if value.get("kind") != "render_job":
        raise RecorderError("RabbitMQ render task has an unsupported kind")
    if value.get("execution") != "per_process_worker":
        raise RecorderError("RabbitMQ render task has an unsupported execution mode")
    return build_render_task_message(
        {
            "id": value.get("job_id"),
            "dataset_id": value.get("dataset_id"),
            "payload": value.get("payload"),
        }
    )


def consume_one_render_task_message(
    url: str,
    queue_name: str,
    *,
    handle: Callable[[dict[str, Any]], None],
    connection_factory: Callable[[str], Any] = _pika_connection_factory,
) -> bool:
    if not isinstance(url, str) or not url:
        raise RecorderError("RabbitMQ URL must be a non-empty string")
    if not isinstance(queue_name, str) or not queue_name:
        raise RecorderError("RabbitMQ render task queue must be a non-empty string")
    connection = connection_factory(url)
    try:
        channel = connection.channel()
        channel.queue_declare(queue=queue_name, durable=True)
        method, _properties, body = channel.basic_get(queue=queue_name, auto_ack=False)
        if method is None:
            return False
        try:
            message = _validate_render_task_message(json.loads(body.decode("utf-8")))
            handle(message)
        except Exception:
            channel.basic_nack(delivery_tag=method.delivery_tag, requeue=True)
            raise
        channel.basic_ack(delivery_tag=method.delivery_tag)
        return True
    finally:
        connection.close()


def dispatch_pending_render_jobs(
    store: RenderQueueStore,
    *,
    publish: Callable[[dict[str, Any]], None],
    limit: int = 50,
) -> int:
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
        raise RecorderError("render task dispatch limit must be between 1 and 100")
    published = 0
    for job in store.pending_publication(limit):
        publish(build_render_task_message(job))
        store.mark_published(job["id"])
        published += 1
    return published
