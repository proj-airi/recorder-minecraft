from __future__ import annotations

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

from mc_recorder.errors import RecorderError
from mc_recorder.exporter import CANONICAL_RENDER_RESULT_TYPE, export_episode
from mc_recorder.render_contract import FULL_CLIENT_PRESENTATION_CONTRACT
from mc_recorder.render_transfer import (
    PORTABLE_REQUEST_TYPE,
    create_portable_render_request,
    create_render_bundle,
    import_render_bundle,
    load_portable_render_request,
    materialize_portable_render_job,
    write_portable_render_request,
)


PLAYER = "00000000-0000-4000-8000-000000000001"
CONNECTION = "00000000-0000-4000-8000-000000000002"
REQUEST_ID = "00000000-0000-4000-8000-000000000003"


def _event(record_type: str, tick: int, sequence: int, **values: object) -> dict[str, object]:
    return {
        "schema_version": 1,
        "record_type": record_type,
        "session_id": "session-a",
        "epoch_index": 0,
        "server_tick": tick,
        "sequence": sequence,
        "recorded_at_ns": sequence,
        **values,
    }


def _episode(root: Path) -> Path:
    episode = root / "session-a"
    epoch = episode / "epochs" / "epoch-000000"
    epoch.mkdir(parents=True)
    (episode / "manifest.json").write_text(
        json.dumps({"session_id": "session-a", "source_format": "mc-recorder-jsonl-v1"}),
        encoding="utf-8",
    )
    records = [
        _event("tick_start", 10, 1, apply_sequence_at_barrier=0),
        _event(
            "player_state",
            10,
            2,
            player_uuid=PLAYER,
            connection_id=CONNECTION,
            state_barrier_apply_sequence=0,
            dimension="minecraft:overworld",
        ),
        _event(
            "control_state",
            10,
            3,
            player_uuid=PLAYER,
            connection_id=CONNECTION,
            forward=False,
        ),
        _event("tick_end", 10, 4, apply_sequence_at_barrier=0),
        _event("tick_start", 11, 5, apply_sequence_at_barrier=0),
        _event(
            "player_state",
            11,
            6,
            player_uuid=PLAYER,
            connection_id=CONNECTION,
            state_barrier_apply_sequence=0,
            dimension="minecraft:overworld",
        ),
        _event(
            "control_state",
            11,
            7,
            player_uuid=PLAYER,
            connection_id=CONNECTION,
            forward=True,
        ),
        _event("tick_end", 11, 8, apply_sequence_at_barrier=0),
    ]
    encoded = "".join(json.dumps(row, sort_keys=True) + "\n" for row in records).encode()
    (epoch / "events.jsonl").write_bytes(encoded)
    (epoch / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "session_id": "session-a",
                "epoch_index": 0,
                "sealed": True,
                "record_count": len(records),
                "events_bytes": len(encoded),
                "events_sha256": hashlib.sha256(encoded).hexdigest(),
                "first_server_tick": 10,
                "last_server_tick": 11,
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


def _png(width: int = 64, height: int = 64) -> bytes:
    def chunk(name: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + name
            + data
            + struct.pack(">I", zlib.crc32(name + data) & 0xFFFFFFFF)
        )

    pixels = b"".join(b"\x00" + b"\x00" * (width * 4) for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(pixels))
        + chunk(b"IEND", b"")
    )


def _request(
    root: Path,
    *,
    range_policy: str = "exact",
    newer_cutoff: int | None = None,
    no_gui: bool = False,
    presentation_contract: str | None = None,
) -> tuple[dict[str, object], Path, Path]:
    episode = _episode(root)
    replay = _replay(root)
    request = create_portable_render_request(
        episode,
        replay,
        segment_id="segment-0001",
        segment_ordinal=1,
        player_uuid=PLAYER,
        connection_id=CONNECTION,
        width=64,
        height=64,
        request_id=REQUEST_ID,
        range_policy=range_policy,
        newer_cutoff=newer_cutoff,
        no_gui=no_gui,
        presentation_contract=presentation_contract,
    )
    return request, episode, replay


def _complete_job(
    root: Path,
    request: dict[str, object],
    replay: Path,
    *,
    ticks: tuple[int, ...] = (10, 11),
) -> Path:
    job = materialize_portable_render_job(request, replay, root / "job")
    frames = job.directory / "frames"
    rows: list[dict[str, object]] = []
    first = min(ticks)
    for frame, tick in enumerate(ticks, 1):
        filename = f"frame_{frame:06d}.png"
        (frames / filename).write_bytes(_png())
        rows.append(
            {
                "frame": frame,
                "server_tick": tick,
                "replay_tick": tick - first,
                "partial_tick": 0.0,
                "session_id": "session-a",
                "connection_id": CONNECTION,
                "player_uuid": PLAYER,
                "path": filename,
            }
        )
    (frames / "frames.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )
    digest = hashlib.sha256(replay.read_bytes()).hexdigest()
    (job.directory / "result.json").write_text(
        json.dumps(
            {
                "status": "complete",
                "replay": str(replay.resolve()),
                "replay_sha256": digest,
                "replay_bytes": replay.stat().st_size,
                "output": str(frames.resolve()),
                "session_id": "session-a",
                "connection_id": CONNECTION,
                "player_uuid": PLAYER,
                "global_start_tick": min(ticks),
                "global_end_tick": max(ticks),
                "replay_start_tick": 0,
                "replay_end_tick": max(ticks) - min(ticks),
                "global_tick_offset": min(ticks),
                "fps": 20,
                "width": 64,
                "height": 64,
                "no_gui": request["render"].get("no_gui", True),
                **(
                    {
                        "presentation_contract": request["render"][
                            "presentation_contract"
                        ]
                    }
                    if "presentation_contract" in request["render"]
                    else {}
                ),
                "voxel_snapshots": 0,
                "voxel_horizontal_radius": 0,
                "voxel_vertical_radius": 0,
            }
        ),
        encoding="utf-8",
    )
    return job.directory


class PortableRenderTransferTest(unittest.TestCase):
    def test_request_is_path_free_hash_bound_and_materializes_locally(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            request, _episode_path, replay = _request(root)
            encoded = json.dumps(request)
            self.assertNotIn(str(root), encoded)
            self.assertEqual(PORTABLE_REQUEST_TYPE, request["request_type"])
            self.assertEqual("segment-0001", request["source_replay"]["segment_id"])
            self.assertFalse(request["render"]["no_gui"])
            published = write_portable_render_request(root / "request.json", request)
            self.assertEqual(published, load_portable_render_request(root / "request.json"))
            self.assertEqual(published, write_portable_render_request(root / "request.json", request))

            materialized = materialize_portable_render_job(
                published, replay, root / "render-job"
            )
            job = json.loads(materialized.manifest.read_text())
            self.assertEqual(published.sha256, job["portable_request"]["sha256"])
            self.assertEqual(str(replay.resolve()), job["replay"])
            self.assertEqual(str((root / "render-job" / "frames").resolve()), job["output"])
            self.assertFalse(job["no_gui"])

            legacy = json.loads(json.dumps(request))
            legacy["render"].pop("no_gui")
            legacy_job = materialize_portable_render_job(
                legacy, replay, root / "legacy-render-job"
            )
            self.assertTrue(json.loads(legacy_job.manifest.read_text())["no_gui"])

            invalid = json.loads(json.dumps(request))
            invalid["render"]["no_gui"] = "false"
            with self.assertRaisesRegex(RecorderError, "no_gui must be a boolean"):
                write_portable_render_request(root / "invalid-request.json", invalid)

    def test_legacy_hud_free_request_is_idempotent_after_upgrade(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            request, _episode_path, replay = _request(root, no_gui=True)
            legacy = json.loads(json.dumps(request))
            legacy["render"].pop("no_gui")
            path = root / "request.json"
            original = write_portable_render_request(path, legacy)
            original_bytes = path.read_bytes()

            reused = write_portable_render_request(path, request)

            self.assertEqual(original.sha256, reused.sha256)
            self.assertNotIn("no_gui", reused.data["render"])
            self.assertEqual(original_bytes, path.read_bytes())

            explicit_path = root / "explicit-request.json"
            explicit_path.write_text(json.dumps(request, indent=2) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(RecorderError, "different bytes"):
                write_portable_render_request(explicit_path, request)

            job = _complete_job(root, legacy, replay)
            result_path = job / "result.json"
            result = json.loads(result_path.read_text())
            result.pop("no_gui")
            result_path.write_text(json.dumps(result), encoding="utf-8")
            bundle = create_render_bundle(
                job, root / "legacy-bundle", legacy, use_hardlinks=False
            )
            self.assertTrue(bundle.manifest.is_file())

    def test_complete_bundle_imports_canonical_relative_result_and_exports(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            request, episode, replay = _request(root)
            request_path = root / "request.json"
            write_portable_render_request(request_path, request)
            job = _complete_job(root, request, replay)
            bundle = create_render_bundle(
                job, root / "bundle", request_path, use_hardlinks=False
            )
            imported = import_render_bundle(
                request_path, bundle.directory, replay, root / "imported"
            )
            self.assertFalse(imported.reused)
            self.assertEqual("complete", imported.status)
            result = json.loads(imported.result.read_text())
            self.assertEqual(CANONICAL_RENDER_RESULT_TYPE, result["result_type"])
            self.assertEqual("frames", result["output"])
            self.assertEqual("frames/frames.jsonl", result["frames_index"])
            self.assertFalse(result["no_gui"])
            self.assertNotIn(str(root), imported.result.read_text())
            self.assertEqual("segment-0001", result["source_replay"]["segment_id"])

            reused = import_render_bundle(
                request_path, bundle.directory, replay, root / "imported"
            )
            self.assertTrue(reused.reused)
            dataset = export_episode(episode, root / "dataset", frames=[imported.directory])
            self.assertEqual(2, dataset.rgb_count)
            manifest = json.loads((dataset.output / "manifest.json").read_text())
            source = manifest["selection"]["frame_attachments"][0]
            self.assertEqual("segment-0001", source["source_replay"]["segment_id"])
            self.assertFalse(source["no_gui"])

            result["output"] = str((imported.directory / "frames").resolve())
            imported.result.write_text(json.dumps(result), encoding="utf-8")
            with self.assertRaisesRegex(RecorderError, "contained relative path"):
                export_episode(episode, root / "dataset-invalid", frames=[imported.directory])

    def test_worker_result_gui_mode_must_match_the_request(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            request, _episode_path, replay = _request(root)
            job = _complete_job(root, request, replay)
            result_path = job / "result.json"
            result = json.loads(result_path.read_text())
            result["no_gui"] = True
            result_path.write_text(json.dumps(result), encoding="utf-8")

            with self.assertRaisesRegex(RecorderError, "no_gui does not match"):
                create_render_bundle(job, root / "bundle", request, use_hardlinks=False)

    def test_hud_free_worker_result_cannot_claim_a_full_client_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            request, _episode_path, replay = _request(root, no_gui=True)
            job = _complete_job(root, request, replay)
            result_path = job / "result.json"
            result = json.loads(result_path.read_text())
            result["presentation_contract"] = FULL_CLIENT_PRESENTATION_CONTRACT
            result_path.write_text(json.dumps(result), encoding="utf-8")

            with self.assertRaisesRegex(RecorderError, "requires no_gui=false"):
                create_render_bundle(
                    job, root / "invalid-bundle", request, use_hardlinks=False
                )

    def test_full_client_presentation_contract_round_trips_and_is_request_bound(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            request, episode, replay = _request(
                root,
                presentation_contract=FULL_CLIENT_PRESENTATION_CONTRACT,
            )
            self.assertEqual(
                FULL_CLIENT_PRESENTATION_CONTRACT,
                request["render"]["presentation_contract"],
            )
            job = _complete_job(root, request, replay)
            job_manifest = json.loads((job / "render-job.json").read_text())
            self.assertEqual(
                FULL_CLIENT_PRESENTATION_CONTRACT,
                job_manifest["presentation_contract"],
            )
            bundle = create_render_bundle(
                job, root / "bundle", request, use_hardlinks=False
            )
            imported = import_render_bundle(
                request, bundle.directory, replay, root / "imported"
            )
            canonical = json.loads(imported.result.read_text())
            self.assertEqual(
                FULL_CLIENT_PRESENTATION_CONTRACT,
                canonical["presentation_contract"],
            )
            dataset = export_episode(
                episode, root / "dataset", frames=[imported.directory]
            )
            manifest = json.loads((dataset.output / "manifest.json").read_text())
            self.assertEqual(
                FULL_CLIENT_PRESENTATION_CONTRACT,
                manifest["selection"]["frame_attachments"][0][
                    "presentation_contract"
                ],
            )

            result_path = job / "result.json"
            result = json.loads(result_path.read_text())
            result.pop("presentation_contract")
            result_path.write_text(json.dumps(result), encoding="utf-8")
            with self.assertRaisesRegex(
                RecorderError, "presentation_contract does not match"
            ):
                create_render_bundle(
                    job, root / "mismatched-bundle", request, use_hardlinks=False
                )

    def test_presentation_contract_validation_preserves_unmarked_legacy_requests(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            request, _episode_path, replay = _request(root)
            self.assertNotIn("presentation_contract", request["render"])
            legacy_job = _complete_job(root, request, replay)
            self.assertTrue(
                create_render_bundle(
                    legacy_job, root / "legacy-bundle", request, use_hardlinks=False
                ).manifest.is_file()
            )
            result_path = legacy_job / "result.json"
            upgraded_result = json.loads(result_path.read_text())
            upgraded_result[
                "presentation_contract"
            ] = FULL_CLIENT_PRESENTATION_CONTRACT
            result_path.write_text(json.dumps(upgraded_result), encoding="utf-8")
            upgraded_bundle = create_render_bundle(
                legacy_job, root / "upgraded-bundle", request, use_hardlinks=False
            )
            upgraded_import = import_render_bundle(
                request,
                upgraded_bundle.directory,
                replay,
                root / "upgraded-import",
            )
            self.assertEqual(
                FULL_CLIENT_PRESENTATION_CONTRACT,
                json.loads(upgraded_import.result.read_text())[
                    "presentation_contract"
                ],
            )

            invalid = json.loads(json.dumps(request))
            invalid["render"]["presentation_contract"] = "unknown_v9"
            with self.assertRaisesRegex(
                RecorderError, "presentation_contract is unsupported"
            ):
                write_portable_render_request(root / "invalid.json", invalid)

            hud_free = json.loads(json.dumps(request))
            hud_free["render"]["no_gui"] = True
            hud_free["render"][
                "presentation_contract"
            ] = FULL_CLIENT_PRESENTATION_CONTRACT
            with self.assertRaisesRegex(RecorderError, "requires no_gui=false"):
                write_portable_render_request(root / "hud-free.json", hud_free)

    def test_rejects_tampering_symlinks_hardlinks_and_extra_payloads(self) -> None:
        cases = ("tamper", "symlink", "hardlink", "extra")
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                request, _episode_path, replay = _request(root)
                job = _complete_job(root, request, replay)
                bundle = create_render_bundle(
                    job, root / "bundle", request, use_hardlinks=False
                )
                frame = bundle.directory / "frames" / "frame_000001.png"
                if case == "tamper":
                    frame.write_bytes(b"tampered")
                    expected = "integrity mismatch"
                elif case == "symlink":
                    frame.unlink()
                    frame.symlink_to("frame_000002.png")
                    expected = "symlink"
                elif case == "hardlink":
                    original = bundle.directory / "frames" / "frame_000002.png"
                    frame.unlink()
                    frame.hardlink_to(original)
                    expected = "hard-linked"
                else:
                    (bundle.directory / "extra.txt").write_text("unexpected")
                    expected = "unexpected or missing"
                with self.assertRaisesRegex(RecorderError, expected):
                    import_render_bundle(request, bundle.directory, replay, root / "imported")

    def test_intersection_accepts_subset_and_no_coverage_but_exact_does_not(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            request, _episode_path, replay = _request(root, range_policy="intersection")
            job = _complete_job(root, request, replay, ticks=(10,))
            bundle = create_render_bundle(job, root / "bundle", request, use_hardlinks=False)
            imported = import_render_bundle(request, bundle.directory, replay, root / "imported")
            self.assertEqual("complete", imported.status)
            result = json.loads(imported.result.read_text())
            self.assertEqual(10, result["global_start_tick"])
            self.assertEqual(10, result["global_end_tick"])

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            request, _episode_path, replay = _request(root)
            job = _complete_job(root, request, replay, ticks=(10,))
            with self.assertRaisesRegex(RecorderError, "exact requested range"):
                create_render_bundle(job, root / "bundle", request, use_hardlinks=False)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            request, _episode_path, replay = _request(root, range_policy="intersection")
            job = materialize_portable_render_job(request, replay, root / "job")
            (job.directory / "result.json").write_text(
                json.dumps(
                    {
                        "status": "no_coverage",
                        "reason": "timeline_marker_not_found",
                        "replay_sha256": hashlib.sha256(replay.read_bytes()).hexdigest(),
                        "replay_bytes": replay.stat().st_size,
                        "session_id": "session-a",
                        "connection_id": CONNECTION,
                        "player_uuid": PLAYER,
                        "no_gui": request["render"].get("no_gui", True),
                    }
                ),
                encoding="utf-8",
            )
            bundle = create_render_bundle(
                job, root / "bundle", request, use_hardlinks=False
            )
            imported = import_render_bundle(request, bundle.directory, replay, root / "imported")
            self.assertEqual("no_coverage", imported.status)
            result = json.loads(imported.result.read_text())
            self.assertEqual("timeline_marker_not_found", result["reason"])
            self.assertFalse((imported.directory / "frames" / "frames.jsonl").exists())

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            request, _episode_path, replay = _request(
                root, range_policy="intersection", newer_cutoff=11
            )
            job = _complete_job(root, request, replay, ticks=(11,))
            with self.assertRaisesRegex(RecorderError, "outside the requested segment intersection"):
                create_render_bundle(job, root / "bundle", request, use_hardlinks=False)

    def test_rejects_traversal_from_worker_indexes_and_request_extensions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            request, _episode_path, replay = _request(root)
            extended = json.loads(json.dumps(request))
            extended["source_path"] = "/tmp/not-authoritative"
            with self.assertRaisesRegex(RecorderError, "unsupported fields"):
                write_portable_render_request(root / "request.json", extended)

            job = _complete_job(root, request, replay)
            index = job / "frames" / "frames.jsonl"
            rows = [json.loads(line) for line in index.read_text().splitlines()]
            rows[0]["path"] = "../frame_000001.png"
            index.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
            with self.assertRaisesRegex(RecorderError, "invalid path components"):
                create_render_bundle(job, root / "bundle", request, use_hardlinks=False)

    def test_rejects_conflicting_request_and_authoritative_replay(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            request, _episode_path, replay = _request(root)
            modified = json.loads(json.dumps(request))
            modified["request_id"] = str(uuid.uuid4())
            job = _complete_job(root, request, replay)
            bundle = create_render_bundle(job, root / "bundle", request, use_hardlinks=False)
            with self.assertRaisesRegex(RecorderError, "request_id"):
                import_render_bundle(modified, bundle.directory, replay, root / "imported-a")

            other = root / "other.zip"
            with zipfile.ZipFile(other, "w") as archive:
                archive.writestr("metadata.json", "{}")
                archive.writestr("other.flashback", b"different")
            with self.assertRaisesRegex(RecorderError, "size or SHA-256"):
                import_render_bundle(request, bundle.directory, other, root / "imported-b")


if __name__ == "__main__":
    unittest.main()
