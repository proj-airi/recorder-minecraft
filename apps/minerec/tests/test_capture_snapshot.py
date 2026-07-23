from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from minerec.errors import RecorderError
from minerec.processing.capture import snapshot as snapshot_module
from minerec.processing.capture.snapshot import snapshot_connection

SESSION = "session-snapshot"
PLAYER = "00000000-0000-4000-8000-000000000001"
CONNECTION = "00000000-0000-4000-8000-000000000002"


def _event(record_type: str, tick: int, sequence: int, **values: object) -> dict[str, object]:
    return {
        "schema_version": 1,
        "record_type": record_type,
        "session_id": SESSION,
        "epoch_index": 1,
        "server_tick": tick,
        "sequence": sequence,
        "recorded_at_ns": sequence,
        **values,
    }


def _connection_records() -> list[dict[str, object]]:
    return [
        _event("tick_start", 10, 1),
        _event(
            "player_join",
            10,
            2,
            player_uuid=PLAYER,
            connection_id=CONNECTION,
        ),
        _event(
            "player_state",
            10,
            3,
            player_uuid=PLAYER,
            connection_id=CONNECTION,
            state_barrier_apply_sequence=0,
            dimension="minecraft:overworld",
            position={"x": 1.0, "y": 64.0, "z": 2.0},
            inventory=[],
        ),
        _event(
            "control_state",
            10,
            4,
            player_uuid=PLAYER,
            connection_id=CONNECTION,
            forward=False,
        ),
        _event("tick_end", 10, 5),
        _event("tick_start", 11, 6),
        _event(
            "player_state",
            11,
            7,
            player_uuid=PLAYER,
            connection_id=CONNECTION,
            state_barrier_apply_sequence=0,
            dimension="minecraft:overworld",
            position={"x": 1.2, "y": 64.0, "z": 2.0},
            inventory=[],
        ),
        _event(
            "control_state",
            11,
            8,
            player_uuid=PLAYER,
            connection_id=CONNECTION,
            forward=True,
        ),
        _event(
            "player_leave",
            11,
            9,
            player_uuid=PLAYER,
            connection_id=CONNECTION,
        ),
        _event("tick_end", 11, 10),
    ]


def _write_episode(root: Path, records: list[dict[str, object]], *, tail: bytes = b"") -> tuple[Path, bytes]:
    episode = root / SESSION
    epoch = episode / "epochs" / "epoch-000001"
    epoch.mkdir(parents=True)
    (episode / "manifest.json").write_text(
        json.dumps({"schema_version": 1, "session_id": SESSION}),
        encoding="utf-8",
    )
    complete = b"".join(json.dumps(record, sort_keys=True, separators=(",", ":")).encode() + b"\n" for record in records)
    (epoch / "events.jsonl.inprogress").write_bytes(complete + tail)
    return episode, complete


class CaptureSnapshotTest(unittest.TestCase):
    def test_materializes_complete_active_prefix_and_ignores_partial_tail(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            episode, complete = _write_episode(
                root / "captures",
                _connection_records(),
                tail=b'{"schema_version":1',
            )

            with snapshot_connection(
                episode,
                root / "runtime",
                player_uuid=PLAYER,
                connection_id=CONNECTION,
            ) as snapshot:
                copied = snapshot.episode / "epochs" / "epoch-000001" / "events.jsonl"
                self.assertEqual(complete, copied.read_bytes())
                segment = snapshot.provenance["segments"][0]
                self.assertEqual("append_prefix_v1", snapshot.provenance["format"])
                self.assertEqual("active_prefix", segment["kind"])
                self.assertEqual(len(complete), segment["bytes"])
                self.assertEqual(
                    hashlib.sha256(complete).hexdigest(),
                    segment["sha256"],
                )
                self.assertEqual(10, segment["record_count"])
                self.assertEqual(CONNECTION, snapshot.connection_id)
                self.assertEqual(PLAYER, snapshot.player_uuid)

            self.assertFalse(snapshot.episode.exists())

    def test_rejects_malformed_complete_line(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            episode, _ = _write_episode(
                root / "captures",
                _connection_records(),
                tail=b"not-json\n",
            )

            with self.assertRaisesRegex(RecorderError, "invalid JSON"):
                with snapshot_connection(
                    episode,
                    root / "runtime",
                    player_uuid=PLAYER,
                    connection_id=CONNECTION,
                ):
                    pass

    def test_requires_join_and_leave_for_selected_connection(self) -> None:
        cases = {
            "join": [record for record in _connection_records() if record["record_type"] != "player_join"],
            "leave": [record for record in _connection_records() if record["record_type"] != "player_leave"],
        }
        for missing, records in cases.items():
            with self.subTest(missing=missing), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                episode, _ = _write_episode(root / "captures", records)
                with self.assertRaisesRegex(RecorderError, missing):
                    with snapshot_connection(
                        episode,
                        root / "runtime",
                        player_uuid=PLAYER,
                        connection_id=CONNECTION,
                    ):
                        pass

    def test_rejects_non_monotonic_sequence_and_tick(self) -> None:
        records = _connection_records()
        for field, value, message in (
            ("sequence", 1, "sequence"),
            ("server_tick", 9, "server tick"),
        ):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                invalid = [dict(record) for record in records]
                invalid[5][field] = value
                episode, _ = _write_episode(root / "captures", invalid)
                with self.assertRaisesRegex(RecorderError, message):
                    with snapshot_connection(
                        episode,
                        root / "runtime",
                        player_uuid=PLAYER,
                        connection_id=CONNECTION,
                    ):
                        pass

    def test_rejects_symlinked_active_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            episode, _ = _write_episode(root / "captures", _connection_records())
            active = episode / "epochs" / "epoch-000001" / "events.jsonl.inprogress"
            target = root / "outside.jsonl"
            target.write_bytes(active.read_bytes())
            active.unlink()
            active.symlink_to(target)

            with self.assertRaisesRegex(RecorderError, "symlink"):
                with snapshot_connection(
                    episode,
                    root / "runtime",
                    player_uuid=PLAYER,
                    connection_id=CONNECTION,
                ):
                    pass

    def test_allows_bytes_appended_after_observed_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            episode, complete = _write_episode(root / "captures", _connection_records())
            active = episode / "epochs" / "epoch-000001" / "events.jsonl.inprogress"
            original_read = snapshot_module._read_exact
            active_size = active.stat().st_size
            appended = False

            def append_after_read(descriptor: int, size: int) -> bytes:
                nonlocal appended
                data = original_read(descriptor, size)
                if size == active_size and not appended:
                    appended = True
                    with active.open("ab") as handle:
                        handle.write(
                            json.dumps(
                                _event("tick_start", 12, 11),
                                separators=(",", ":"),
                            ).encode()
                            + b"\n"
                        )
                return data

            with mock.patch.object(
                snapshot_module,
                "_read_exact",
                side_effect=append_after_read,
            ):
                with snapshot_connection(
                    episode,
                    root / "runtime",
                    player_uuid=PLAYER,
                    connection_id=CONNECTION,
                ) as snapshot:
                    copied = snapshot.episode / "epochs" / "epoch-000001" / "events.jsonl"
                    self.assertEqual(complete, copied.read_bytes())

    def test_rejects_prefix_rewrite_replacement_and_truncation(self) -> None:
        mutations = {
            "prefix changed": lambda active: active.write_bytes(b" " + active.read_bytes()[1:]),
            "replaced": lambda active: (
                active.rename(active.with_suffix(".old")),
                active.write_bytes(active.with_suffix(".old").read_bytes()),
            ),
            "truncated": lambda active: active.write_bytes(active.read_bytes()[:-10]),
        }
        for expected, mutate in mutations.items():
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                episode, _ = _write_episode(root / "captures", _connection_records())
                active = episode / "epochs" / "epoch-000001" / "events.jsonl.inprogress"
                original_read = snapshot_module._read_exact
                active_size = active.stat().st_size
                mutated = False

                def mutate_after_read(descriptor: int, size: int) -> bytes:
                    nonlocal mutated
                    data = original_read(descriptor, size)
                    if size == active_size and not mutated:
                        mutated = True
                        mutate(active)
                    return data

                with (
                    mock.patch.object(
                        snapshot_module,
                        "_read_exact",
                        side_effect=mutate_after_read,
                    ),
                    self.assertRaisesRegex(RecorderError, expected),
                ):
                    with snapshot_connection(
                        episode,
                        root / "runtime",
                        player_uuid=PLAYER,
                        connection_id=CONNECTION,
                    ):
                        pass


if __name__ == "__main__":
    unittest.main()
