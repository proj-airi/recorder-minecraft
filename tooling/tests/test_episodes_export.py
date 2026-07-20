from __future__ import annotations

import base64
import gzip
import hashlib
import json
import struct
import sys
import tempfile
import unittest
import zipfile
import zlib
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from mc_recorder.episodes import inspect_episode, validate_episode
from mc_recorder.errors import RecorderError
from mc_recorder.exporter import _safe_replace_directory, export_episode
from mc_recorder.cli import _parser
from mc_recorder.render_job import prepare_render_job


PLAYER = "00000000-0000-4000-8000-000000000001"
CONNECTION = "00000000-0000-4000-8000-000000000002"
PEER = "00000000-0000-4000-8000-000000000003"
PEER_CONNECTION = "00000000-0000-4000-8000-000000000004"
UNKNOWN_PLAYER = "00000000-0000-4000-8000-000000000005"
RECONNECTED_CONNECTION = "00000000-0000-4000-8000-000000000006"
RENDER_WIDTH = 64
RENDER_HEIGHT = 64


def _event(record_type: str, tick: int, sequence: int, **values: object) -> dict[str, object]:
    return {
        "schema_version": 1,
        "record_type": record_type,
        "session_id": "session-a",
        "epoch_index": 0,
        "server_tick": tick,
        "sequence": sequence,
        "recorded_at_ns": sequence * 100,
        **values,
    }


def _records(*, current_connection: str = CONNECTION, invalid_barrier: bool = False) -> list[dict[str, object]]:
    second_state_barrier = 1 if invalid_barrier else 2
    return [
        _event("tick_start", 10, 1, apply_sequence_at_barrier=0),
        _event(
            "player_state",
            10,
            2,
            player_uuid=PLAYER,
            connection_id=CONNECTION,
            state_barrier_apply_sequence=0,
            dimension="minecraft:overworld",
            position={"x": 1.0, "y": 64.0, "z": 2.0},
            health=20.0,
            inventory=[],
        ),
        _event(
            "control_state",
            10,
            3,
            player_uuid=PLAYER,
            connection_id=CONNECTION,
            forward=False,
            jump=False,
            camera_yaw=88.0,
            camera_pitch=0.0,
        ),
        _event(
            "player_state",
            10,
            4,
            player_uuid=PEER,
            connection_id=PEER_CONNECTION,
            state_barrier_apply_sequence=0,
            dimension="minecraft:overworld",
            position={"x": 2.0, "y": 64.0, "z": 2.0},
            health=20.0,
            inventory=[],
        ),
        _event(
            "control_state",
            10,
            5,
            player_uuid=PEER,
            connection_id=PEER_CONNECTION,
            forward=False,
            jump=False,
            camera_yaw=270.0,
            camera_pitch=0.0,
        ),
        _event("tick_end", 10, 6, apply_sequence_at_barrier=0, player_count=2),
        _event("tick_start", 11, 7, apply_sequence_at_barrier=0),
        _event(
            "packet_apply",
            11,
            8,
            player_uuid=PLAYER,
            connection_id=current_connection,
            apply_sequence=1,
            packet={
                "action_kind": "other",
                "packet_type": "serverbound/minecraft:player_input",
                "input": {"forward": True},
            },
        ),
        _event(
            "packet_apply",
            11,
            9,
            player_uuid=PEER,
            connection_id=PEER_CONNECTION,
            apply_sequence=2,
            packet={
                "action_kind": "interact",
                "interaction": "attack",
                "target": {"uuid": PLAYER, "is_player": True},
            },
        ),
        _event(
            "player_state",
            11,
            10,
            player_uuid=PLAYER,
            connection_id=current_connection,
            state_barrier_apply_sequence=second_state_barrier,
            dimension="minecraft:overworld",
            position={"x": 1.1, "y": 64.0, "z": 2.0},
            health=19.0,
            inventory=[],
        ),
        _event(
            "control_state",
            11,
            11,
            player_uuid=PLAYER,
            connection_id=current_connection,
            forward=True,
            jump=False,
            camera_yaw=90.0,
            camera_pitch=0.0,
        ),
        _event(
            "player_state",
            11,
            12,
            player_uuid=PEER,
            connection_id=PEER_CONNECTION,
            state_barrier_apply_sequence=2,
            dimension="minecraft:overworld",
            position={"x": 2.0, "y": 64.0, "z": 2.0},
            health=20.0,
            inventory=[],
        ),
        _event(
            "control_state",
            11,
            13,
            player_uuid=PEER,
            connection_id=PEER_CONNECTION,
            forward=False,
            jump=False,
            camera_yaw=270.0,
            camera_pitch=0.0,
        ),
        _event("tick_end", 11, 14, apply_sequence_at_barrier=2, player_count=2),
    ]


def _seal_epoch(epoch: Path, records: list[dict[str, object]], *, active: bool = False) -> None:
    data = "".join(json.dumps(record, sort_keys=True) + "\n" for record in records)
    ticks = [record["server_tick"] for record in records]
    name = "events.jsonl.inprogress" if active else "events.jsonl"
    (epoch / name).write_text(data, encoding="utf-8")
    manifest: dict[str, object] = {
        "schema_version": 1,
        "session_id": "session-a",
        "epoch_index": 0,
        "sealed": not active,
        "record_count": len(records),
        "events_bytes": len(data.encode("utf-8")),
        "events_sha256": hashlib.sha256(data.encode("utf-8")).hexdigest(),
        "first_server_tick": min(ticks),
        "last_server_tick": max(ticks),
    }
    (epoch / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def _episode(
    root: Path,
    *,
    active: bool = False,
    current_connection: str = CONNECTION,
    invalid_barrier: bool = False,
) -> Path:
    episode = root / "session-a"
    epoch = episode / "epochs" / "epoch-000000"
    epoch.mkdir(parents=True)
    (episode / "manifest.json").write_text(
        json.dumps({"session_id": "session-a", "source_format": "mc-recorder-jsonl-v1"}),
        encoding="utf-8",
    )
    _seal_epoch(
        epoch,
        _records(current_connection=current_connection, invalid_barrier=invalid_barrier),
        active=active,
    )
    return episode


def _reconnected_episode(root: Path) -> Path:
    episode = _episode(root)
    records = _records()
    sequence = len(records) + 1
    for tick in (12, 13):
        records.extend(
            [
                _event("tick_start", tick, sequence, apply_sequence_at_barrier=2),
                _event(
                    "player_state",
                    tick,
                    sequence + 1,
                    player_uuid=PLAYER,
                    connection_id=RECONNECTED_CONNECTION,
                    state_barrier_apply_sequence=2,
                    dimension="minecraft:overworld",
                    position={"x": 1.2, "y": 64.0, "z": 2.0},
                    health=19.0,
                    inventory=[],
                ),
                _event(
                    "control_state",
                    tick,
                    sequence + 2,
                    player_uuid=PLAYER,
                    connection_id=RECONNECTED_CONNECTION,
                    forward=False,
                    jump=False,
                    camera_yaw=90.0,
                    camera_pitch=0.0,
                ),
                _event(
                    "player_state",
                    tick,
                    sequence + 3,
                    player_uuid=PEER,
                    connection_id=PEER_CONNECTION,
                    state_barrier_apply_sequence=2,
                    dimension="minecraft:overworld",
                    position={"x": 2.0, "y": 64.0, "z": 2.0},
                    health=20.0,
                    inventory=[],
                ),
                _event(
                    "control_state",
                    tick,
                    sequence + 4,
                    player_uuid=PEER,
                    connection_id=PEER_CONNECTION,
                    forward=False,
                    jump=False,
                    camera_yaw=270.0,
                    camera_pitch=0.0,
                ),
                _event("tick_end", tick, sequence + 5, apply_sequence_at_barrier=2, player_count=2),
            ]
        )
        sequence += 6
    _seal_epoch(episode / "epochs" / "epoch-000000", records)
    return episode


def _png_chunk(chunk_type: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + chunk_type
        + payload
        + struct.pack(">I", zlib.crc32(chunk_type + payload) & 0xFFFFFFFF)
    )


def _png(width: int, height: int) -> bytes:
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    rows = b"".join(b"\x00" + b"\x00" * (width * 4) for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", zlib.compress(rows))
        + _png_chunk(b"IEND", b"")
    )


def _voxel_snapshot(tick: int) -> dict[str, object]:
    total = 27
    coverage = ((1 << 26) - 1).to_bytes(4, "little")
    return {
        "schema_version": 1,
        "format": "mc-recorder-voxel-palette-v1",
        "session_id": "session-a",
        "connection_id": CONNECTION,
        "player_uuid": PLAYER,
        "server_tick": tick,
        "replay_tick": tick - 10,
        "dimension": "minecraft:overworld",
        "center": {"x": 1, "y": 64, "z": 2},
        "origin": {"x": 0, "y": 63, "z": 1},
        "shape": {"x": 3, "y": 3, "z": 3},
        "linear_order": "x_fastest_then_z_then_y",
        "index_dtype": "uint16",
        "index_byte_order": "little_endian",
        "indices_base64": base64.b64encode(b"\x00\x00" * total).decode("ascii"),
        "coverage_bitset_base64": base64.b64encode(coverage).decode("ascii"),
        "coverage_bit_order": "lsb0",
        "covered_cells": 26,
        "total_cells": total,
        "coverage_complete": False,
        "palette": ["minecraft:air"],
        "block_entities_included": False,
        "block_entities_note": "not materialized in v1",
    }


def _write_voxel(path: Path, snapshot: dict[str, object]) -> None:
    path.write_bytes(gzip.compress(json.dumps(snapshot).encode("utf-8"), mtime=0))


def _render_artifacts(root: Path, *, status: str = "complete") -> Path:
    job = root / "render-job"
    frames = job / "frames"
    voxel_dir = frames / "voxels"
    voxel_dir.mkdir(parents=True)
    frame_rows: list[dict[str, object]] = []
    voxel_rows: list[dict[str, object]] = []
    for frame_number, tick in enumerate((10, 11), 1):
        image_name = f"frame_{frame_number:06d}.png"
        (frames / image_name).write_bytes(_png(RENDER_WIDTH, RENDER_HEIGHT))
        frame_rows.append(
            {
                "frame": frame_number,
                "server_tick": tick,
                "replay_tick": tick - 10,
                "partial_tick": 0.0,
                "session_id": "session-a",
                "connection_id": CONNECTION,
                "player_uuid": PLAYER,
                "path": image_name,
            }
        )
        voxel_name = f"voxel_{tick:012d}.json.gz"
        _write_voxel(voxel_dir / voxel_name, _voxel_snapshot(tick))
        voxel_rows.append(
            {
                "schema_version": 1,
                "session_id": "session-a",
                "connection_id": CONNECTION,
                "player_uuid": PLAYER,
                "server_tick": tick,
                "replay_tick": tick - 10,
                "reference": f"voxels/{voxel_name}",
                "origin": {"x": 0, "y": 63, "z": 1},
                "shape": {"x": 3, "y": 3, "z": 3},
                "covered_cells": 26,
                "total_cells": 27,
                "coverage_complete": False,
            }
        )
    (frames / "frames.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in frame_rows), encoding="utf-8"
    )
    (frames / "voxels.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in voxel_rows), encoding="utf-8"
    )
    (job / "result.json").write_text(
        json.dumps(
            {
                "status": status,
                "replay": str((root / "recording.zip").resolve()),
                "replay_sha256": "ab" * 32,
                "replay_bytes": 123,
                "output": str(frames.resolve()),
                "session_id": "session-a",
                "connection_id": CONNECTION,
                "player_uuid": PLAYER,
                "global_start_tick": 10,
                "global_end_tick": 11,
                "replay_start_tick": 0,
                "replay_end_tick": 1,
                "global_tick_offset": 10,
                "fps": 20,
                "width": RENDER_WIDTH,
                "height": RENDER_HEIGHT,
                "voxel_snapshots": 2,
                "voxel_index": str((frames / "voxels.jsonl").resolve()),
                "voxel_horizontal_radius": 1,
                "voxel_vertical_radius": 1,
            }
        ),
        encoding="utf-8",
    )
    return job


class EpisodeExportTest(unittest.TestCase):
    def test_exports_canonical_connection_aware_multiplayer_transition(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            episode = _episode(root)
            validation = validate_episode(episode)
            self.assertTrue(validation.valid, validation.issues)
            self.assertEqual(14, validation.event_count)

            output = root / "dataset"
            result = export_episode(episode, output, players=[PLAYER])
            self.assertEqual(1, result.sample_count)
            self.assertEqual(2, result.state_count)
            self.assertEqual(3, result.action_count)

            sample = json.loads((output / "samples.jsonl").read_text().splitlines()[0])
            self.assertEqual(10, sample["server_tick"])
            self.assertEqual(11, sample["next_server_tick"])
            self.assertEqual(0, sample["action"]["after_apply_sequence"])
            self.assertEqual(2, sample["action"]["through_apply_sequence"])
            self.assertEqual("control_state", sample["action"]["reconstructed_control"]["action_type"])
            packets = sample["action"]["ordered_packets"]
            self.assertEqual([1], [packet["apply_sequence"] for packet in packets])
            self.assertEqual("movement_controls", packets[0]["action_type"])
            self.assertTrue(sample["transition_valid"], sample["transition_invalid_reasons"])
            self.assertEqual(PEER, sample["peers"]["state"][0]["player_uuid"])
            self.assertEqual(PEER, sample["peers"]["next_state"][0]["player_uuid"])
            self.assertEqual(64, len(sample["source"]["state"]["events_sha256"]))

            manifest = json.loads((output / "manifest.json").read_text())
            self.assertEqual(20, manifest["timeline"]["sample_rate_hz"])
            self.assertEqual(64, len(manifest["source"]["epochs"][0]["events_sha256"]))

    def test_connection_filter_intersects_player_and_tick_filters(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            episode = _episode(root)
            output = root / "dataset"
            result = export_episode(
                episode,
                output,
                players=[PLAYER],
                connections=[CONNECTION],
                first_tick=10,
                last_tick=11,
            )

            self.assertEqual(1, result.sample_count)
            self.assertEqual(2, result.state_count)
            self.assertEqual(3, result.action_count)
            for filename in ("states.jsonl", "actions.jsonl", "modalities.jsonl"):
                rows = [
                    json.loads(line)
                    for line in (output / filename).read_text(encoding="utf-8").splitlines()
                ]
                self.assertTrue(rows)
                self.assertEqual({PLAYER}, {row["player_uuid"] for row in rows})
                self.assertEqual({CONNECTION}, {row["connection_id"] for row in rows})

            sample = json.loads((output / "samples.jsonl").read_text(encoding="utf-8"))
            self.assertEqual(CONNECTION, sample["connection_id"])
            self.assertEqual(PEER, sample["peers"]["state"][0]["player_uuid"])
            manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual([PLAYER], manifest["selection"]["players"])
            self.assertEqual([CONNECTION], manifest["selection"]["connections"])
            self.assertEqual(10, manifest["selection"]["from_tick"])
            self.assertEqual(11, manifest["selection"]["to_tick"])

    def test_connection_filter_does_not_select_another_players_connection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "dataset"
            result = export_episode(
                _episode(root),
                output,
                players=[PLAYER],
                connections=[PEER_CONNECTION],
            )

            self.assertEqual(0, result.sample_count)
            self.assertEqual(0, result.state_count)
            self.assertEqual(0, result.action_count)
            for filename in (
                "samples.jsonl",
                "states.jsonl",
                "actions.jsonl",
                "modalities.jsonl",
            ):
                self.assertEqual("", (output / filename).read_text(encoding="utf-8"))

    def test_connection_filter_isolates_same_player_reconnections(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            episode = _reconnected_episode(root)
            first_output = root / "first.dataset"
            second_output = root / "second.dataset"

            first = export_episode(
                episode,
                first_output,
                players=[PLAYER],
                connections=[CONNECTION],
            )
            second = export_episode(
                episode,
                second_output,
                players=[PLAYER],
                connections=[RECONNECTED_CONNECTION],
            )

            self.assertEqual(1, first.sample_count)
            self.assertEqual(1, second.sample_count)
            first_sample = json.loads((first_output / "samples.jsonl").read_text(encoding="utf-8"))
            second_sample = json.loads(
                (second_output / "samples.jsonl").read_text(encoding="utf-8")
            )
            self.assertEqual(
                (10, CONNECTION),
                (first_sample["server_tick"], first_sample["connection_id"]),
            )
            self.assertEqual(
                (12, RECONNECTED_CONNECTION),
                (second_sample["server_tick"], second_sample["connection_id"]),
            )
            self.assertEqual(PEER, second_sample["peers"]["state"][0]["player_uuid"])
            second_states = [
                json.loads(line)
                for line in (second_output / "states.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(
                {RECONNECTED_CONNECTION},
                {row["connection_id"] for row in second_states},
            )

    def test_rejects_invalid_connection_uuid_filter(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaisesRegex(RecorderError, "invalid connection UUID filter"):
                export_episode(
                    _episode(root),
                    root / "dataset",
                    connections=["not-a-connection-uuid"],
                )

    def test_attaches_completed_rgb_and_voxels_by_exact_sample_key(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            episode = _episode(root)
            render = _render_artifacts(root)
            output = root / "dataset"
            result = export_episode(
                episode,
                output,
                players=[PLAYER],
                frames=[render],
                voxels=[render],
            )
            self.assertEqual(2, result.rgb_count)
            self.assertEqual(2, result.voxel_count)

            modalities = [
                json.loads(line) for line in (output / "modalities.jsonl").read_text().splitlines()
            ]
            self.assertTrue(modalities[0]["rgb"]["valid"])
            self.assertTrue(Path(modalities[0]["rgb"]["reference"]).is_absolute())
            self.assertEqual(64, len(modalities[0]["rgb"]["artifact_sha256"]))
            self.assertEqual(RENDER_WIDTH, modalities[0]["rgb"]["width"])
            self.assertEqual(RENDER_HEIGHT, modalities[0]["rgb"]["height"])
            self.assertTrue(modalities[0]["voxels"]["valid"])
            self.assertFalse(modalities[0]["voxels"]["coverage_complete"])
            self.assertEqual({"x": 3, "y": 3, "z": 3}, modalities[0]["voxels"]["shape"])
            self.assertEqual(64, len(modalities[0]["voxels"]["artifact_sha256"]))
            self.assertEqual("minecraft:overworld", modalities[0]["voxels"]["dimension"])

            sample = json.loads((output / "samples.jsonl").read_text().splitlines()[0])
            self.assertTrue(sample["modalities"]["rgb"]["available"])
            self.assertTrue(sample["modalities"]["voxels"]["available"])
            manifest = json.loads((output / "manifest.json").read_text())
            self.assertEqual(2, manifest["modalities"]["rgb"]["records_attached"])
            self.assertEqual(2, manifest["modalities"]["voxels"]["records_attached"])
            self.assertEqual(1, len(manifest["selection"]["frame_attachments"]))
            self.assertEqual(1, len(manifest["selection"]["voxel_attachments"]))
            self.assertEqual(
                "ab" * 32,
                manifest["selection"]["frame_attachments"][0]["replay_sha256"],
            )
            self.assertEqual(
                123,
                manifest["selection"]["voxel_attachments"][0]["replay_bytes"],
            )

    def test_rejects_incomplete_or_identity_mismatched_render_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            episode = _episode(root)
            render = _render_artifacts(root, status="running")
            with self.assertRaisesRegex(RecorderError, "not complete"):
                export_episode(episode, root / "dataset-a", frames=[render])

            result_path = render / "result.json"
            result = json.loads(result_path.read_text())
            result["status"] = "complete"
            result_path.write_text(json.dumps(result), encoding="utf-8")
            index_path = render / "frames" / "frames.jsonl"
            rows = [json.loads(line) for line in index_path.read_text().splitlines()]
            rows[0]["connection_id"] = PEER_CONNECTION
            index_path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
            with self.assertRaisesRegex(RecorderError, "connection_id does not match"):
                export_episode(episode, root / "dataset-b", frames=[render])

    def test_rejects_completed_result_without_replay_integrity_envelope(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            episode = _episode(root)
            render = _render_artifacts(root)
            result_path = render / "result.json"
            result = json.loads(result_path.read_text())
            result.pop("replay_sha256")
            result_path.write_text(json.dumps(result), encoding="utf-8")
            with self.assertRaisesRegex(RecorderError, "valid replay_sha256"):
                export_episode(episode, root / "dataset", frames=[render])

    def test_never_trusts_unattached_source_voxel_references(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            episode = _episode(root)
            epoch = episode / "epochs" / "epoch-000000"
            records = [
                json.loads(line) for line in (epoch / "events.jsonl").read_text().splitlines()
            ]
            records[1]["voxel_ref"] = "/unvalidated/voxel.json.gz"
            records[1]["coverage_mask_ref"] = "/unvalidated/mask.bin"
            _seal_epoch(epoch, records)
            output = root / "dataset"
            export_episode(episode, output, players=[PLAYER])
            modality = json.loads((output / "modalities.jsonl").read_text().splitlines()[0])
            self.assertFalse(modality["voxels"]["available"])
            self.assertFalse(modality["voxels"]["valid"])
            self.assertIsNone(modality["voxels"]["reference"])
            self.assertEqual(
                "/unvalidated/voxel.json.gz",
                modality["voxels"]["unvalidated_source_reference"],
            )

    def test_rejects_png_signature_and_result_dimension_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            episode = _episode(root)
            render = _render_artifacts(root)
            first_frame = render / "frames" / "frame_000001.png"
            first_frame.write_bytes(b"not-a-png")
            with self.assertRaisesRegex(RecorderError, "invalid PNG signature"):
                export_episode(episode, root / "dataset-a", frames=[render])

            first_frame.write_bytes(_png(RENDER_WIDTH, RENDER_HEIGHT))
            result_path = render / "result.json"
            result = json.loads(result_path.read_text())
            result["width"] = RENDER_WIDTH + 1
            result_path.write_text(json.dumps(result), encoding="utf-8")
            with self.assertRaisesRegex(RecorderError, "PNG dimensions .* do not match"):
                export_episode(episode, root / "dataset-b", frames=[render])

    def test_rejects_corrupt_and_semantically_invalid_voxel_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            episode = _episode(root)
            render = _render_artifacts(root)
            first_voxel = render / "frames" / "voxels" / "voxel_000000000010.json.gz"
            first_voxel.write_bytes(b"not-gzip")
            with self.assertRaisesRegex(RecorderError, "not a valid complete gzip stream"):
                export_episode(episode, root / "dataset-a", voxels=[render])

            snapshot = _voxel_snapshot(10)
            indices = bytearray(base64.b64decode(snapshot["indices_base64"]))
            indices[0:2] = (1).to_bytes(2, "little")
            snapshot["indices_base64"] = base64.b64encode(indices).decode("ascii")
            _write_voxel(first_voxel, snapshot)
            with self.assertRaisesRegex(RecorderError, "references outside the palette"):
                export_episode(episode, root / "dataset-b", voxels=[render])

            snapshot = _voxel_snapshot(10)
            snapshot["coverage_bitset_base64"] = base64.b64encode(
                ((1 << 25) - 1).to_bytes(4, "little")
            ).decode("ascii")
            _write_voxel(first_voxel, snapshot)
            with self.assertRaisesRegex(RecorderError, "bitset count does not match"):
                export_episode(episode, root / "dataset-c", voxels=[render])

            snapshot = _voxel_snapshot(10)
            snapshot["dimension"] = "minecraft:the_nether"
            _write_voxel(first_voxel, snapshot)
            with self.assertRaisesRegex(RecorderError, "dimension does not match"):
                export_episode(episode, root / "dataset-d", voxels=[render])

            _write_voxel(first_voxel, _voxel_snapshot(10))
            with mock.patch("mc_recorder.exporter.MAX_VOXEL_JSON_BYTES", 128):
                with self.assertRaisesRegex(RecorderError, "decompressed voxel JSON exceeds"):
                    export_episode(episode, root / "dataset-e", voxels=[render])

    def test_rejects_symlinked_attached_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            episode = _episode(root)
            render = _render_artifacts(root)
            first_frame = render / "frames" / "frame_000001.png"
            first_frame.unlink()
            first_frame.symlink_to("frame_000002.png")
            with self.assertRaisesRegex(RecorderError, "contains a symlink"):
                export_episode(episode, root / "dataset", frames=[render])

    def test_rejects_attachment_key_absent_from_episode_states(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            episode = _episode(root)
            render = _render_artifacts(root)
            result_path = render / "result.json"
            result = json.loads(result_path.read_text())
            result["player_uuid"] = UNKNOWN_PLAYER
            result_path.write_text(json.dumps(result), encoding="utf-8")
            index_path = render / "frames" / "frames.jsonl"
            rows = [json.loads(line) for line in index_path.read_text().splitlines()]
            for row in rows:
                row["player_uuid"] = UNKNOWN_PLAYER
            index_path.write_text(
                "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
            )
            with self.assertRaisesRegex(RecorderError, "no matching episode player_state"):
                export_episode(episode, root / "dataset", frames=[render])

    def test_excludes_packet_outside_transition_barriers(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            episode = _episode(root)
            epoch = episode / "epochs" / "epoch-000000"
            events = epoch / "events.jsonl"
            records = [json.loads(line) for line in events.read_text().splitlines()]
            records[7]["apply_sequence"] = 3
            _seal_epoch(epoch, records)
            output = root / "dataset"
            export_episode(episode, output, players=[PLAYER])
            sample = json.loads((output / "samples.jsonl").read_text().splitlines()[0])
            self.assertEqual([], sample["action"]["ordered_packets"])
            self.assertEqual(1, sample["action"]["excluded_packet_count"])
            self.assertFalse(sample["transition_valid"])
            self.assertIn(
                "packet_apply_sequence_outside_transition_barriers",
                sample["transition_invalid_reasons"],
            )

    def test_cli_accepts_repeatable_frame_and_voxel_attachments(self) -> None:
        args = _parser().parse_args(
            [
                "export",
                "session-a",
                "--connection",
                CONNECTION,
                "--connection",
                PEER_CONNECTION,
                "--frames",
                "one",
                "--frames",
                "two",
                "--voxels",
                "three",
            ]
        )
        self.assertEqual([CONNECTION, PEER_CONNECTION], args.connection)
        self.assertEqual([Path("one"), Path("two")], args.frames)
        self.assertEqual([Path("three")], args.voxels)
        render = _parser().parse_args(
            [
                "render",
                "session-a",
                "--player",
                PLAYER,
                "--voxel-horizontal-radius",
                "12",
                "--voxel-vertical-radius",
                "6",
            ]
        )
        self.assertEqual(12, render.voxel_horizontal_radius)
        self.assertEqual(6, render.voxel_vertical_radius)

    def test_marks_barrier_disagreement_invalid_without_losing_sample(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            episode = _episode(root, invalid_barrier=True)
            output = root / "dataset"
            result = export_episode(episode, output, players=[PLAYER])
            self.assertEqual(1, result.sample_count)
            sample = json.loads((output / "samples.jsonl").read_text().splitlines()[0])
            self.assertFalse(sample["transition_valid"])
            self.assertIn("state_and_tick_barriers_disagree", sample["transition_invalid_reasons"])

    def test_does_not_cross_a_reconnection_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            episode = _episode(root, current_connection=PEER_CONNECTION)
            output = root / "dataset"
            result = export_episode(episode, output, players=[PLAYER])
            self.assertEqual(0, result.sample_count)

    def test_requires_explicitly_sealed_hash_verified_epoch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            episode = _episode(root)
            manifest_path = episode / "epochs" / "epoch-000000" / "manifest.json"
            manifest = json.loads(manifest_path.read_text())
            manifest.pop("sealed")
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            self.assertFalse(validate_episode(episode).valid)
            with self.assertRaisesRegex(RecorderError, "episode validation failed"):
                export_episode(episode, root / "dataset")

    def test_force_refuses_non_owned_export_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            episode = _episode(root)
            output = root / "important"
            output.mkdir()
            (output / "keep.txt").write_text("owned by somebody else", encoding="utf-8")
            with self.assertRaisesRegex(RecorderError, "non-owned"):
                export_episode(episode, output, force=True)
            self.assertEqual("owned by somebody else", (output / "keep.txt").read_text())

    def test_force_never_follows_a_symlinked_export_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            episode = _episode(root)
            owned = root / "owned-dataset"
            export_episode(episode, owned)
            linked = root / "linked-dataset"
            linked.symlink_to(owned, target_is_directory=True)

            with self.assertRaisesRegex(RecorderError, "symlinked export output"):
                export_episode(episode, linked, force=True)
            self.assertTrue((owned / "manifest.json").is_file())

    def test_force_replaces_only_an_intact_owned_export(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            episode = _episode(root)
            output = root / "dataset"
            export_episode(episode, output)
            export_episode(episode, output, force=True)
            self.assertTrue((output / "manifest.json").is_file())

            (output / "unexpected.txt").write_text("keep", encoding="utf-8")
            with self.assertRaisesRegex(RecorderError, "non-owned"):
                export_episode(episode, output, force=True)
            self.assertEqual("keep", (output / "unexpected.txt").read_text())

    def test_force_restores_the_verified_export_when_candidate_promotion_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            episode = _episode(root)
            output = root / "dataset"
            export_episode(episode, output)
            original_manifest = (output / "manifest.json").read_bytes()
            staging = root / ".candidate"
            staging.mkdir()
            original_rename = Path.rename

            def rename(path: Path, target: Path) -> Path:
                if path == staging:
                    raise OSError("simulated promotion failure")
                return original_rename(path, target)

            with mock.patch.object(Path, "rename", autospec=True, side_effect=rename):
                with self.assertRaisesRegex(OSError, "simulated promotion failure"):
                    _safe_replace_directory(staging, output, True)

            self.assertEqual(original_manifest, (output / "manifest.json").read_bytes())
            self.assertEqual([], list(root.glob(".dataset.backup-*")))

    def test_active_epoch_is_visible_but_not_valid_source_by_itself(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            episode = _episode(Path(temporary), active=True)
            info = inspect_episode(episode)
            assert info is not None
            self.assertEqual("active", info.status)
            validation = validate_episode(episode)
            self.assertTrue(validation.valid)
            self.assertEqual(1, validation.active_epochs)
            self.assertEqual(0, validation.sealed_epochs)

    def test_validation_rejects_out_of_order_global_sequence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            episode = _episode(Path(temporary))
            events = episode / "epochs" / "epoch-000000" / "events.jsonl"
            records = [json.loads(line) for line in events.read_text().splitlines()]
            records[-1]["sequence"] = 1
            _seal_epoch(events.parent, records)
            validation = validate_episode(episode)
            self.assertFalse(validation.valid)
            self.assertTrue(any("strictly increasing" in issue.message for issue in validation.issues))

    def test_prepares_but_does_not_claim_to_execute_render_job(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            episode = _episode(root)
            replay = root / "recording.zip"
            with zipfile.ZipFile(replay, "w") as archive:
                archive.writestr("metadata.json", "{}")
                archive.writestr("c0.flashback", b"test")
            result = prepare_render_job(
                episode,
                replay,
                root / "render-job",
                player_uuid=PLAYER,
                connection_id=None,
                width=640,
                height=360,
                fps=20,
                first_tick=None,
                last_tick=None,
                force=False,
                voxel_horizontal_radius=2,
                voxel_vertical_radius=1,
            )
            manifest = json.loads(result.manifest.read_text())
            self.assertEqual("prepared", manifest["status"])
            self.assertEqual("flashback", manifest["source_replay"]["format"])
            self.assertEqual(str(replay.resolve()), manifest["replay"])
            self.assertEqual(str((root / "render-job" / "frames").resolve()), manifest["output"])
            self.assertEqual(PLAYER, manifest["player_uuid"])
            self.assertEqual(CONNECTION, manifest["connection_id"])
            self.assertEqual(10, manifest["global_start_tick"])
            self.assertEqual(11, manifest["global_end_tick"])
            self.assertTrue(manifest["no_gui"])
            self.assertTrue(manifest["stop_when_done"])
            self.assertEqual(2, manifest["voxel_horizontal_radius"])
            self.assertEqual(1, manifest["voxel_vertical_radius"])
            self.assertTrue(manifest["voxel_crop"]["enabled"])
            self.assertEqual(75, manifest["voxel_crop"]["cells_per_tick"])
            self.assertEqual("mc.recorder.renderJob", manifest["renderer_contract"]["job_property"])
            self.assertFalse((result.directory / "result.json").exists())

    def test_render_force_refuses_non_owned_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            episode = _episode(root)
            replay = root / "recording.zip"
            with zipfile.ZipFile(replay, "w") as archive:
                archive.writestr("metadata.json", "{}")
                archive.writestr("c0.flashback", b"test")
            output = root / "important"
            output.mkdir()
            (output / "keep.txt").write_text("owned by somebody else", encoding="utf-8")

            with self.assertRaisesRegex(Exception, "non-owned"):
                prepare_render_job(
                    episode,
                    replay,
                    output,
                    player_uuid=PLAYER,
                    connection_id=None,
                    width=640,
                    height=360,
                    fps=20,
                    first_tick=None,
                    last_tick=None,
                    force=True,
                )
            self.assertEqual("owned by somebody else", (output / "keep.txt").read_text())

    def test_render_force_replaces_only_owned_job_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            episode = _episode(root)
            replay = root / "recording.zip"
            with zipfile.ZipFile(replay, "w") as archive:
                archive.writestr("metadata.json", "{}")
                archive.writestr("c0.flashback", b"test")
            output = root / "render-job"
            arguments = dict(
                player_uuid=PLAYER,
                connection_id=None,
                width=640,
                height=360,
                fps=20,
                first_tick=None,
                last_tick=None,
            )
            prepare_render_job(episode, replay, output, force=False, **arguments)
            prepare_render_job(episode, replay, output, force=True, **arguments)
            self.assertTrue((output / "render-job.json").is_file())


if __name__ == "__main__":
    unittest.main()
