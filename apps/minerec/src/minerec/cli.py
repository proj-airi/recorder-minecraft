from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Sequence

from .config import (
    DEFAULT_CONFIG_NAME,
    DEFAULT_RENDER_TASK_QUEUE,
    ENV_RABBITMQ_URL,
    initialize,
    load_config,
    load_runtime_env,
    render_queue_database,
)
from .errors import RecorderError
from .operations import operation_lock
from .processing.capture.episodes import (
    EpisodeInfo,
    directory_size,
    list_episodes,
    resolve_episode,
    validate_episode,
)
from .processing.capture.exporter import export_episode
from .processing.capture.storage import StorageReport, enforce_quota, human_bytes, pin_sealed_epochs
from .processing.scene.job import (
    cleanup_scene_job,
    cleanup_stale_scene_jobs,
    launch_scene_job,
    prepare_scene_job,
)
from .processing.scene.store import compact_scene_stream, validate_scene_store
from .render.control.broker import (
    consume_render_task_messages,
    dispatch_pending_render_jobs,
    publish_render_task_message,
)
from .render.control.preparer import RenderPreparer
from .render.control.queue import RenderQueueStore
from .serve.dashboard.server import serve_dashboard
from .workers.render import RenderConsumer, dashboard_recorder, requeue_render_task_error


def _parser() -> argparse.ArgumentParser:
    runtime_env = load_runtime_env()
    parser = argparse.ArgumentParser(prog="minerec", description="Minecraft gameplay dataset recorder")
    parser.add_argument("--config", "-c", default=DEFAULT_CONFIG_NAME, help="recorder TOML path (default: recorder.toml)")
    commands = parser.add_subparsers(dest="command", required=True)

    init = commands.add_parser("init", help="create recorder.toml and workspace directories")
    init.add_argument("--accept-eula", action="store_true", help="record explicit acceptance of the Minecraft EULA")
    init.add_argument("--force", action="store_true", help="replace an existing recorder.toml")

    episodes = commands.add_parser("episodes", help="inspect immutable source episodes")
    episode_commands = episodes.add_subparsers(dest="episodes_command", required=True)
    episode_list = episode_commands.add_parser("list", help="list capture sessions")
    episode_list.add_argument("--json", action="store_true", dest="as_json")
    validate = episode_commands.add_parser("validate", help="validate sealed epoch envelopes")
    validate.add_argument("episode", nargs="?", help="session id; validates all sessions when omitted")
    validate.add_argument("--json", action="store_true", dest="as_json")

    export = commands.add_parser("export", help="export state/action JSONL from sealed epochs")
    export.add_argument("episode", help="session id")
    export.add_argument("--output", "-o", type=Path)
    export.add_argument("--player", action="append", default=[], help="player UUID filter; repeatable")
    export.add_argument(
        "--connection",
        action="append",
        default=[],
        help="player connection UUID filter; repeatable",
    )
    export.add_argument("--from-tick", type=int)
    export.add_argument("--to-tick", type=int)
    export.add_argument(
        "--frames",
        action="append",
        type=Path,
        default=[],
        help="completed render job, result.json, or frames.jsonl to attach; repeatable",
    )
    export.add_argument(
        "--scene",
        action="append",
        type=Path,
        default=[],
        help="verified scene-v1 SQLite store to attach; at most one",
    )
    export.add_argument("--force", action="store_true", help="replace an existing output directory")

    scene = commands.add_parser("scene", help="extract random-access world scenes without a GUI client")
    scene_commands = scene.add_subparsers(dest="scene_command", required=True)
    scene_extract = scene_commands.add_parser("extract", help="replay one connection headlessly and compact a scene store")
    scene_extract.add_argument("episode", help="session id")
    scene_extract.add_argument("--player", required=True, help="recorded player UUID")
    scene_extract.add_argument("--connection", required=True, help="recorded connection UUID")
    scene_extract.add_argument("--from-tick", type=int)
    scene_extract.add_argument("--to-tick", type=int)
    scene_extract.add_argument("--output", "-o", type=Path, required=True)
    scene_extract.add_argument(
        "--force",
        action="store_true",
        help="atomically replace an existing valid scene store",
    )
    scene_extract.add_argument("--prepare-only", action="store_true", help="write scene-job.json without launching the server")

    storage = commands.add_parser("storage", help="inspect or enforce capture retention")
    storage_commands = storage.add_subparsers(dest="storage_command", required=True)
    storage_commands.add_parser("status", help="show capture plus replay quota without deleting data")
    storage_commands.add_parser("enforce", help="evict oldest verified immutable source units when over quota")

    dashboard = commands.add_parser("dashboard", help="serve the authenticated LAN dashboard")
    dashboard_commands = dashboard.add_subparsers(dest="dashboard_command", required=True)
    dashboard_commands.add_parser("serve", help="run the host dashboard until interrupted")

    worker = commands.add_parser(
        "render-worker",
        help="consume RabbitMQ render tasks on this GUI host until interrupted",
    )
    worker.add_argument(
        "--rabbitmq-url",
        default=runtime_env.render_queue.rabbitmq_url,
        help=f"RabbitMQ AMQP URL; defaults to {ENV_RABBITMQ_URL}",
    )
    worker.add_argument(
        "--queue",
        default=runtime_env.render_queue.task_queue,
        help=f"RabbitMQ render task queue (default: {DEFAULT_RENDER_TASK_QUEUE})",
    )
    worker.add_argument("--cache", type=Path, help="local replay cache and temporary workspace")
    worker.add_argument("--keep-workspace", action="store_true")

    dispatcher = commands.add_parser(
        "render-dispatcher",
        help="publish queued RGB render jobs to RabbitMQ for GUI consumers",
    )
    dispatcher.add_argument(
        "--rabbitmq-url",
        default=runtime_env.render_queue.rabbitmq_url,
        help=f"RabbitMQ AMQP URL; defaults to {ENV_RABBITMQ_URL}",
    )
    dispatcher.add_argument(
        "--queue",
        default=runtime_env.render_queue.task_queue,
        help=f"RabbitMQ render task queue (default: {DEFAULT_RENDER_TASK_QUEUE})",
    )
    dispatcher.add_argument(
        "--once",
        action="store_true",
        help="publish one scan and exit instead of running periodically",
    )
    dispatcher.add_argument(
        "--interval",
        type=float,
        default=10.0,
        help="seconds between dispatch scans in continuous mode (1-300; default: 10)",
    )
    dispatcher.add_argument(
        "--limit",
        type=int,
        default=50,
        help="maximum queued jobs to publish per scan (1-100; default: 50)",
    )
    preparer = commands.add_parser(
        "render-preparer",
        help="build verified datasets for artifact render requests",
    )
    preparer.add_argument(
        "--once",
        action="store_true",
        help="process at most one request and exit",
    )
    preparer.add_argument(
        "--interval",
        type=float,
        default=5.0,
        help="seconds between preparation scans (1-300; default: 5)",
    )
    return parser


def _prepare_scene_output(path: Path, *, force: bool) -> Path:
    requested = Path(os.path.abspath(path.expanduser()))
    if requested.is_symlink():
        raise RecorderError(f"scene store output may not be a symlink: {requested}")
    parent = requested.parent
    missing: list[str] = []
    ancestor = parent
    while not ancestor.exists() and not ancestor.is_symlink():
        missing.append(ancestor.name)
        ancestor = ancestor.parent
    if ancestor.is_symlink() or not ancestor.is_dir():
        raise RecorderError(f"scene store parent is not a safe directory: {ancestor}")
    resolved_parent = ancestor.resolve()
    for name in reversed(missing):
        resolved_parent /= name
    resolved_parent.mkdir(parents=True, exist_ok=True)
    if resolved_parent.is_symlink() or not resolved_parent.is_dir():
        raise RecorderError(f"scene store parent is not a safe directory: {resolved_parent}")
    output = resolved_parent / requested.name
    if output.exists() or output.is_symlink():
        if not force:
            raise RecorderError(f"scene store output exists: {output}; pass --force to replace it")
        validate_scene_store(output)
    return output


def _episode_json(info: EpisodeInfo) -> dict[str, object]:
    value = asdict(info)
    value["path"] = str(info.path)
    return value


def _print_storage(report: object) -> None:
    status = getattr(report, "status")
    message = getattr(report, "message")
    after = getattr(report, "after_bytes")
    quota = getattr(report, "quota_bytes")
    stream = sys.stderr if status in {"warning", "full"} else sys.stdout
    print(f"storage {status}: {message} ({human_bytes(after)} / {human_bytes(quota)})", file=stream)
    for item in getattr(report, "evicted"):
        kind = getattr(item, "source_kind", "capture_epoch")
        source = getattr(item, "source_path", "") or f"{item.session_id}/{item.epoch}"
        print(
            f"evicted {kind} {source} ({human_bytes(item.size_bytes)})",
            file=sys.stderr,
        )


def _list(config_path: str, as_json: bool) -> int:
    config = load_config(config_path)
    episodes = list_episodes(config.paths.captures)
    if as_json:
        print(json.dumps([_episode_json(info) for info in episodes], indent=2, sort_keys=True))
        return 0
    if not episodes:
        print("No episodes found.")
        return 0
    print("SESSION\tSTATUS\tEPOCHS\tTICKS\tSIZE")
    for info in episodes:
        tick_range = "-" if info.first_tick is None else f"{info.first_tick}..{info.last_tick}"
        epochs = f"{info.sealed_epoch_count} sealed"
        if info.active_epoch_count:
            label = "active" if info.status == "active" else "unsealed"
            epochs += f", {info.active_epoch_count} {label}"
        print(f"{info.session_id}\t{info.status}\t{epochs}\t{tick_range}\t{human_bytes(info.size_bytes)}")
    used = directory_size(config.paths.captures) + directory_size(config.paths.replays)
    if used >= config.storage.quota_bytes * config.storage.warn_percent // 100:
        print(
            f"WARNING: capture and replay storage uses {human_bytes(used)} of {human_bytes(config.storage.quota_bytes)}",
            file=sys.stderr,
        )
    return 0


def _validate(config_path: str, episode_id: str | None, as_json: bool) -> int:
    config = load_config(config_path)
    if episode_id:
        paths = [resolve_episode(config.paths.captures, episode_id)]
    else:
        paths = [info.path for info in list_episodes(config.paths.captures)]
    results = [validate_episode(path) for path in paths]
    if as_json:
        print(json.dumps([result.as_json() for result in results], indent=2, sort_keys=True))
    else:
        if not results:
            print("No episodes found.")
        for result in results:
            status = "valid" if result.valid else "INVALID"
            print(f"{result.session_id}: {status}; {result.sealed_epochs} sealed epoch(s), {result.active_epochs} active, {result.event_count} event(s)")
            for issue in result.issues:
                print(f"  {issue.severity.upper()}: {issue.path}: {issue.message}")
    return 0 if all(result.valid for result in results) else 1


def _storage(config_path: str, enforce: bool) -> int:
    config = load_config(config_path)

    def inspect_or_enforce() -> StorageReport:
        return enforce_quota(
            config.paths.captures,
            quota_bytes=config.storage.quota_bytes,
            warn_percent=config.storage.warn_percent,
            evict_oldest=config.storage.evict_oldest if enforce else False,
            replays_root=config.paths.replays,
        )

    if enforce:
        with operation_lock(config.paths.runtime, "storage_enforce"):
            report = inspect_or_enforce()
    else:
        report = inspect_or_enforce()
    if not enforce and report.status == "full" and config.storage.evict_oldest:
        print(
            "storage full: run 'minerec storage enforce' or start the server to evict sealed epochs",
            file=sys.stderr,
        )
    _print_storage(report)
    return 1 if report.status == "full" else 0


def run(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "init":
        path = initialize(args.config, accept_eula=args.accept_eula, force=args.force)
        print(f"Initialized {path}")
        if not args.accept_eula:
            print("Review the Minecraft EULA, then set MC_EULA=TRUE in deploy/.env before starting.")
        print("Next: copy deploy/.env.example to deploy/.env, edit it, then run hack/minecraft-server start")
        return 0

    if args.command == "episodes":
        if args.episodes_command == "list":
            return _list(args.config, args.as_json)
        return _validate(args.config, args.episode, args.as_json)

    if args.command == "export":
        config = load_config(args.config)
        episode = resolve_episode(config.paths.captures, args.episode)
        output = args.output or (config.paths.exports / f"{args.episode}.dataset")
        with operation_lock(config.paths.runtime, "export"):
            result = export_episode(
                episode,
                output,
                players=args.player,
                connections=args.connection,
                first_tick=args.from_tick,
                last_tick=args.to_tick,
                frames=args.frames,
                scenes=args.scene,
                force=args.force,
            )
        print(f"Exported {result.sample_count} samples, {result.state_count} states, and {result.action_count} actions to {result.output}")
        if result.action_count == 0:
            print("WARNING: no applied serverbound action records matched the selection", file=sys.stderr)
        print(f"Attached RGB for {result.rgb_count} state(s) and scene frames for {result.scene_count} state(s); modalities.jsonl marks all missing references explicitly.")
        return 0

    if args.command == "scene":
        config = load_config(args.config)
        episode = resolve_episode(config.paths.captures, args.episode)
        output = _prepare_scene_output(args.output, force=args.force)
        with operation_lock(config.paths.runtime, "scene_extract"):
            cleanup_stale_scene_jobs(config.paths.runtime, keep=1)
            with pin_sealed_epochs(episode) as pinned_epochs:
                job = prepare_scene_job(
                    config,
                    episode,
                    player_uuid=args.player,
                    connection_id=args.connection,
                    first_tick=args.from_tick,
                    last_tick=args.to_tick,
                    pinned_epoch_paths=pinned_epochs,
                )
            print(f"Prepared scene extraction job {job.manifest}")
            if args.prepare_only:
                cleanup_stale_scene_jobs(config.paths.runtime, keep=1)
                return 0
            try:
                verified_stream = launch_scene_job(config, job)
                info = compact_scene_stream(
                    job.stream,
                    output,
                    expected_session_id=job.session_id,
                    expected_player_uuid=job.player_uuid,
                    expected_connection_id=job.connection_id,
                    expected_ticks=job.state_ticks,
                    force=args.force,
                    verified_stream=verified_stream,
                )
            except BaseException:
                cleanup_stale_scene_jobs(config.paths.runtime, keep=1)
                raise
            else:
                cleanup_scene_job(job)
        print(f"Extracted {info.frame_count} random-access scene frames to {output}")
        return 0

    if args.command == "storage":
        return _storage(args.config, args.storage_command == "enforce")

    if args.command == "dashboard":
        config = load_config(args.config)
        serve_dashboard(config)
        return 0

    if args.command == "render-worker":
        config = load_config(args.config)
        if not args.rabbitmq_url:
            raise RecorderError(f"--rabbitmq-url or {ENV_RABBITMQ_URL} is required")
        remote = dashboard_recorder(config)
        consumer = RenderConsumer(
            config,
            remote,
            cache_root=args.cache,
            keep_workspace=args.keep_workspace,
        )

        def handle(message: dict[str, object]) -> None:
            job_id = message.get("job_id")
            if not isinstance(job_id, str):
                raise RecorderError("RabbitMQ render task lacks a job id")
            consumer.process(job_id)

        def report_error(error: Exception) -> None:
            print(f"Render task failed: {error}", file=sys.stderr, flush=True)

        try:
            consume_render_task_messages(
                args.rabbitmq_url,
                args.queue,
                handle=handle,
                requeue_on_error=requeue_render_task_error,
                on_error=report_error,
            )
        finally:
            consumer.close()
        return 0

    if args.command == "render-dispatcher":
        config = load_config(args.config)
        if not args.rabbitmq_url:
            raise RecorderError(f"--rabbitmq-url or {ENV_RABBITMQ_URL} is required")
        if not 1.0 <= args.interval <= 300.0:
            raise RecorderError("--interval must be between 1 and 300 seconds")
        queue = RenderQueueStore(render_queue_database(config))

        def publish(message: dict[str, object]) -> None:
            publish_render_task_message(args.rabbitmq_url, args.queue, message)

        while True:
            count = dispatch_pending_render_jobs(
                queue,
                publish=publish,
                limit=args.limit,
            )
            print(f"Published {count} render task(s).", flush=True)
            if args.once:
                return 0
            time.sleep(args.interval)

    if args.command == "render-preparer":
        config = load_config(args.config)
        if not 1.0 <= args.interval <= 300.0:
            raise RecorderError("--interval must be between 1 and 300 seconds")
        preparer = RenderPreparer(config)
        while True:
            result = preparer.run_once()
            prepared = int(result is not None and result.get("state") == "queued")
            print(f"Prepared {prepared} render dataset(s).", flush=True)
            if args.once:
                return 0
            time.sleep(args.interval)

    raise RecorderError(f"unknown command: {args.command}")


def main(argv: Sequence[str] | None = None) -> int:
    try:
        return run(argv)
    except RecorderError as exc:
        print(f"minerec: error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("minerec: interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
