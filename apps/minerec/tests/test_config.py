from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from minerec.config import initialize, load_config
from minerec.errors import RecorderError


class ConfigTest(unittest.TestCase):
    def test_init_creates_artifact_and_private_runtime_roots(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            with mock.patch("minerec.config.socket.gethostname", return_value="recorder-a"):
                source = initialize(workspace / "recorder.toml", accept_eula=True)
                config = load_config(source)
            self.assertTrue(config.server.eula)
            self.assertEqual("recorder-a", config.server.name)
            self.assertNotIn('\nname = "', source.read_text(encoding="utf-8"))
            self.assertEqual((workspace / "artifacts").resolve(), config.paths.artifacts)
            self.assertEqual((workspace / ".mc-recorder" / "runtime").resolve(), config.paths.runtime)
            self.assertTrue(config.paths.artifacts.is_dir())
            self.assertTrue(config.paths.runtime.is_dir())

    def test_server_name_can_override_machine_hostname(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = initialize(Path(temporary) / "recorder.toml")
            text = source.read_text(encoding="utf-8").replace(
                "[server]\n",
                '[server]\nname = "friendly-name"\n',
            )
            source.write_text(text, encoding="utf-8")
            with mock.patch("minerec.config.socket.gethostname", return_value="recorder-a"):
                self.assertEqual("friendly-name", load_config(source).server.name)

    def test_empty_machine_hostname_requires_an_explicit_name(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = initialize(Path(temporary) / "recorder.toml")
            with (
                mock.patch("minerec.config.socket.gethostname", return_value=""),
                self.assertRaisesRegex(RecorderError, "configure server.name explicitly"),
            ):
                load_config(source)

    def test_init_persists_one_required_server_instance_id(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = initialize(Path(temporary) / "recorder.toml")
            self.assertEqual(load_config(source).server.instance_id, load_config(source).server.instance_id)
            source.write_text(source.read_text(encoding="utf-8").replace('instance_id = "', 'instance_id = "INVALID-'), encoding="utf-8")
            with self.assertRaisesRegex(RecorderError, "instance_id"):
                load_config(source)

    def test_legacy_paths_are_not_accepted_as_implicit_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = initialize(Path(temporary) / "recorder.toml")
            text = source.read_text(encoding="utf-8")
            text = text.replace('artifacts = "artifacts"', 'captures = "artifacts/captures"')
            source.write_text(text, encoding="utf-8")
            with self.assertRaisesRegex(RecorderError, "unsupported paths"):
                load_config(source)

    def test_managed_roots_must_be_disjoint(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = initialize(Path(temporary) / "recorder.toml")
            text = source.read_text(encoding="utf-8").replace(
                'runtime = ".mc-recorder/runtime"',
                'runtime = "artifacts/runtime"',
            )
            source.write_text(text, encoding="utf-8")
            with self.assertRaisesRegex(RecorderError, "separate and non-nested"):
                load_config(source)

    def test_init_does_not_overwrite_or_follow_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = initialize(root / "recorder.toml")
            with self.assertRaises(RecorderError):
                initialize(source)
            target = root / "other.toml"
            target.write_text("keep\n", encoding="utf-8")
            link = root / "linked.toml"
            link.symlink_to(target)
            with self.assertRaisesRegex(RecorderError, "symlinked"):
                initialize(link, force=True)
            self.assertEqual("keep\n", target.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
