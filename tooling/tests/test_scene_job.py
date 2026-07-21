from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import unittest
import zlib
from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from mc_recorder.cli import _parser, _prepare_scene_output, run
from mc_recorder.config import initialize, load_config
from mc_recorder.errors import RecorderError
from mc_recorder.render_sources import ReplaySegmentSource
from mc_recorder.scene_job import (
    _validate_result,
    cleanup_scene_job,
    cleanup_stale_scene_jobs,
    launch_scene_job,
    prepare_scene_job,
)
from mc_recorder.scene_store import SceneStoreError, compact_scene_stream


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

    def _write_success(self, job) -> dict[str, object]:
        job.stream.mkdir()
        (job.stream / "blobs").mkdir()
        frames = b'{"server_tick":10}\n{"server_tick":11}\n'
        changes = b'{"type":"segment_begin"}\n'
        (job.stream / "frames.jsonl").write_bytes(frames)
        (job.stream / "changes.jsonl").write_bytes(changes)
        source = job.sources[0]
        value = {
            "schema_version": 1,
            "result_type": "mc-recorder-scene-extraction-result-v1",
            "status": "complete",
            "job_id": job.job_id,
            "session_id": job.session_id,
            "player_uuid": job.player_uuid,
            "connection_id": job.connection_id,
            "global_start_tick": 10,
            "global_end_tick": 11,
            "scope": "client_visible",
            "metadata_policy": "full_packet_metadata",
            "source_replays": [
                {
                    "segment_id": source.segment_id,
                    "segment_ordinal": source.segment_ordinal,
                    "path": str(source.path),
                    "sha256": source.sha256,
                    "size_bytes": source.size_bytes,
                    "format": source.replay_format,
                }
            ],
            "stream": {
                "format": "mc-recorder-scene-stream-v1",
                "path": str(job.stream),
                "frames_index": "frames.jsonl",
                "changes_index": "changes.jsonl",
                "blobs_directory": "blobs",
                "frame_count": 2,
                "change_count": 1,
                "blob_count": 0,
                "blob_bytes": 0,
                "frames_sha256": hashlib.sha256(frames).hexdigest(),
                "frames_size_bytes": len(frames),
                "changes_sha256": hashlib.sha256(changes).hexdigest(),
                "changes_size_bytes": len(changes),
            },
            "ignored_packet_counts": {},
            "covered_tick_count": 2,
        }
        job.result.write_text(json.dumps(value), encoding="utf-8")
        return value

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

    def test_result_accepts_only_an_ordered_contributing_source_subset(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _config, prepared = self._prepare(root)
            replay = prepared.sources[0].path.with_name("trailing-segment.zip")
            replay.write_bytes(b"unused trailing replay")
            trailing = ReplaySegmentSource(
                segment_id="00000000-0000-4000-8000-000000000004",
                segment_ordinal=1,
                player_uuid=PLAYER,
                connection_id=CONNECTION,
                path=replay,
                replay_format="flashback",
                sha256=hashlib.sha256(replay.read_bytes()).hexdigest(),
                size_bytes=replay.stat().st_size,
            )
            job = replace(prepared, sources=prepared.sources + (trailing,))
            value = self._write_success(job)

            integrity = _validate_result(job, value)

            self.assertEqual(2, integrity.frame_count)
            first = value["source_replays"][0]  # type: ignore[index]
            trailing_value = {
                "segment_id": trailing.segment_id,
                "segment_ordinal": trailing.segment_ordinal,
                "path": str(trailing.path),
                "sha256": trailing.sha256,
                "size_bytes": trailing.size_bytes,
                "format": trailing.replay_format,
            }
            value["source_replays"] = [trailing_value, first]
            with self.assertRaisesRegex(RecorderError, "ordered exact subset"):
                _validate_result(job, value)

    def test_prepare_rejects_an_unpinned_new_seal_before_reading_epoch_data(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config, episode, _source = self._fixture(root)
            pinned = episode / "epochs" / "epoch-000000"
            new_seal = episode / "epochs" / "epoch-000001"
            for path in (pinned, new_seal):
                path.mkdir(parents=True)
                (path / "manifest.json").write_text(
                    json.dumps({"sealed": True}), encoding="utf-8"
                )

            with (
                mock.patch("mc_recorder.scene_job.inspect_epoch") as inspect,
                mock.patch("mc_recorder.scene_job.validate_episode") as validate,
            ):
                with self.assertRaisesRegex(RecorderError, "set changed"):
                    prepare_scene_job(
                        config,
                        episode,
                        player_uuid=PLAYER,
                        connection_id=CONNECTION,
                        pinned_epoch_paths=(pinned.resolve(),),
                    )

            inspect.assert_not_called()
            validate.assert_not_called()

    def test_prepare_reads_only_the_exact_pinned_epoch_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config, episode, source = self._fixture(root)
            epoch = episode / "epochs" / "epoch-000000"
            epoch.mkdir(parents=True)
            (epoch / "manifest.json").write_text(
                json.dumps({"sealed": True}), encoding="utf-8"
            )
            info = SimpleNamespace(index=0, path=epoch, status="sealed")
            validation = SimpleNamespace(
                valid=True, sealed_epochs=1, session_id="session-a"
            )
            with (
                mock.patch("mc_recorder.scene_job.inspect_epoch", return_value=info),
                mock.patch(
                    "mc_recorder.scene_job.validate_episode", return_value=validation
                ) as validate,
                mock.patch(
                    "mc_recorder.scene_job.subject_state_ticks", return_value=(10, 11)
                ) as state_ticks,
                mock.patch(
                    "mc_recorder.scene_job.resolve_replay_segments", return_value=[source]
                ),
            ):
                prepare_scene_job(
                    config,
                    episode,
                    player_uuid=PLAYER,
                    connection_id=CONNECTION,
                    pinned_epoch_paths=(epoch.resolve(),),
                )

            validate.assert_called_once_with(episode, epochs=(info,))
            self.assertEqual((info,), state_ticks.call_args.kwargs["epochs"])

    def test_scene_extract_force_is_explicit(self) -> None:
        arguments = _parser().parse_args(
            [
                "scene",
                "extract",
                "session-a",
                "--player",
                PLAYER,
                "--connection",
                CONNECTION,
                "--output",
                "scene.sqlite3",
                "--force",
            ]
        )
        self.assertTrue(arguments.force)

    def test_scene_output_creates_safe_parent_and_rejects_symlink_parent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = _prepare_scene_output(
                root / "artifacts" / "scenes" / "scene.sqlite3", force=False
            )
            self.assertTrue(output.parent.is_dir())
            linked = root / "linked"
            linked.symlink_to(root / "artifacts", target_is_directory=True)
            with self.assertRaisesRegex(RecorderError, "safe directory"):
                _prepare_scene_output(linked / "scene.sqlite3", force=False)

    def test_scene_cli_keeps_only_newest_job_when_launch_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = SimpleNamespace(
                paths=SimpleNamespace(captures=root / "captures", runtime=root / "runtime")
            )
            job = SimpleNamespace(manifest=root / "job" / "scene-job.json")
            with (
                mock.patch("mc_recorder.cli.load_config", return_value=config),
                mock.patch("mc_recorder.cli.resolve_episode", return_value=root / "episode"),
                mock.patch("mc_recorder.cli.operation_lock", return_value=nullcontext()),
                mock.patch(
                    "mc_recorder.cli.pin_sealed_epochs",
                    return_value=nullcontext(()),
                ),
                mock.patch("mc_recorder.cli.prepare_scene_job", return_value=job),
                mock.patch(
                    "mc_recorder.cli.launch_scene_job",
                    side_effect=RecorderError("extractor failed"),
                ),
                mock.patch("mc_recorder.cli.cleanup_scene_job") as cleanup,
                mock.patch("mc_recorder.cli.cleanup_stale_scene_jobs") as cleanup_stale,
            ):
                with self.assertRaisesRegex(RecorderError, "extractor failed"):
                    run(
                        [
                            "scene",
                            "extract",
                            "session-a",
                            "--player",
                            PLAYER,
                            "--connection",
                            CONNECTION,
                            "--output",
                            str(root / "new" / "scene.sqlite3"),
                        ]
                    )
            cleanup.assert_not_called()
            self.assertEqual(
                [
                    mock.call(config.paths.runtime, keep=1),
                    mock.call(config.paths.runtime, keep=1),
                ],
                cleanup_stale.call_args_list,
            )

    @mock.patch("mc_recorder.scene_job.subprocess.run")
    def test_launch_holds_sources_and_accepts_only_an_identity_bound_result(
        self, run: mock.Mock
    ) -> None:
        run.return_value = SimpleNamespace(returncode=0, stderr="")
        with tempfile.TemporaryDirectory() as temporary:
            config, job = self._prepare(Path(temporary))
            self._write_success(job)

            result = launch_scene_job(config, job, capture_output=True)

            self.assertEqual("complete", result.result["status"])
            environment = run.call_args.kwargs["env"]
            self.assertEqual(str(job.manifest), environment["MC_RECORDER_SCENE_JOB"])
            command = run.call_args.args[0]
            self.assertIn(
                "-PmcRecorderSceneRunDir="
                + os.path.relpath(job.run_directory, config.mods.scene_extractor_project),
                command,
            )
            self.assertEqual(
                "eula=true\n",
                (job.run_directory / "eula.txt").read_text(),
            )
            self.assertIn("server-port=0\n", (job.run_directory / "server.properties").read_text())
            self.assertFalse((config.mods.scene_extractor_project / "run").exists())

    @mock.patch("mc_recorder.scene_job.subprocess.run")
    def test_launch_rejects_spool_bytes_that_do_not_match_terminal_result(
        self, run: mock.Mock
    ) -> None:
        run.return_value = SimpleNamespace(returncode=0, stderr="")
        with tempfile.TemporaryDirectory() as temporary:
            config, job = self._prepare(Path(temporary))
            self._write_success(job)
            (job.stream / "frames.jsonl").write_bytes(b"tampered\n")

            with self.assertRaisesRegex(RecorderError, "size|SHA-256"):
                launch_scene_job(config, job, capture_output=True)

    @mock.patch("mc_recorder.scene_job.subprocess.run")
    def test_launch_requires_the_exact_complete_result_and_canonical_blobs(
        self, run: mock.Mock
    ) -> None:
        run.return_value = SimpleNamespace(returncode=0, stderr="")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config, missing_field = self._prepare(root)
            value = self._write_success(missing_field)
            del value["stream"]["blob_bytes"]  # type: ignore[index]
            missing_field.result.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(RecorderError, "stream envelope"):
                launch_scene_job(config, missing_field, capture_output=True)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config, bad_blob = self._prepare(root)
            value = self._write_success(bad_blob)
            canonical = b'{"type_id":"minecraft:cow"}'
            digest = hashlib.sha256(canonical).hexdigest()
            compressed = zlib.compress(canonical)
            (bad_blob.stream / "blobs" / f"{digest}.zlib").write_bytes(compressed)
            changes = json.dumps(
                {"type": "entity_set", "blob_sha256": digest}, separators=(",", ":")
            ).encode() + b"\n"
            (bad_blob.stream / "changes.jsonl").write_bytes(changes)
            stream = value["stream"]
            assert isinstance(stream, dict)
            stream.update(
                {
                    "change_count": 1,
                    "changes_sha256": hashlib.sha256(changes).hexdigest(),
                    "changes_size_bytes": len(changes),
                    "blob_count": 1,
                    "blob_bytes": len(compressed),
                }
            )
            bad_blob.result.write_text(json.dumps(value), encoding="utf-8")
            (bad_blob.stream / "blobs" / f"{digest}.zlib").write_bytes(
                zlib.compress(b"tampered")
            )
            with self.assertRaisesRegex(RecorderError, "blob"):
                launch_scene_job(config, bad_blob, capture_output=True)

    @mock.patch("mc_recorder.scene_job.subprocess.run")
    def test_compaction_rechecks_frozen_verified_stream_after_launch(
        self, run: mock.Mock
    ) -> None:
        run.return_value = SimpleNamespace(returncode=0, stderr="")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config, job = self._prepare(root)
            self._write_success(job)
            verified = launch_scene_job(config, job, capture_output=True)
            (job.stream / "changes.jsonl").write_bytes(b"tampered\n")

            with self.assertRaisesRegex(SceneStoreError, "verified scene stream"):
                compact_scene_stream(
                    job.stream,
                    root / "scene.sqlite3",
                    expected_session_id=job.session_id,
                    expected_player_uuid=job.player_uuid,
                    expected_connection_id=job.connection_id,
                    expected_ticks=job.state_ticks,
                    verified_stream=verified,
                )

    def test_cleanup_removes_only_exact_owned_job_without_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _config, job = self._prepare(root)
            cleanup_scene_job(job)
            self.assertFalse(job.directory.exists())

            unsafe_root = root / "unsafe"
            unsafe_root.mkdir()
            _config, unsafe = self._prepare(unsafe_root)
            target = root / "outside"
            target.write_text("keep", encoding="utf-8")
            (unsafe.directory / "linked").symlink_to(target)
            with self.assertRaisesRegex(RecorderError, "non-owned|symlink"):
                cleanup_scene_job(unsafe)
            self.assertTrue(unsafe.directory.exists())
            self.assertEqual("keep", target.read_text(encoding="utf-8"))

    def test_stale_cleanup_removes_only_marker_owned_jobs_and_crash_temps(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config, episode, source = self._fixture(root)
            with (
                mock.patch(
                    "mc_recorder.scene_job.validate_episode",
                    return_value=SimpleNamespace(
                        valid=True, sealed_epochs=1, session_id="session-a"
                    ),
                ),
                mock.patch(
                    "mc_recorder.scene_job.subject_state_ticks", return_value=(10, 11)
                ),
                mock.patch(
                    "mc_recorder.scene_job.resolve_replay_segments", return_value=[source]
                ),
            ):
                first = prepare_scene_job(
                    config, episode, player_uuid=PLAYER, connection_id=CONNECTION
                )
                second = prepare_scene_job(
                    config, episode, player_uuid=PLAYER, connection_id=CONNECTION
                )
            (first.directory / "mc-recorder-scene-events-crash.sqlite3").write_bytes(b"")
            (first.directory / ".scene-v1.sqlite3.tmp-crash.sqlite3").write_bytes(b"")
            jobs = config.paths.runtime / "scene-jobs"
            foreign = jobs / "foreign"
            foreign.mkdir()
            link = jobs / "linked"
            link.symlink_to(foreign, target_is_directory=True)

            removed = cleanup_stale_scene_jobs(config.paths.runtime)

            self.assertEqual({first.directory, second.directory}, set(removed))
            self.assertFalse(first.directory.exists())
            self.assertFalse(second.directory.exists())
            self.assertTrue(foreign.is_dir())
            self.assertTrue(link.is_symlink())


if __name__ == "__main__":
    unittest.main()
