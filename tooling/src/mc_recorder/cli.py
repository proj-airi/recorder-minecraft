from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Sequence

from .config import DEFAULT_CONFIG_NAME, initialize, load_config
from .episodes import (
    EpisodeInfo,
    directory_size,
    list_episodes,
    resolve_episode,
    validate_episode,
)
from .errors import RecorderError
from .exporter import export_episode
from .render_job import launch_render_job, prepare_render_job, resolve_replay
from .server import check_storage, show_logs, show_status, start_server, stop_server
from .storage import enforce_quota, human_bytes


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
        "--voxels",
        action="append",
        type=Path,
        default=[],
        help="render job directory or voxels.jsonl to attach; repeatable",
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
    render.add_argument(
        "--voxel-horizontal-radius",
        type=int,
        default=0,
        help="capture block states this many blocks around X/Z; 0 disables voxel conversion",
    )
    render.add_argument(
        "--voxel-vertical-radius",
        type=int,
        default=0,
        help="capture block states this many blocks above/below Y; 0 disables voxel conversion",
    )
    render.add_argument("--from-tick", type=int)
    render.add_argument("--to-tick", type=int)
    render.add_argument("--force", action="store_true")
    render.add_argument(
        "--prepare-only", action="store_true", help="write render-job.json without launching the local client"
    )

    storage = commands.add_parser("storage", help="inspect or enforce capture retention")
    storage_commands = storage.add_subparsers(dest="storage_command", required=True)
    storage_commands.add_parser("status", help="show capture plus replay quota without deleting data")
    storage_commands.add_parser("enforce", help="evict oldest verified immutable source units when over quota")
    return parser


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
    report = enforce_quota(
        config.paths.captures,
        quota_bytes=config.storage.quota_bytes,
        warn_percent=config.storage.warn_percent,
        evict_oldest=config.storage.evict_oldest if enforce else False,
        replays_root=config.paths.replays,
    )
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
        result = export_episode(
            episode,
            output,
            players=args.player,
            connections=args.connection,
            first_tick=args.from_tick,
            last_tick=args.to_tick,
            frames=args.frames,
            voxels=args.voxels,
            force=args.force,
        )
        print(
            f"Exported {result.sample_count} samples, {result.state_count} states, and "
            f"{result.action_count} actions to {result.output}"
        )
        if result.action_count == 0:
            print("WARNING: no applied serverbound action records matched the selection", file=sys.stderr)
        print(
            f"Attached RGB for {result.rgb_count} state(s) and voxels for "
            f"{result.voxel_count} state(s); modalities.jsonl marks all missing references explicitly."
        )
        return 0

    if args.command == "render":
        config = load_config(args.config)
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
            voxel_horizontal_radius=args.voxel_horizontal_radius,
            voxel_vertical_radius=args.voxel_vertical_radius,
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

    if args.command == "storage":
        return _storage(args.config, args.storage_command == "enforce")

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
