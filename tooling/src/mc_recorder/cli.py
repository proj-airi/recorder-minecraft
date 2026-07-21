from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Sequence

from .config import DEFAULT_CONFIG_NAME, initialize, load_config
from .dashboard_http import serve_dashboard
from .episodes import (
    EpisodeInfo,
    directory_size,
    list_episodes,
    resolve_episode,
    validate_episode,
)
from .errors import RecorderError
from .exporter import export_episode
from .operations import operation_lock
from .render_attach import attach_imported_renders
from .render_job import launch_render_job, prepare_render_job, resolve_replay
from .render_queue import RenderQueueStore
from .render_rpc import dispatch_render_rpc
from .render_worker import RemoteRecorder, run_render_worker
from .scene_job import (
    cleanup_scene_job,
    cleanup_stale_scene_jobs,
    launch_scene_job,
    prepare_scene_job,
)
from .scene_store import compact_scene_stream, validate_scene_store
from .server import show_logs, show_status, start_server, stop_server
from .storage import StorageReport, enforce_quota, human_bytes


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mc-recorder", description="Minecraft gameplay dataset recorder")
    parser.add_argument(
        "--config", "-c", default=DEFAULT_CONFIG_NAME, help="recorder TOML path (default: recorder.toml)"
    )
    commands = parser.add_subparsers(dest="command", required=True)

    init = commands.add_parser("init", help="create recorder.toml and workspace directories")
    init.add_argument("--accept-eula", action="store_true", help="record explicit acceptance of the Minecraft EULA")
    init.add_argument("--force", action="store_true", help="replace an existing recorder.toml")

    server = commands.add_parser("server", help="manage the Docker Minecraft server")
    server_commands = server.add_subparsers(dest="server_command", required=True)
    start = server_commands.add_parser("start", help="build the local mod and start capture services")
    start.add_argument("--wait", action="store_true", help="wait up to 180 seconds for Compose services")
    stop = server_commands.add_parser("stop", help="gracefully stop and seal recordings")
    stop.add_argument("--timeout", type=int, default=120, help="graceful stop timeout in seconds")
    server_commands.add_parser("status", help="show Compose service status")
    logs = server_commands.add_parser("logs", help="show service logs")
    logs.add_argument("--follow", "-f", action="store_true")
    logs.add_argument("--tail", type=int, default=100)
    logs.add_argument("--service", choices=("minecraft", "storage-monitor", "all"), default="minecraft")

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

    render = commands.add_parser("render", help="prepare a deterministic first-person client render job")
    render.add_argument("episode", help="session id")
    render.add_argument("--player", required=True, help="recorded player UUID")
    render.add_argument("--connection", help="player connection ID; required when the player reconnected")
    render.add_argument("--replay", type=Path, help="completed ServerReplay ZIP/MCPR; inferred only when unique")
    render.add_argument("--output", "-o", type=Path)
    render.add_argument("--width", type=int, default=640)
    render.add_argument("--height", type=int, default=360)
    render.add_argument("--fps", type=int, default=20)
    render.add_argument("--from-tick", type=int)
    render.add_argument("--to-tick", type=int)
    render.add_argument(
        "--no-gui",
        action="store_true",
        help="render without the client HUD; a graphical desktop is still required",
    )
    render.add_argument("--force", action="store_true")
    render.add_argument(
        "--prepare-only", action="store_true", help="write render-job.json without launching the local client"
    )

    scene = commands.add_parser(
        "scene", help="extract random-access world scenes without a GUI client"
    )
    scene_commands = scene.add_subparsers(dest="scene_command", required=True)
    scene_extract = scene_commands.add_parser(
        "extract", help="replay one connection headlessly and compact a scene store"
    )
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
    scene_extract.add_argument(
        "--prepare-only", action="store_true", help="write scene-job.json without launching the server"
    )

    storage = commands.add_parser("storage", help="inspect or enforce capture retention")
    storage_commands = storage.add_subparsers(dest="storage_command", required=True)
    storage_commands.add_parser("status", help="show capture plus replay quota without deleting data")
    storage_commands.add_parser("enforce", help="evict oldest verified immutable source units when over quota")

    dashboard = commands.add_parser("dashboard", help="serve the authenticated LAN dashboard")
    dashboard_commands = dashboard.add_subparsers(dest="dashboard_command", required=True)
    dashboard_commands.add_parser("serve", help="run the host dashboard until interrupted")

    worker = commands.add_parser(
        "render-worker",
        help="poll and process RGB jobs with the local GUI renderer",
    )
    worker.add_argument("--host", required=True, help="SSH host or alias of the recorder server")
    worker.add_argument(
        "--remote-root",
        default="/srv/mc-play-recorder",
        help="absolute mc-recorder workspace on the SSH host",
    )
    worker.add_argument(
        "--job",
        help="claim one exact queued render job UUID and exit afterward",
    )
    worker.add_argument("--cache", type=Path, help="local replay cache and temporary workspace")
    worker.add_argument(
        "--once",
        action="store_true",
        help="make one claim attempt and exit instead of polling continuously",
    )
    worker.add_argument(
        "--poll-interval",
        type=float,
        default=10.0,
        help="seconds between empty queue checks in continuous mode (1-30; default: 10)",
    )
    worker.add_argument(
        "--keep-workspace",
        action="store_true",
        help="retain the local per-job render workspace after completion or failure",
    )

    rpc = commands.add_parser("render-rpc", help=argparse.SUPPRESS)
    rpc.add_argument(
        "render_rpc_action",
        choices=(
            "register",
            "worker-heartbeat",
            "claim",
            "request",
            "heartbeat",
            "finalize",
            "fail",
        ),
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
            raise RecorderError(
                f"scene store output exists: {output}; pass --force to replace it"
            )
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
            f"WARNING: capture and replay storage uses {human_bytes(used)} of "
            f"{human_bytes(config.storage.quota_bytes)}",
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
            print(
                f"{result.session_id}: {status}; {result.sealed_epochs} sealed epoch(s), "
                f"{result.active_epochs} active, {result.event_count} event(s)"
            )
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
            "storage full: run 'mc-recorder storage enforce' or start the server to evict sealed epochs",
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
            print("Review the Minecraft EULA, then set server.eula=true before starting.")
        print("Next: mc-recorder server start")
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
        print(
            f"Exported {result.sample_count} samples, {result.state_count} states, and "
            f"{result.action_count} actions to {result.output}"
        )
        if result.action_count == 0:
            print("WARNING: no applied serverbound action records matched the selection", file=sys.stderr)
        print(
            f"Attached RGB for {result.rgb_count} state(s) and scene frames for "
            f"{result.scene_count} state(s); modalities.jsonl marks all missing references explicitly."
        )
        return 0

    if args.command == "render":
        config = load_config(args.config)
        with operation_lock(config.paths.runtime, "render_prepare"):
            episode = resolve_episode(config.paths.captures, args.episode)
            replay = resolve_replay(config.paths.replays, args.player, args.replay)
            output = args.output or (
                config.paths.exports / "render-jobs" / f"{args.episode}-{args.player}"
            )
            result = prepare_render_job(
                episode,
                replay,
                output,
                player_uuid=args.player,
                connection_id=args.connection,
                width=args.width,
                height=args.height,
                fps=args.fps,
                first_tick=args.from_tick,
                last_tick=args.to_tick,
                force=args.force,
                no_gui=args.no_gui,
            )
        print(f"Prepared render job {result.manifest}")
        if args.prepare_only:
            print(
                "Launch the 1.21.8 renderer client with "
                f"-Dmc.recorder.renderJob={result.manifest}"
            )
            return 0
        completed = launch_render_job(config, result)
        print(
            f"Rendered global ticks {completed['global_start_tick']}..{completed['global_end_tick']} "
            f"to {completed['output']}"
        )
        return 0

    if args.command == "scene":
        config = load_config(args.config)
        episode = resolve_episode(config.paths.captures, args.episode)
        output = _prepare_scene_output(args.output, force=args.force)
        with operation_lock(config.paths.runtime, "scene_extract"):
            cleanup_stale_scene_jobs(config.paths.runtime, keep=1)
            job = prepare_scene_job(
                config,
                episode,
                player_uuid=args.player,
                connection_id=args.connection,
                first_tick=args.from_tick,
                last_tick=args.to_tick,
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
        print(
            f"Extracted {info.frame_count} random-access scene frames to "
            f"{output}"
        )
        return 0

    if args.command == "storage":
        return _storage(args.config, args.storage_command == "enforce")

    if args.command == "dashboard":
        config = load_config(args.config)
        serve_dashboard(config)
        return 0

    if args.command == "render-rpc":
        config = load_config(args.config)
        body = _read_render_rpc_body()
        result = dispatch_render_rpc(config, args.render_rpc_action, body)
        if args.render_rpc_action == "finalize":
            result = _attach_finalized_render(config, result)
        print(json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False))
        return 0

    if args.command == "render-worker":
        config = load_config(args.config)
        remote = RemoteRecorder.parse(args.host, args.remote_root)
        if not 1.0 <= args.poll_interval <= 30.0:
            raise RecorderError("--poll-interval must be between 1 and 30 seconds")
        stop_after_one = args.once or args.job is not None
        if not stop_after_one:
            print(
                "RGB render worker is running; waiting for server jobs. Press Ctrl-C to stop.",
                flush=True,
            )

        def report_result(result: dict[str, object]) -> None:
            job = result.get("job") if isinstance(result.get("job"), dict) else {}
            state = job.get("state", "processed")
            print(
                f"RGB render job {job.get('id', args.job or '')} is {state}.",
                flush=True,
            )
            if result.get("local_workspace"):
                print(
                    f"Retained local render workspace: {result['local_workspace']}",
                    flush=True,
                )

        def report_error(message: str, retry_seconds: float) -> None:
            print(
                f"mc-recorder: render worker error: {message}; retrying in {retry_seconds:g}s",
                file=sys.stderr,
                flush=True,
            )

        result = run_render_worker(
            config,
            remote,
            job_id=args.job,
            cache_root=args.cache,
            keep_workspace=args.keep_workspace,
            once=args.once,
            poll_interval=args.poll_interval,
            on_result=report_result,
            on_error=report_error,
        )
        if stop_after_one and result is None:
            print("No queued RGB render job is ready on the server.")
        return 0

    config = load_config(args.config)
    if args.server_command == "start":
        jar, report = start_server(config, wait=args.wait)
        _print_storage(report)
        print(f"Server started on port {config.server.port}; local capture mod: {jar}")
        print("Recording begins automatically when any player joins.")
        return 0
    if args.server_command == "stop":
        if args.timeout < 1:
            raise RecorderError("--timeout must be positive")
        report = stop_server(config, timeout_seconds=args.timeout)
        _print_storage(report)
        print("Server stopped; completed recorder files are sealed asynchronously by the mods.")
        return 0
    if args.server_command == "status":
        code = show_status(config)
        used = directory_size(config.paths.captures) + directory_size(config.paths.replays)
        if used >= config.storage.quota_bytes * config.storage.warn_percent // 100:
            print(
                f"WARNING: capture and replay storage uses {human_bytes(used)} of "
                f"{human_bytes(config.storage.quota_bytes)}",
                file=sys.stderr,
            )
        return code
    if args.tail < 0:
        raise RecorderError("--tail cannot be negative")
    return show_logs(config, follow=args.follow, tail=args.tail, service=args.service)


def _read_render_rpc_body() -> dict[str, object]:
    maximum = 64 * 1024
    raw = sys.stdin.buffer.read(maximum + 1)
    if len(raw) > maximum:
        raise RecorderError("render RPC request exceeds the size limit")
    try:
        value = json.loads(
            raw or b"{}",
            parse_constant=lambda token: (_ for _ in ()).throw(
                ValueError(f"non-finite JSON constant {token}")
            ),
        )
    except (ValueError, UnicodeDecodeError, RecursionError) as exc:
        raise RecorderError("render RPC request is invalid JSON") from exc
    if not isinstance(value, dict):
        raise RecorderError("render RPC request must be a JSON object")
    return value


def _attach_finalized_render(
    config: RecorderConfig, finalized: dict[str, object]
) -> dict[str, object]:
    job = finalized.get("job")
    imports = finalized.get("imports")
    if not isinstance(job, dict) or not isinstance(imports, list):
        raise RecorderError("render finalization lacks its queue job or canonical imports")
    state = job.get("state")
    if state in {"complete", "partial"}:
        return finalized
    if state not in {"verifying", "attaching"}:
        raise RecorderError(f"render finalization cannot attach while job is {state!r}")
    job_id = job.get("id")
    if not isinstance(job_id, str):
        raise RecorderError("render finalization lacks its queue job ID")

    queue = RenderQueueStore(config.paths.runtime / "render-queue.sqlite3")
    if state == "verifying":
        job = queue.set_server_phase(
            job_id,
            "attaching",
            progress={"message": "Attaching verified RGB to the dataset"},
        )
    try:
        attachment = attach_imported_renders(config, job, imports)
        attachment_json = attachment.as_json()
        completed = queue.complete(job_id, attachment_json, partial=attachment.partial)
    except Exception as exc:
        try:
            queue.fail(job_id, str(exc))
        except Exception:
            pass
        if isinstance(exc, RecorderError):
            raise
        raise RecorderError(f"could not attach the imported RGB dataset: {exc}") from exc
    return {**finalized, "job": completed, "attachment": attachment_json}


def main(argv: Sequence[str] | None = None) -> int:
    try:
        return run(argv)
    except RecorderError as exc:
        print(f"mc-recorder: error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("mc-recorder: interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
