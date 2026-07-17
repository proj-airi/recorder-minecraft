from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from mc_recorder.config import initialize, load_config
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
                source.read_text(encoding="utf-8").replace(
                    'minecraft_version = "1.21.8"', 'minecraft_version = "1.21.9"'
                ),
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

if __name__ == "__main__":
    unittest.main()
