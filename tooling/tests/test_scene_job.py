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

from mc_recorder.config import initialize, load_config
from mc_recorder.render_sources import ReplaySegmentSource
from mc_recorder.scene_job import launch_scene_job, prepare_scene_job


PLAYER = "00000000-0000-4000-8000-000000000001"
CONNECTION = "00000000-0000-4000-8000-000000000002"
SEGMENT = "00000000-0000-4000-8000-000000000003"


class SceneJobTest(unittest.TestCase):
    def _fixture(self, root: Path):
        config = load_config(initialize(root / "recorder.toml", accept_eula=True))
        config.mods.scene_extractor_project.mkdir()
        (root / "gradlew").write_text("wrapper\n", encoding="utf-8")
        episode = config.paths.captures / "session-a"
        episode.mkdir(parents=True)
        replay = config.paths.replays / "segment.zip"
        replay.parent.mkdir(parents=True, exist_ok=True)
        replay.write_bytes(b"immutable replay")
        source = ReplaySegmentSource(
            segment_id=SEGMENT,
            segment_ordinal=0,
            player_uuid=PLAYER,
            connection_id=CONNECTION,
            path=replay,
            replay_format="flashback",
            sha256=hashlib.sha256(replay.read_bytes()).hexdigest(),
            size_bytes=replay.stat().st_size,
        )
        return config, episode, source

    def _prepare(self, root: Path):
        config, episode, source = self._fixture(root)
        with (
            mock.patch(
                "mc_recorder.scene_job.validate_episode",
                return_value=SimpleNamespace(valid=True, sealed_epochs=1, session_id="session-a"),
            ),
            mock.patch(
                "mc_recorder.scene_job.subject_state_ticks", return_value=(10, 11)
            ),
            mock.patch(
                "mc_recorder.scene_job.resolve_replay_segments", return_value=[source]
            ),
        ):
            job = prepare_scene_job(
                config,
                episode,
                player_uuid=PLAYER,
                connection_id=CONNECTION,
            )
        return config, job

    def test_prepares_the_exact_server_only_contract_without_creating_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            _config, job = self._prepare(Path(temporary))
            value = json.loads(job.manifest.read_text(encoding="utf-8"))

            self.assertEqual(
                {
                    "schema_version",
                    "job_id",
                    "session_id",
                    "subject",
                    "global_start_tick",
                    "global_end_tick",
                    "scope",
                    "metadata_policy",
                    "source_replays",
                    "output",
                    "stop_when_done",
                },
                set(value),
            )
            self.assertEqual("client_visible", value["scope"])
            self.assertEqual("full_packet_metadata", value["metadata_policy"])
            self.assertFalse(job.stream.exists())
            self.assertFalse(job.result.exists())

    @mock.patch("mc_recorder.scene_job.subprocess.run")
    def test_launch_holds_sources_and_accepts_only_an_identity_bound_result(
        self, run: mock.Mock
    ) -> None:
        run.return_value = SimpleNamespace(returncode=0, stderr="")
        with tempfile.TemporaryDirectory() as temporary:
            config, job = self._prepare(Path(temporary))
            job.stream.mkdir()
            job.result.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "result_type": "mc-recorder-scene-extraction-result-v1",
                        "status": "complete",
                        "job_id": job.job_id,
                        "session_id": job.session_id,
                        "player_uuid": job.player_uuid,
                        "connection_id": job.connection_id,
                        "global_start_tick": 10,
                        "global_end_tick": 11,
                        "stream": {
                            "format": "mc-recorder-scene-stream-v1",
                            "path": str(job.stream),
                            "frames_index": "frames.jsonl",
                            "changes_index": "changes.jsonl",
                            "frame_count": 2,
                        },
                    }
                ),
                encoding="utf-8",
            )

            result = launch_scene_job(config, job, capture_output=True)

            self.assertEqual("complete", result["status"])
            environment = run.call_args.kwargs["env"]
            self.assertEqual(str(job.manifest), environment["MC_RECORDER_SCENE_JOB"])
            self.assertEqual(
                "eula=true\n",
                (config.mods.scene_extractor_project / "run" / "eula.txt").read_text(),
            )


if __name__ == "__main__":
    unittest.main()
