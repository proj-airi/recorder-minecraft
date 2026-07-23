from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Callable, cast
from unittest import mock

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from mc_recorder.errors import RecorderError
from mc_recorder.processing.dataset.viewer import DatasetValidationError, DatasetViewer, opaque_dataset_id
from mc_recorder.processing.render.hud import (
    create_structured_hud_sidecar,
    validate_hud_sidecar_envelope,
)

PLAYER = "00000000-0000-4000-8000-000000000001"
CONNECTION = "00000000-0000-4000-8000-000000000002"
SESSION = "session-a"


def _sample(tick: int, *, with_snbt: bool = True) -> dict[str, object]:
    stack: dict[str, object] = {
        "slot": 2,
        "item": "minecraft:dandelion",
        "count": 1,
        "damage": 0,
        "max_damage": 0,
    }
    if with_snbt:
        stack["stack_snbt"] = '{id:"minecraft:dandelion",count:1}'
    return {
        "schema_version": 2,
        "sample_key": {
            "session_id": SESSION,
            "server_tick": tick,
            "player_uuid": PLAYER,
            "connection_id": CONNECTION,
        },
        "session_id": SESSION,
        "server_tick": tick,
        "player_uuid": PLAYER,
        "connection_id": CONNECTION,
        "state": {
            "player_uuid": PLAYER,
            "connection_id": CONNECTION,
            "health": 19.0,
            "max_health": 20.0,
            "absorption": 2.0,
            "air": 299,
            "max_air": 300,
            "food_level": 18,
            "saturation": 3.5,
            "experience_progress": 0.25,
            "experience_level": 4,
            "total_experience": 44,
            "selected_slot": 2,
            "inventory": [
                {
                    "slot": 0,
                    "item": "minecraft:wooden_pickaxe",
                    "count": 1,
                    "damage": tick,
                    "max_damage": 59,
                    "stack_snbt": (f'{{id:"minecraft:wooden_pickaxe",count:1,components:{{"minecraft:damage":{tick}}}}}'),
                },
                stack,
            ],
        },
    }


def _state(sample: dict[str, object]) -> dict[str, object]:
    state = dict(cast(dict[str, object], sample["state"]))
    state.update(
        {
            "schema_version": 2,
            "session_id": sample["session_id"],
            "server_tick": sample["server_tick"],
            "player_uuid": sample["player_uuid"],
            "connection_id": sample["connection_id"],
        }
    )
    return state


def _dataset(
    exports: Path,
    samples: list[dict[str, object]],
    *,
    states: list[dict[str, object]] | None = None,
) -> tuple[Path, str]:
    directory = exports / "session-a-player-connection.dataset"
    directory.mkdir()
    streams = {
        "samples.jsonl": b"".join(json.dumps(row, sort_keys=True, separators=(",", ":")).encode() + b"\n" for row in samples),
        "states.jsonl": b"".join(json.dumps(row, sort_keys=True, separators=(",", ":")).encode() + b"\n" for row in (states if states is not None else [_state(sample) for sample in samples])),
        "actions.jsonl": b"",
        "modalities.jsonl": b"",
    }
    for name, data in streams.items():
        (directory / name).write_bytes(data)
    manifest = {
        "schema_version": 2,
        "owner": "mc-recorder",
        "format": "mc-recorder-jsonl-v2",
        "session_id": SESSION,
        "selection": {
            "players": [PLAYER],
            "connections": [CONNECTION],
            "from_tick": 10,
            "to_tick": 12,
            "scene_attachment": None,
        },
        "files": {
            name: {
                "size_bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
            for name, data in streams.items()
        },
    }
    (directory / "manifest.json").write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
    return directory, opaque_dataset_id(exports, directory.name)


class StructuredHudSidecarTest(unittest.TestCase):
    def test_generator_bounds_match_the_java_sidecar_reader(self) -> None:
        cases: list[tuple[str, Callable[[dict[str, Any]], None]]] = [
            (
                "inventory slot",
                lambda sample: sample["state"]["inventory"][0].__setitem__("slot", 43),
            ),
            (
                "inventory count",
                lambda sample: sample["state"]["inventory"][0].__setitem__("count", 1000),
            ),
            (
                "saturation",
                lambda sample: sample["state"].__setitem__("saturation", 20.01),
            ),
            (
                "stack_snbt",
                lambda sample: sample["state"]["inventory"][0].__setitem__("stack_snbt", "x" * (256 * 1024 + 1)),
            ),
        ]
        for message, mutate in cases:
            with self.subTest(message=message), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                exports = root / "exports"
                runtime = root / "runtime"
                exports.mkdir()
                runtime.mkdir()
                samples = [_sample(10), _sample(11), _sample(12)]
                mutate(samples[0])  # type: ignore[arg-type]
                _directory, dataset_id = _dataset(exports, samples)
                with self.assertRaisesRegex(RecorderError, message):
                    create_structured_hud_sidecar(
                        DatasetViewer(exports, runtime),
                        dataset_id,
                        root / "hud.jsonl",
                        session_id=SESSION,
                        player_uuid=PLAYER,
                        connection_id=CONNECTION,
                        first_tick=10,
                        last_tick=12,
                        selection_first_tick=10,
                        selection_last_tick=12,
                    )

    def test_sidecar_is_exact_dataset_bound_and_preserves_all_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            exports = root / "exports"
            runtime = root / "runtime"
            exports.mkdir()
            runtime.mkdir()
            samples = [_sample(10), _sample(11), _sample(12)]
            for sample in samples:
                state = cast(dict[str, object], sample["state"])
                inventory = cast(list[object], state["inventory"])
                stack = cast(dict[str, object], inventory[1])
                stack["slot"] = 42
            _directory, dataset_id = _dataset(exports, samples)
            result = create_structured_hud_sidecar(
                DatasetViewer(exports, runtime),
                dataset_id,
                root / "hud.jsonl",
                session_id=SESSION,
                player_uuid=PLAYER,
                connection_id=CONNECTION,
                first_tick=10,
                last_tick=12,
                selection_first_tick=10,
                selection_last_tick=12,
            )

            self.assertEqual(3, result.record_count)
            self.assertEqual((10, 12), (result.first_tick, result.last_tick))
            self.assertEqual(0o600, result.path.stat().st_mode & 0o777)
            self.assertEqual(result.sha256, hashlib.sha256(result.path.read_bytes()).hexdigest())
            rows = [json.loads(line) for line in result.path.read_text().splitlines()]
            self.assertEqual([10, 11, 12], [row["server_tick"] for row in rows])
            self.assertEqual([0, 42], [item["slot"] for item in rows[0]["state"]["inventory"]])
            self.assertEqual(18, rows[0]["state"]["food_level"])
            self.assertIn("stack_snbt", rows[0]["state"]["inventory"][1])
            self.assertEqual(result.envelope(), validate_hud_sidecar_envelope(result.envelope()))
            self.assertNotIn("path", result.envelope())

    def test_sidecar_includes_terminal_state_without_a_transition_sample(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            exports = root / "exports"
            runtime = root / "runtime"
            exports.mkdir()
            runtime.mkdir()
            terminal = _sample(12)
            _directory, dataset_id = _dataset(
                exports,
                [_sample(10), _sample(11)],
                states=[_state(_sample(10)), _state(_sample(11)), _state(terminal)],
            )

            result = create_structured_hud_sidecar(
                DatasetViewer(exports, runtime),
                dataset_id,
                root / "hud.jsonl",
                session_id=SESSION,
                player_uuid=PLAYER,
                connection_id=CONNECTION,
                first_tick=10,
                last_tick=12,
                selection_first_tick=10,
                selection_last_tick=12,
            )

            self.assertEqual((3, 10, 12), (result.record_count, result.first_tick, result.last_tick))
            rows = [json.loads(line) for line in result.path.read_text().splitlines()]
            self.assertEqual([10, 11, 12], [row["server_tick"] for row in rows])

    def test_sidecar_fails_closed_on_missing_tick_or_authoritative_stack(self) -> None:
        for samples, message in (
            ([_sample(10), _sample(12)], "missing tick at 11"),
            ([_sample(10, with_snbt=False), _sample(11), _sample(12)], "stack_snbt"),
        ):
            with self.subTest(message=message), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                exports = root / "exports"
                runtime = root / "runtime"
                exports.mkdir()
                runtime.mkdir()
                _directory, dataset_id = _dataset(exports, samples)
                with self.assertRaisesRegex(RecorderError, message):
                    create_structured_hud_sidecar(
                        DatasetViewer(exports, runtime),
                        dataset_id,
                        root / "hud.jsonl",
                        session_id=SESSION,
                        player_uuid=PLAYER,
                        connection_id=CONNECTION,
                        first_tick=10,
                        last_tick=12,
                        selection_first_tick=10,
                        selection_last_tick=12,
                    )

    def test_sidecar_rechecks_verified_state_identity_and_hash_during_stream(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            exports = root / "exports"
            runtime = root / "runtime"
            exports.mkdir()
            runtime.mkdir()
            directory, dataset_id = _dataset(exports, [_sample(10), _sample(11), _sample(12)])
            viewer = DatasetViewer(exports, runtime)
            verified = viewer._dataset_by_id(dataset_id)
            with (directory / "states.jsonl").open("ab") as handle:
                handle.write(b"{}\n")
            with (
                mock.patch.object(viewer, "_dataset_by_id", return_value=verified),
                self.assertRaisesRegex(DatasetValidationError, "changed after"),
            ):
                create_structured_hud_sidecar(
                    viewer,
                    dataset_id,
                    root / "hud.jsonl",
                    session_id=SESSION,
                    player_uuid=PLAYER,
                    connection_id=CONNECTION,
                    first_tick=10,
                    last_tick=12,
                    selection_first_tick=10,
                    selection_last_tick=12,
                )


if __name__ == "__main__":
    unittest.main()
