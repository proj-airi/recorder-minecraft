from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from mc_recorder.config import (
    DEFAULT_RENDER_TASK_QUEUE,
    ENV_DASHBOARD_PASSWORD,
    ENV_DASHBOARD_STATIC_ROOT,
    ENV_DASHBOARD_USERNAME,
    ENV_REPLAY_ROOT,
    ENV_STORAGE_CAPTURE_ROOT,
    ENV_STORAGE_CHECK_INTERVAL,
    ENV_STORAGE_EVICT_OLDEST,
    ENV_STORAGE_QUOTA_BYTES,
    ENV_STORAGE_WARN_PERCENT,
    compose_environment_variables,
    initialize,
    load_config,
    load_runtime_env,
    load_storage_monitor_env,
)
from mc_recorder.errors import RecorderError


class ConfigTest(unittest.TestCase):
    def test_init_keeps_every_default_path_in_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            source = initialize(workspace / "recorder.toml", accept_eula=True)
            config = load_config(source)

            self.assertTrue(config.server.eula)
            self.assertEqual("1.21.8", config.server.minecraft_version)
            self.assertEqual((workspace / "artifacts" / "captures").resolve(), config.paths.captures)
            self.assertTrue(config.paths.server_data.is_dir())
            self.assertTrue(config.paths.runtime.is_dir())
            self.assertEqual(
                (workspace / "mods" / "scene-extractor-mod").resolve(),
                config.mods.scene_extractor_project,
            )
            self.assertEqual("0.0.0.0", config.dashboard.bind)
            self.assertEqual(8765, config.dashboard.port)

    def test_init_does_not_silently_accept_eula_or_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = initialize(Path(temporary) / "recorder.toml")
            self.assertFalse(load_config(source).server.eula)
            with self.assertRaises(RecorderError):
                initialize(source)

    def test_init_force_refuses_a_symlinked_config_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            unrelated = root / "unrelated.toml"
            unrelated.write_text("keep me\n", encoding="utf-8")
            link = root / "recorder.toml"
            link.symlink_to(unrelated)

            with self.assertRaisesRegex(RecorderError, "symlinked configuration"):
                initialize(link, force=True)
            self.assertEqual("keep me\n", unrelated.read_text(encoding="utf-8"))

    def test_v1_rejects_another_minecraft_version(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = initialize(Path(temporary) / "recorder.toml")
            source.write_text(
                source.read_text(encoding="utf-8").replace('minecraft_version = "1.21.8"', 'minecraft_version = "1.21.9"'),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RecorderError, "exactly Minecraft 1.21.8"):
                load_config(source)

    def test_retention_roots_cannot_overlap_world_or_each_other(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = initialize(Path(temporary) / "recorder.toml")
            original = source.read_text(encoding="utf-8")
            source.write_text(
                original.replace(
                    'captures = "artifacts/captures"',
                    'captures = "artifacts/server/captures"',
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RecorderError, "non-nested"):
                load_config(source)

            source.write_text(
                original.replace(
                    'replays = "artifacts/replays"',
                    'replays = "artifacts/captures/replays"',
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RecorderError, "separate and non-nested"):
                load_config(source)

    def test_all_managed_paths_must_be_disjoint(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = initialize(Path(temporary) / "recorder.toml")
            original = source.read_text(encoding="utf-8")

            for old, new in (
                ('exports = "artifacts/exports"', 'exports = "artifacts/captures/exports"'),
                ('runtime = ".mc-recorder"', 'runtime = "artifacts/replays/runtime"'),
                ('server_data = "artifacts/server"', 'server_data = "artifacts"'),
            ):
                with self.subTest(new=new):
                    source.write_text(original.replace(old, new), encoding="utf-8")
                    with self.assertRaisesRegex(RecorderError, "separate and non-nested"):
                        load_config(source)

    def test_dashboard_listener_is_validated(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = initialize(Path(temporary) / "recorder.toml")
            original = source.read_text(encoding="utf-8")
            source.write_text(original.replace("port = 8765", "port = 0"), encoding="utf-8")
            with self.assertRaisesRegex(RecorderError, "dashboard.port"):
                load_config(source)

            source.write_text(original.replace('bind = "0.0.0.0"', 'bind = ""'), encoding="utf-8")
            with self.assertRaisesRegex(RecorderError, "dashboard.bind"):
                load_config(source)

    def test_runtime_environment_is_typed_and_centralized(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dashboard_static = root / "dashboard"
            replay_root = root / "replays"
            env = load_runtime_env(
                {
                    ENV_DASHBOARD_USERNAME: "operator",
                    ENV_DASHBOARD_PASSWORD: "secret",
                    ENV_DASHBOARD_STATIC_ROOT: str(dashboard_static),
                    ENV_REPLAY_ROOT: str(replay_root),
                },
            )

            self.assertEqual("operator", env.dashboard.username)
            self.assertEqual("secret", env.dashboard.password)
            self.assertEqual(dashboard_static.resolve(), env.dashboard.static_root)
            self.assertEqual(replay_root.resolve(), env.replay_root)
            self.assertEqual(DEFAULT_RENDER_TASK_QUEUE, env.render_queue.task_queue)
            self.assertEqual("mc-recorder", env.worker_cache_root.name)

    def test_storage_monitor_environment_uses_recorder_prefixed_names(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            captures = Path(temporary) / "captures"
            env = load_storage_monitor_env(
                {
                    ENV_STORAGE_CAPTURE_ROOT: str(captures),
                    ENV_STORAGE_QUOTA_BYTES: "12345",
                    ENV_STORAGE_WARN_PERCENT: "75",
                    ENV_STORAGE_CHECK_INTERVAL: "30",
                    ENV_STORAGE_EVICT_OLDEST: "false",
                },
            )

            self.assertEqual(captures.resolve(), env.capture_root)
            self.assertEqual(12345, env.quota_bytes)
            self.assertEqual(75, env.warn_percent)
            self.assertEqual(30, env.check_interval_seconds)
            self.assertFalse(env.evict_oldest)

        with self.assertRaisesRegex(RecorderError, ENV_STORAGE_QUOTA_BYTES):
            load_storage_monitor_env({})

    def test_compose_environment_uses_the_same_storage_contract_as_monitor(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            source = initialize(workspace / "recorder.toml", accept_eula=True)
            config = load_config(source)
            values = compose_environment_variables(
                config,
                dashboard_static=workspace / "apps" / "dashboard" / "dist",
                tooling_source=workspace / "tooling" / "src",
                render_task_queue=DEFAULT_RENDER_TASK_QUEUE,
            )

            self.assertEqual(config.paths.captures, values[ENV_STORAGE_CAPTURE_ROOT])
            self.assertEqual(config.storage.quota_bytes, values[ENV_STORAGE_QUOTA_BYTES])
            self.assertEqual(config.storage.warn_percent, values[ENV_STORAGE_WARN_PERCENT])
            self.assertEqual(str(config.storage.evict_oldest).lower(), values[ENV_STORAGE_EVICT_OLDEST])
            self.assertEqual(config.storage.check_interval_seconds, values[ENV_STORAGE_CHECK_INTERVAL])
            self.assertEqual(config.paths.replays, values[ENV_REPLAY_ROOT])
            self.assertNotIn("MC_CAPTURE_QUOTA_BYTES", values)
            self.assertNotIn("MC_STORAGE_CHECK_INTERVAL", values)


if __name__ == "__main__":
    unittest.main()
