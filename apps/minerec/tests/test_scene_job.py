from __future__ import annotations

import hashlib
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
from minerec.processing.capture.episodes import inspect_epoch
from minerec.processing.replays import FLASHBACK_CAPTURE_CONTRACT, ReplaySegmentSource
from minerec.processing.scene.job import SubjectPoseSourceEpoch, _resolve_scene_extractor_executable, _select_subject_poses, cleanup_scene_job, prepare_scene_job

PLAYER = "00000000-0000-4000-8000-000000000001"
CONNECTION = "00000000-0000-4000-8000-000000000002"
SEGMENT = "00000000-0000-4000-8000-000000000003"


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


def _sealed_epoch(root: Path, records: list[dict[str, object]]):  # noqa: ANN202
    epoch = root / "epoch-000000"
    epoch.mkdir(parents=True)
    events = b"".join(json.dumps(record, separators=(",", ":")).encode() + b"\n" for record in records)
    (epoch / "events.jsonl").write_bytes(events)
    (epoch / "manifest.json").write_text(
        json.dumps(
            {
                "sealed": True,
                "record_count": len(records),
                "events_bytes": len(events),
                "events_sha256": hashlib.sha256(events).hexdigest(),
                "first_server_tick": records[0]["server_tick"],
                "last_server_tick": records[-1]["server_tick"],
            }
        ),
        encoding="utf-8",
    )
    result = inspect_epoch(epoch)
    assert result is not None
    return result


class SceneJobTest(unittest.TestCase):
    def test_selection_keeps_complete_states_beside_minimal_extractor_poses(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            epoch = _sealed_epoch(Path(temporary), [_state(10), _state(11)])
            selection = _select_subject_poses(
                session_id="session-a",
                player_uuid=PLAYER,
                connection_id=CONNECTION,
                epochs=[epoch],
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
            events = config.paths.sessions / "session-a"
            events.mkdir()
            replay = root / "segment.zip"
            replay.write_bytes(b"replay")
            source = ReplaySegmentSource(
                segment_id=SEGMENT,
                segment_ordinal=0,
                player_uuid=PLAYER,
                connection_id=CONNECTION,
                path=replay,
                replay_format="flashback",
                sha256=hashlib.sha256(b"replay").hexdigest(),
                size_bytes=6,
                flashback_capture_contract=FLASHBACK_CAPTURE_CONTRACT,
            )
            state_line = json.dumps(_state(10), sort_keys=True, separators=(",", ":")).encode() + b"\n"
            selection = SimpleNamespace(
                data=b'{"server_tick":10}\n',
                player_states=state_line,
                ticks=(10,),
                source_epochs=(SubjectPoseSourceEpoch(0, "a" * 64, 1, 1),),
            )
            with (
                mock.patch("minerec.processing.scene.job.validate_episode", return_value=SimpleNamespace(valid=True, sealed_epochs=1, session_id="session-a")),
                mock.patch("minerec.processing.scene.job._select_subject_poses", return_value=selection),
                mock.patch("minerec.processing.scene.job.resolve_replay_segments", return_value=(source,)) as resolve,
            ):
                job = prepare_scene_job(config, events, [replay], player_uuid=PLAYER, connection_id=CONNECTION)
            resolve.assert_called_once_with([replay], player_uuid=PLAYER, connection_id=CONNECTION)
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
