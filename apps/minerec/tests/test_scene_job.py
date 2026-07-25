from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from minerec.cli import _prepare_scene_output
from minerec.config import initialize, load_config
from minerec.errors import RecorderError
from minerec.processing.capture import load_capture_metadata
from minerec.processing.replays import FLASHBACK_CAPTURE_CONTRACT, ReplaySource
from minerec.processing.scene.job import SubjectPoseSourceEvents, _resolve_scene_extractor_executable, _select_subject_poses, cleanup_scene_job, prepare_scene_job
from play_fixture import CONNECTION, PLAYER, completed_capture

REPLAY = "00000000-0000-4000-8000-000000000003"


def _state(tick: int) -> dict[str, object]:
    return {
        "schema_version": 1,
        "record_type": "player_state",
        "session_id": "session-a",
        "server_tick": tick,
        "sequence": tick,
        "player_uuid": PLAYER,
        "connection_id": CONNECTION,
        "entity_id": 7,
        "dimension": "minecraft:overworld",
        "position": {"x": float(tick), "y": 64.0, "z": 0.0},
        "velocity": {"x": 0.0, "y": 0.0, "z": 0.0},
        "rotation": {"yaw": 0.0, "pitch": 0.0, "head_yaw": 0.0},
        "on_ground": True,
        "inventory": [{"slot": 0, "item": "minecraft:stone", "count": 1}],
    }


class SceneJobTest(unittest.TestCase):
    def test_selection_keeps_complete_states_beside_minimal_extractor_poses(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            metadata_path, events = completed_capture(Path(temporary) / "play", [_state(10), _state(11)])
            metadata = load_capture_metadata(metadata_path)
            selection = _select_subject_poses(
                session_id="session-a",
                player_uuid=PLAYER,
                connection_id=CONNECTION,
                metadata=metadata,
                events=events,
            )
            states = [json.loads(line) for line in selection.player_states.splitlines()]
            poses = [json.loads(line) for line in selection.data.splitlines()]
            self.assertEqual((10, 11), selection.ticks)
            self.assertIn("inventory", states[0])
            self.assertNotIn("inventory", poses[0])

    def test_prepare_uses_explicit_replays_and_private_state_staging(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = load_config(initialize(root / "recorder.toml"))
            metadata, events = completed_capture(root / "play", [_state(10)])
            replay = root / "segment.zip"
            replay.write_bytes(b"replay")
            source = ReplaySource(
                replay_id=REPLAY,
                player_uuid=PLAYER,
                connection_id=CONNECTION,
                path=replay,
                replay_format="flashback",
                sha256="a" * 64,
                size_bytes=6,
                flashback_capture_contract=FLASHBACK_CAPTURE_CONTRACT,
            )
            state_line = json.dumps(_state(10), sort_keys=True, separators=(",", ":")).encode() + b"\n"
            selection = SimpleNamespace(
                data=b'{"server_tick":10}\n',
                player_states=state_line,
                ticks=(10,),
                source_events=SubjectPoseSourceEvents("b" * 64, 1, 1),
            )
            with (
                mock.patch("minerec.processing.scene.job._select_subject_poses", return_value=selection),
                mock.patch("minerec.processing.scene.job.verified_replay_source", return_value=source) as resolve,
            ):
                job = prepare_scene_job(config, metadata, events, replay)
            resolve.assert_called_once_with(replay, player_uuid=PLAYER, connection_id=CONNECTION)
            self.assertEqual(state_line, job.player_states.read_bytes())
            self.assertNotIn("player-states.jsonl", json.loads(job.manifest.read_text(encoding="utf-8")))
            cleanup_scene_job(job)
            self.assertFalse(job.directory.exists())

    def test_scene_output_rejects_symlinked_parent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "target"
            target.mkdir()
            linked = root / "linked"
            linked.symlink_to(target, target_is_directory=True)
            with self.assertRaisesRegex(RecorderError, "safe directory"):
                _prepare_scene_output(linked / "scene.sqlite3", force=False)

    def test_extractor_override_must_be_absolute_and_executable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = load_config(initialize(root / "recorder.toml"))
            executable = root / "extractor"
            executable.write_text("#!/bin/sh\n", encoding="utf-8")
            executable.chmod(0o755)
            self.assertEqual(str(executable), _resolve_scene_extractor_executable(config, {"MC_RECORDER_SCENE_EXTRACTOR": str(executable)}))
            with self.assertRaisesRegex(RecorderError, "absolute"):
                _resolve_scene_extractor_executable(config, {"MC_RECORDER_SCENE_EXTRACTOR": "relative"})


if __name__ == "__main__":
    unittest.main()
