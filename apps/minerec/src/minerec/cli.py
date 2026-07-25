from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Sequence

from .config import DEFAULT_CONFIG_NAME, initialize, load_config
from .errors import RecorderError
from .operations import operation_lock
from .processing.actions import extract_actions
from .processing.capture.episodes import EpisodeInfo, list_episodes, resolve_episode, validate_episode
from .processing.capture.pinning import pin_sealed_epochs
from .processing.render.job import launch_render_job, prepare_render_job
from .processing.scene.job import cleanup_scene_job, cleanup_stale_scene_jobs, launch_scene_job, prepare_scene_job
from .processing.scene.store import compact_scene_stream
from .processing.scene.store_v2 import finalize_scene_store_v2, validate_scene_store_v2


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="minerec", description="Minecraft gameplay recorder processors")
    parser.add_argument("--config", "-c", default=DEFAULT_CONFIG_NAME, help="recorder TOML path (default: recorder.toml)")
    commands = parser.add_subparsers(dest="command", required=True)

    init = commands.add_parser("init", help="create recorder.toml and workspace directories")
    init.add_argument("--accept-eula", action="store_true")
    init.add_argument("--force", action="store_true")

    sessions = commands.add_parser("sessions", help="inspect recorder intermediate sessions")
    session_commands = sessions.add_subparsers(dest="sessions_command", required=True)
    session_list = session_commands.add_parser("list", help="list intermediate sessions")
    session_list.add_argument("--json", action="store_true", dest="as_json")
    validate = session_commands.add_parser("validate", help="validate sealed epoch envelopes")
    validate.add_argument("session", nargs="?", help="session ID; validates every session when omitted")
    validate.add_argument("--json", action="store_true", dest="as_json")

    actions = commands.add_parser("actions", help="reconstruct player actions")
    action_commands = actions.add_subparsers(dest="actions_command", required=True)
    action_extract = action_commands.add_parser("extract", help="write one connection action stream")
    _add_connection_inputs(action_extract)
    action_extract.add_argument("--output", "-o", type=Path, required=True, help="actions.jsonl output")
    action_extract.add_argument("--force", action="store_true")

    render = commands.add_parser("render", help="render one Flashback replay into an explicit output directory")
    _add_connection_inputs(render)
    render.add_argument("--replay", type=Path, required=True, help="Flashback replay ZIP input")
    render.add_argument("--output", "-o", type=Path, required=True, help="renders directory output")
    render.add_argument("--width", type=int, default=640)
    render.add_argument("--height", type=int, default=360)
    render.add_argument("--fps", type=int, default=20)
    render.add_argument("--no-gui", action="store_true", help="omit the client HUD")
    render.add_argument("--force", action="store_true")
    render.add_argument("--prepare-only", action="store_true")

    scene = commands.add_parser("scene", help="extract one connection into Scene Store V2")
    scene_commands = scene.add_subparsers(dest="scene_command", required=True)
    scene_extract = scene_commands.add_parser("extract", help="write scene.sqlite3 from explicit inputs")
    _add_connection_inputs(scene_extract)
    scene_extract.add_argument(
        "--replay",
        type=Path,
        required=True,
        action="append",
        help="Flashback replay ZIP input; repeat for every contributing segment",
    )
    scene_extract.add_argument("--output", "-o", type=Path, required=True, help="scene.sqlite3 output")
    scene_extract.add_argument("--force", action="store_true")
    scene_extract.add_argument("--prepare-only", action="store_true")
    return parser


def _add_connection_inputs(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("events", type=Path, help="closed recorder intermediate session")
    parser.add_argument("--player", required=True, help="recorded player UUID")
    parser.add_argument("--connection", required=True, help="recorded connection UUID")
    parser.add_argument("--from-tick", type=int)
    parser.add_argument("--to-tick", type=int)


def _prepare_scene_output(path: Path, *, force: bool) -> Path:
    requested = Path(os.path.abspath(path.expanduser()))
    if requested.is_symlink():
        raise RecorderError(f"scene store output may not be a symlink: {requested}")
    missing: list[str] = []
    ancestor = requested.parent
    while not ancestor.exists() and not ancestor.is_symlink():
        missing.append(ancestor.name)
        ancestor = ancestor.parent
    if ancestor.is_symlink() or not ancestor.is_dir():
        raise RecorderError(f"scene store parent is not a safe directory: {ancestor}")
    parent = ancestor.resolve()
    for name in reversed(missing):
        parent /= name
    parent.mkdir(parents=True, exist_ok=True)
    if parent.is_symlink() or not parent.is_dir():
        raise RecorderError(f"scene store parent is not a safe directory: {parent}")
    output = parent / requested.name
    if output.exists() or output.is_symlink():
        if not force:
            raise RecorderError(f"scene store output exists: {output}; pass --force to replace it")
        validate_scene_store_v2(output)
    return output


def _episode_json(info: EpisodeInfo) -> dict[str, object]:
    value = asdict(info)
    value["path"] = str(info.path)
    return value


def _sessions(config_path: str, session_id: str | None, *, validate: bool, as_json: bool) -> int:
    config = load_config(config_path)
    if validate:
        paths = [resolve_episode(config.paths.sessions, session_id)] if session_id else [item.path for item in list_episodes(config.paths.sessions)]
        results = [validate_episode(path) for path in paths]
        if as_json:
            print(json.dumps([result.as_json() for result in results], indent=2, sort_keys=True))
        else:
            for result in results:
                print(f"{result.session_id}: {'valid' if result.valid else 'INVALID'}; {result.sealed_epochs} sealed epoch(s)")
                for issue in result.issues:
                    print(f"  {issue.severity.upper()}: {issue.path}: {issue.message}")
        return 0 if all(result.valid for result in results) else 1
    sessions = list_episodes(config.paths.sessions)
    if as_json:
        print(json.dumps([_episode_json(info) for info in sessions], indent=2, sort_keys=True))
    else:
        for info in sessions:
            tick_range = "-" if info.first_tick is None else f"{info.first_tick}..{info.last_tick}"
            print(f"{info.session_id}\t{info.status}\t{tick_range}")
    return 0


def run(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "init":
        path = initialize(args.config, accept_eula=args.accept_eula, force=args.force)
        print(f"Initialized {path}")
        return 0

    if args.command == "sessions":
        return _sessions(
            args.config,
            getattr(args, "session", None),
            validate=args.sessions_command == "validate",
            as_json=args.as_json,
        )

    config = load_config(args.config)
    if args.command == "actions":
        with operation_lock(config.paths.runtime, "actions_extract"):
            result = extract_actions(
                args.events,
                args.output,
                player_uuid=args.player,
                connection_id=args.connection,
                first_tick=args.from_tick,
                last_tick=args.to_tick,
                force=args.force,
            )
        print(f"Extracted {result.record_count} actions for ticks {result.first_tick}..{result.last_tick} to {result.output}")
        return 0

    if args.command == "render":
        with operation_lock(config.paths.runtime, "render_prepare"):
            job = prepare_render_job(
                args.events,
                args.replay,
                args.output,
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
        print(f"Prepared render job {job.manifest}")
        if args.prepare_only:
            return 0
        result = launch_render_job(config, job)
        print(f"Rendered ticks {result['global_start_tick']}..{result['global_end_tick']} to {result['output']}")
        return 0

    if args.command == "scene":
        output = _prepare_scene_output(args.output, force=args.force)
        with operation_lock(config.paths.runtime, "scene_extract"):
            cleanup_stale_scene_jobs(config.paths.runtime, keep=1)
            with pin_sealed_epochs(args.events) as pinned_epochs:
                job = prepare_scene_job(
                    config,
                    args.events,
                    args.replay,
                    player_uuid=args.player,
                    connection_id=args.connection,
                    first_tick=args.from_tick,
                    last_tick=args.to_tick,
                    pinned_epoch_paths=pinned_epochs,
                )
            print(f"Prepared scene extraction job {job.manifest}")
            if args.prepare_only:
                return 0
            try:
                stream = launch_scene_job(config, job)
                private_v1 = job.directory / "scene-v1.sqlite3"
                compact_scene_stream(
                    job.stream,
                    private_v1,
                    expected_session_id=job.session_id,
                    expected_player_uuid=job.player_uuid,
                    expected_connection_id=job.connection_id,
                    expected_ticks=job.state_ticks,
                    force=False,
                    verified_stream=stream,
                )
                info = finalize_scene_store_v2(private_v1, job.player_states, output, force=args.force)
            except BaseException:
                cleanup_stale_scene_jobs(config.paths.runtime, keep=1)
                raise
            else:
                cleanup_scene_job(job)
        print(f"Extracted {info.frame_count} Scene Store V2 frames to {output}")
        return 0
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
