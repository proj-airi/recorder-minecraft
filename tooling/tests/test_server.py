from __future__ import annotations

import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from mc_recorder.config import initialize, load_config
from mc_recorder.errors import RecorderError
from unittest.mock import patch

from mc_recorder.server import (
    compose_status,
    provision_local_mod,
    show_logs,
    show_status,
    start_server,
    stop_server,
    write_compose_env,
    write_mod_configs,
)


class ServerProvisioningTest(unittest.TestCase):
    @staticmethod
    def _prepared_config(root: Path):
        source = root / "recorder.toml"
        if not source.exists():
            source = initialize(source, accept_eula=True)
        config = load_config(source)
        config.paths.runtime.mkdir(parents=True, exist_ok=True)
        (config.paths.runtime / "compose.env").write_text("prepared\n", encoding="utf-8")
        config.paths.compose_file.parent.mkdir(parents=True, exist_ok=True)
        config.paths.compose_file.write_text("services: {}\n", encoding="utf-8")
        return config

    def test_stages_local_fabric_jar_and_all_player_replay_config(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = initialize(root / "recorder.toml", accept_eula=True)
            (root / "tooling" / "src").mkdir(parents=True)
            project = root / "recorder-mod" / "build" / "libs"
            project.mkdir(parents=True)
            jar = project / "mc-recorder-capture-0.1.0.jar"
            with zipfile.ZipFile(jar, "w") as archive:
                archive.writestr(
                    "fabric.mod.json",
                    json.dumps({"schemaVersion": 1, "id": "mc-recorder-capture", "version": "0.1.0"}),
                )

            config = load_config(source)
            staged = provision_local_mod(config, build=False)
            self.assertTrue(staged.is_file())
            self.assertEqual(jar.read_bytes(), staged.read_bytes())

            write_mod_configs(config)
            replay_config = json.loads(
                (config.paths.runtime / "config" / "server-replay" / "config.json").read_text()
            )
            self.assertTrue(replay_config["automatically_record"])
            self.assertEqual({"type": "all"}, replay_config["player_predicate"])
            self.assertEqual("/replays/players", replay_config["player_recording_path"])
            self.assertTrue(replay_config["record_hotbar"])
            capture_config = json.loads(
                (config.paths.runtime / "config" / "mc-recorder.json").read_text()
            )
            self.assertEqual("/captures", capture_config["capture_root"])
            self.assertEqual("/control", capture_config["control_root"])
            self.assertEqual(6000, capture_config["epoch_ticks"])
            self.assertTrue(capture_config["record_all_players"])

            env_path = write_compose_env(config)
            env = env_path.read_text()
            self.assertIn('MC_VERSION="1.21.8"', env)
            self.assertIn('MC_MODRINTH_PROJECTS="server-replay:TbWIikrT"', env)
            self.assertIn(str(root / "artifacts" / "captures"), env)
            self.assertIn(str(root / ".mc-recorder" / "control"), env)

    def test_empty_server_replay_project_uses_only_local_mods(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = initialize(root / "recorder.toml", accept_eula=True)
            source.write_text(
                source.read_text(encoding="utf-8").replace(
                    'server_replay_project = "server-replay:TbWIikrT"',
                    'server_replay_project = ""',
                ),
                encoding="utf-8",
            )
            (root / "tooling" / "src").mkdir(parents=True)

            env = write_compose_env(load_config(source)).read_text(encoding="utf-8")
            self.assertIn('MC_MODRINTH_PROJECTS=""', env)

    def test_compose_status_maps_structured_service_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = self._prepared_config(root)

            responses = [
                type("Result", (), {"returncode": 0, "stdout": "Docker Compose version", "stderr": ""})(),
                type(
                    "Result",
                    (),
                    {
                        "returncode": 0,
                        "stdout": json.dumps(
                            [{"Service": "minecraft", "State": "running", "Health": "healthy"}]
                        ),
                        "stderr": "",
                    },
                )(),
            ]
            with patch("mc_recorder.server._run", side_effect=responses) as run:
                self.assertEqual("running", compose_status(config)["state"])
            self.assertEqual(
                str(config.paths.runtime / "control"),
                run.call_args_list[1].kwargs["env"]["MC_CONTROL_DIR"],
            )

    def test_compose_status_covers_dashboard_lifecycle_states(self) -> None:
        cases = (
            ([], "stopped"),
            ([{"Service": "minecraft", "State": "created"}], "starting"),
            ([{"Service": "minecraft", "State": "running", "Health": "starting"}], "starting"),
            ([{"Service": "minecraft", "State": "running", "Health": "healthy"}], "running"),
            ([{"Service": "minecraft", "State": "running", "Health": "unhealthy"}], "unhealthy"),
            ([{"Service": "minecraft", "State": "stopping"}], "stopping"),
            ([{"Service": "minecraft", "State": "exited", "ExitCode": 1}], "unhealthy"),
        )
        with tempfile.TemporaryDirectory() as temporary:
            config = self._prepared_config(Path(temporary))
            for services, expected in cases:
                with self.subTest(expected=expected, services=services):
                    responses = [
                        type("Result", (), {"returncode": 0, "stdout": "Docker Compose", "stderr": ""})(),
                        type(
                            "Result",
                            (),
                            {"returncode": 0, "stdout": json.dumps(services), "stderr": ""},
                        )(),
                    ]
                    with patch("mc_recorder.server._run", side_effect=responses):
                        self.assertEqual(expected, compose_status(config)["state"])

    def test_compose_status_reports_unprepared_and_docker_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = load_config(initialize(root / "recorder.toml", accept_eula=True))
            self.assertEqual("unprepared", compose_status(config)["state"])

            config = self._prepared_config(root)
            unavailable = type(
                "Result",
                (),
                {
                    "returncode": 1,
                    "stdout": "",
                    "stderr": "Docker daemon unavailable" + "x" * 4096,
                },
            )()
            with patch("mc_recorder.server._run", return_value=unavailable):
                status = compose_status(config)
            self.assertEqual("docker_unavailable", status["state"])
            self.assertIn("Docker daemon unavailable", status["message"])
            self.assertEqual(2048, len(status["message"]))

    def test_start_rejects_unaccepted_eula_before_provisioning(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = load_config(initialize(Path(temporary) / "recorder.toml"))
            with self.assertRaisesRegex(RecorderError, "EULA has not been accepted"):
                start_server(config)

    def test_dashboard_stop_captures_actionable_compose_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = self._prepared_config(Path(temporary))
            failed = type(
                "Result",
                (),
                {
                    "returncode": 1,
                    "stdout": "",
                    "stderr": "Docker daemon refused the stop request",
                },
            )()
            with (
                patch("mc_recorder.server.write_compose_env"),
                patch("mc_recorder.server.verify_docker"),
                patch("mc_recorder.server._run", return_value=failed) as run,
            ):
                with self.assertRaisesRegex(RecorderError, "daemon refused"):
                    stop_server(config, capture_output=True)

            self.assertTrue(run.call_args.kwargs["capture"])

    def test_read_only_compose_commands_reconcile_legacy_control_variable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = initialize(root / "recorder.toml", accept_eula=True)
            config = load_config(source)
            config.paths.runtime.mkdir(parents=True, exist_ok=True)
            (config.paths.runtime / "compose.env").write_text("prepared\n", encoding="utf-8")
            config.paths.compose_file.parent.mkdir(parents=True, exist_ok=True)
            config.paths.compose_file.write_text("services: {}\n", encoding="utf-8")

            successful = type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()
            with patch("mc_recorder.server._run", return_value=successful) as run:
                self.assertEqual(0, show_status(config))
                self.assertEqual(0, show_logs(config, follow=False, tail=5, service="minecraft"))

            expected = str(config.paths.runtime / "control")
            self.assertEqual(expected, run.call_args_list[1].kwargs["env"]["MC_CONTROL_DIR"])
            self.assertEqual(expected, run.call_args_list[3].kwargs["env"]["MC_CONTROL_DIR"])


if __name__ == "__main__":
    unittest.main()
