from __future__ import annotations

import base64
import gzip
import hashlib
import json
import struct
import sys
import tempfile
import unittest
import uuid
import zipfile
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from mc_recorder.config import initialize, load_config
from mc_recorder.dataset_viewer import opaque_dataset_id
from mc_recorder.errors import RecorderError
from mc_recorder.exporter import export_episode
from mc_recorder.render_attach import attach_imported_renders
from mc_recorder.render_transfer import (
    create_portable_render_request,
    create_render_bundle,
    import_render_bundle,
    materialize_portable_render_job,
)


PLAYER = "00000000-0000-4000-8000-000000000001"
CONNECTION = "00000000-0000-4000-8000-000000000002"
JOB_ID = "00000000-0000-4000-8000-000000000003"
RECORDING_ID = "a" * 24
SESSION = "session-a"


def _event(record_type: str, tick: int, sequence: int, **values: object) -> dict[str, object]:
    return {
        "schema_version": 1,
        "record_type": record_type,
        "session_id": SESSION,
        "epoch_index": 0,
        "server_tick": tick,
        "sequence": sequence,
        "recorded_at_ns": sequence,
        **values,
    }


def _episode(captures: Path) -> Path:
    episode = captures / SESSION
    epoch = episode / "epochs" / "epoch-000000"
    epoch.mkdir(parents=True)
    (episode / "manifest.json").write_text(
        json.dumps({"session_id": SESSION, "source_format": "mc-recorder-jsonl-v1"}),
        encoding="utf-8",
    )
    records: list[dict[str, object]] = []
    sequence = 1
    for tick in (10, 11, 12):
        records.extend(
            [
                _event("tick_start", tick, sequence, apply_sequence_at_barrier=0),
                _event(
                    "player_state",
                    tick,
                    sequence + 1,
                    player_uuid=PLAYER,
                    connection_id=CONNECTION,
                    state_barrier_apply_sequence=0,
                    dimension="minecraft:overworld",
                    position={"x": 1.0, "y": 64.0, "z": 2.0},
                ),
                _event(
                    "control_state",
                    tick,
                    sequence + 2,
                    player_uuid=PLAYER,
                    connection_id=CONNECTION,
                    forward=False,
                ),
                _event("tick_end", tick, sequence + 3, apply_sequence_at_barrier=0),
            ]
        )
        sequence += 4
    encoded = "".join(json.dumps(row, sort_keys=True) + "\n" for row in records).encode()
    (epoch / "events.jsonl").write_bytes(encoded)
    (epoch / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "session_id": SESSION,
                "epoch_index": 0,
                "sealed": True,
                "record_count": len(records),
                "events_bytes": len(encoded),
                "events_sha256": hashlib.sha256(encoded).hexdigest(),
                "first_server_tick": 10,
                "last_server_tick": 12,
            }
        ),
        encoding="utf-8",
    )
    return episode


def _replay(root: Path) -> Path:
    replay = root / "recording.zip"
    with zipfile.ZipFile(replay, "w") as archive:
        archive.writestr("metadata.json", "{}")
        archive.writestr("c0.flashback", b"render source")
    return replay


def _png() -> bytes:
    def chunk(name: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + name
            + payload
            + struct.pack(">I", zlib.crc32(name + payload) & 0xFFFFFFFF)
        )

    rows = b"".join(b"\0" + b"\0" * (64 * 4) for _ in range(64))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 64, 64, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )


def _voxel(tick: int) -> tuple[dict[str, object], bytes]:
    total = 27
    snapshot = {
        "schema_version": 1,
        "format": "mc-recorder-voxel-palette-v1",
        "session_id": SESSION,
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
        "indices_base64": base64.b64encode(b"\0\0" * total).decode(),
        "coverage_bitset_base64": base64.b64encode(((1 << 26) - 1).to_bytes(4, "little")).decode(),
        "coverage_bit_order": "lsb0",
        "covered_cells": 26,
        "total_cells": total,
        "coverage_complete": False,
        "palette": ["minecraft:air"],
        "block_entities_included": False,
        "block_entities_note": "not materialized in v1",
    }
    row = {
        "schema_version": 1,
        "session_id": SESSION,
        "connection_id": CONNECTION,
        "player_uuid": PLAYER,
        "server_tick": tick,
        "replay_tick": tick - 10,
        "reference": f"voxels/voxel_{tick:012d}.json.gz",
        "origin": snapshot["origin"],
        "shape": snapshot["shape"],
        "covered_cells": 26,
        "total_cells": total,
        "coverage_complete": False,
    }
    return row, gzip.compress(json.dumps(snapshot).encode(), mtime=0)


def _dataset(config, episode: Path, *, matching: bool = True) -> Path:
    output = config.paths.exports / f"{SESSION}-{PLAYER}-{CONNECTION}.dataset"
    export_episode(
        episode,
        output,
        players=[PLAYER] if matching else [],
        connections=[CONNECTION],
        first_tick=10,
        last_tick=12,
    )
    return output


def _job(config, output: Path) -> dict[str, object]:
    return {
        "id": JOB_ID,
        "recording_id": RECORDING_ID,
        "state": "verifying",
        "payload": {
            "recording_id": RECORDING_ID,
            "session_id": SESSION,
            "player_uuid": PLAYER,
            "connection_id": CONNECTION,
            "dataset_id": opaque_dataset_id(config.paths.exports, output.name),
            "start_tick": 10,
            "end_tick": 12,
            "render": {"width": 64, "height": 64, "fps": 20},
        },
    }


def _import(
    config,
    episode: Path,
    replay: Path,
    *,
    status: str = "complete",
    voxels: bool = False,
    segment_id: str = "segment-0001",
    first_tick: int = 10,
    last_tick: int = 12,
) -> dict[str, object]:
    request = create_portable_render_request(
        episode,
        replay,
        segment_id=segment_id,
        segment_ordinal=1,
        player_uuid=PLAYER,
        connection_id=CONNECTION,
        width=64,
        height=64,
        first_tick=first_tick,
        last_tick=last_tick,
        voxel_horizontal_radius=1 if voxels else 0,
        voxel_vertical_radius=1 if voxels else 0,
        request_id=str(uuid.uuid5(uuid.NAMESPACE_DNS, segment_id)),
        range_policy="intersection",
    )
    work = config.paths.runtime / "test-worker" / segment_id
    materialized = materialize_portable_render_job(request, replay, work / "job")
    if status == "no_coverage":
        raw_result: dict[str, object] = {
            "status": "no_coverage",
            "reason": "timeline_marker_not_found",
            "replay_sha256": hashlib.sha256(replay.read_bytes()).hexdigest(),
            "replay_bytes": replay.stat().st_size,
            "session_id": SESSION,
            "connection_id": CONNECTION,
            "player_uuid": PLAYER,
        }
    else:
        frames = materialized.directory / "frames"
        frame_rows: list[dict[str, object]] = []
        voxel_rows: list[dict[str, object]] = []
        if voxels:
            (frames / "voxels").mkdir()
        for number, tick in enumerate(range(first_tick, last_tick + 1), 1):
            name = f"frame_{number:06d}.png"
            (frames / name).write_bytes(_png())
            frame_rows.append(
                {
                    "frame": number,
                    "server_tick": tick,
                    "replay_tick": tick - 10,
                    "partial_tick": 0.0,
                    "session_id": SESSION,
                    "connection_id": CONNECTION,
                    "player_uuid": PLAYER,
                    "path": name,
                }
            )
            if voxels:
                voxel_row, compressed = _voxel(tick)
                (frames / "voxels" / f"voxel_{tick:012d}.json.gz").write_bytes(compressed)
                voxel_rows.append(voxel_row)
        (frames / "frames.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in frame_rows), encoding="utf-8"
        )
        if voxels:
            (frames / "voxels.jsonl").write_text(
                "".join(json.dumps(row) + "\n" for row in voxel_rows), encoding="utf-8"
            )
        raw_result = {
            "status": "complete",
            "replay": str(replay.resolve()),
            "replay_sha256": hashlib.sha256(replay.read_bytes()).hexdigest(),
            "replay_bytes": replay.stat().st_size,
            "output": str(frames.resolve()),
            "session_id": SESSION,
            "connection_id": CONNECTION,
            "player_uuid": PLAYER,
            "global_start_tick": first_tick,
            "global_end_tick": last_tick,
            "replay_start_tick": 0,
            "replay_end_tick": last_tick - first_tick,
            "global_tick_offset": first_tick,
            "fps": 20,
            "width": 64,
            "height": 64,
            "voxel_snapshots": last_tick - first_tick + 1 if voxels else 0,
            "voxel_horizontal_radius": 1 if voxels else 0,
            "voxel_vertical_radius": 1 if voxels else 0,
        }
        if voxels:
            raw_result["voxel_index"] = str((frames / "voxels.jsonl").resolve())
    (materialized.directory / "result.json").write_text(json.dumps(raw_result), encoding="utf-8")
    bundle = create_render_bundle(
        materialized.directory, work / "bundle", request, use_hardlinks=False
    )
    destination = config.paths.exports / "render-jobs" / JOB_ID / segment_id
    imported = import_render_bundle(request, bundle.directory, replay, destination)
    return {
        "segment_id": segment_id,
        "request_id": imported.request_id,
        "status": imported.status,
        "reused": imported.reused,
        "directory": str(imported.directory),
        "result": str(imported.result),
        "manifest": str(imported.manifest),
    }


class RenderAttachmentTest(unittest.TestCase):
    def _fixture(self, root: Path):
        config = load_config(initialize(root / "recorder.toml", accept_eula=True))
        episode = _episode(config.paths.captures)
        replay = _replay(root)
        output = _dataset(config, episode)
        return config, episode, replay, output, _job(config, output)

    def test_attaches_complete_rgb_and_voxels_then_validates_viewer_counts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config, episode, replay, output, job = self._fixture(Path(temporary))
            imported = _import(config, episode, replay, voxels=True)

            result = attach_imported_renders(config, job, [imported])

            self.assertFalse(result.partial)
            self.assertEqual(2, result.sample_count)
            self.assertEqual(2, result.rgb_sample_count)
            self.assertEqual(2, result.voxel_sample_count)
            self.assertEqual(3, result.rendered_tick_count)
            self.assertEqual((10, 12), (result.coverage_start_tick, result.coverage_end_tick))
            self.assertEqual(output, result.output)
            self.assertEqual(result.dataset_id, result.as_json()["dataset_id"])

    def test_no_coverage_is_partial_and_preserves_the_verified_dataset(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config, episode, replay, output, job = self._fixture(Path(temporary))
            imported = _import(config, episode, replay, status="no_coverage")
            before = {path.name: path.read_bytes() for path in output.iterdir()}

            result = attach_imported_renders(config, job, [imported])

            self.assertTrue(result.partial)
            self.assertEqual(0, result.complete_import_count)
            self.assertEqual(1, result.no_coverage_import_count)
            self.assertEqual(0, result.rgb_sample_count)
            self.assertEqual(before, {path.name: path.read_bytes() for path in output.iterdir()})

    def test_render_range_can_be_narrower_than_the_preserved_dataset_selection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config, episode, replay, output, job = self._fixture(Path(temporary))
            job["payload"].update(
                {
                    "start_tick": 10,
                    "end_tick": 11,
                    "selection_start_tick": 10,
                    "selection_end_tick": 12,
                }
            )
            imported = _import(config, episode, replay, first_tick=10, last_tick=11)

            result = attach_imported_renders(config, job, [imported])

            self.assertFalse(result.partial)
            self.assertEqual(2, result.rendered_tick_count)
            self.assertEqual((10, 11), (result.requested_start_tick, result.requested_end_tick))
            manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(10, manifest["selection"]["from_tick"])
            self.assertEqual(12, manifest["selection"]["to_tick"])

    def test_refuses_missing_conflicting_and_tampered_existing_datasets(self) -> None:
        for case in ("missing", "conflicting", "tampered"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                config = load_config(initialize(root / "recorder.toml", accept_eula=True))
                episode = _episode(config.paths.captures)
                replay = _replay(root)
                output = config.paths.exports / f"{SESSION}-{PLAYER}-{CONNECTION}.dataset"
                if case != "missing":
                    output = _dataset(config, episode, matching=case != "conflicting")
                job = _job(config, output)
                imported = _import(config, episode, replay)
                if case == "tampered":
                    (output / "samples.jsonl").write_text("tampered\n", encoding="utf-8")
                snapshot = (
                    {path.name: path.read_bytes() for path in output.iterdir()}
                    if output.exists()
                    else None
                )

                with self.assertRaises(RecorderError):
                    attach_imported_renders(config, job, [imported])

                if snapshot is None:
                    self.assertFalse(output.exists())
                else:
                    self.assertEqual(snapshot, {path.name: path.read_bytes() for path in output.iterdir()})

    def test_rejects_import_result_tampering_and_paths_outside_the_fixed_job_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config, episode, replay, output, job = self._fixture(Path(temporary))
            imported = _import(config, episode, replay)
            before = (output / "manifest.json").read_bytes()
            result_path = Path(str(imported["result"]))
            value = json.loads(result_path.read_text())
            value["connection_id"] = str(uuid.uuid4())
            result_path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(RecorderError, "identity|manifest"):
                attach_imported_renders(config, job, [imported])
            self.assertEqual(before, (output / "manifest.json").read_bytes())

            outside = {**imported, "directory": str(config.paths.exports)}
            with self.assertRaisesRegex(RecorderError, "outside"):
                attach_imported_renders(config, job, [outside])


if __name__ == "__main__":
    unittest.main()
