from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Sequence

from .config import DEFAULT_CONFIG_NAME, initialize, load_config
from .errors import RecorderError
from .operations import operation_lock
from .processing.actions import extract_actions
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

    actions = commands.add_parser("actions", help="reconstruct player actions")
    action_commands = actions.add_subparsers(dest="actions_command", required=True)
    action_extract = action_commands.add_parser("extract", help="write one connection action stream")
    _add_capture_inputs(action_extract)
    action_extract.add_argument("--output", "-o", type=Path, required=True, help="actions.jsonl output")
    action_extract.add_argument("--force", action="store_true")

    render = commands.add_parser("render", help="render one Flashback replay into an explicit output directory")
    _add_capture_inputs(render)
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
    _add_capture_inputs(scene_extract)
    scene_extract.add_argument("--replay", type=Path, required=True, help="capture/replay.zip input")
    scene_extract.add_argument("--output", "-o", type=Path, required=True, help="scene.sqlite3 output")
    scene_extract.add_argument("--force", action="store_true")
    scene_extract.add_argument("--prepare-only", action="store_true")
    return parser


def _add_capture_inputs(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--metadata", type=Path, required=True, help="completed play metadata.json input")
    parser.add_argument("--events", type=Path, required=True, help="capture/events.jsonl input")
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


def run(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "init":
        path = initialize(args.config, accept_eula=args.accept_eula, force=args.force)
        print(f"Initialized {path}")
        return 0

    config = load_config(args.config)
    if args.command == "actions":
        with operation_lock(config.paths.runtime, "actions_extract"):
            result = extract_actions(
                args.metadata,
                args.events,
                args.output,
                first_tick=args.from_tick,
                last_tick=args.to_tick,
                force=args.force,
            )
        print(f"Extracted {result.record_count} actions for ticks {result.first_tick}..{result.last_tick} to {result.output}")
        return 0

    if args.command == "render":
        with operation_lock(config.paths.runtime, "render_prepare"):
            job = prepare_render_job(
                args.metadata,
                args.events,
                args.replay,
                args.output,
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
            job = prepare_scene_job(
                config,
                args.metadata,
                args.events,
                args.replay,
                first_tick=args.from_tick,
                last_tick=args.to_tick,
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
