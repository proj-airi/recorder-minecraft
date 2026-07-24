from __future__ import annotations

import errno
import hashlib
import json
import os
import sqlite3
import stat
import struct
import sys
import tempfile
import unittest
import warnings
import zipfile
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest import mock

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from minerec.processing.bundle import (
    ArtifactSource,
    BundleError,
    BundleIdentity,
    BundleLimits,
    BundleRequest,
    RenderAttachment,
    ReplaySegment,
    canonical_json_bytes,
    deterministic_bundle_id,
    open_bundle,
    publish_bundle,
)
from minerec.processing.bundle.contract import validate_metadata
from minerec.processing.bundle.reader import _validate_flashback_replay

PLAYER_UUID = "12345678-1234-5678-9234-567812345678"
SERVER_INSTANCE_ID = "87654321-4321-4678-9234-567812345678"
CONNECTION_ID = "11111111-2222-4333-8444-555555555555"


def _artifact(path: Path) -> ArtifactSource:
    data = path.read_bytes()
    return ArtifactSource(
        path=path,
        sha256=hashlib.sha256(data).hexdigest(),
        size_bytes=len(data),
    )


def _write_scene(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute("CREATE TABLE frame_ticks(server_tick INTEGER PRIMARY KEY)")
        connection.executemany("INSERT INTO frame_ticks VALUES (?)", ((10,), (11,)))
        connection.commit()
    finally:
        connection.close()


def _write_actions(path: Path) -> None:
    rows = [
        {
            "schema_version": 2,
            "session_id": "session-a",
            "epoch_index": 0,
            "server_tick": tick,
            "sequence": tick,
            "apply_sequence": None,
            "player_uuid": PLAYER_UUID,
            "connection_id": CONNECTION_ID,
            "action_type": "control_state",
            "applied": True,
            "payload": {
                "player_uuid": PLAYER_UUID,
                "connection_id": CONNECTION_ID,
                "forward": tick == 10,
            },
            "source": {
                "epoch_index": 0,
                "event_sequence": tick,
                "events_sha256": "a" * 64,
                "epoch_manifest_sha256": "b" * 64,
                "record_type": "control_state",
                "recorded_at_ns": tick * 1_000,
                "arrival_sequence": None,
            },
        }
        for tick in (10, 11)
    ]
    path.write_bytes(b"".join(json.dumps(row, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n" for row in rows))


def _write_replay(
    path: Path,
    payload: bytes,
    *,
    segment_id: str,
    segment_ordinal: int,
    session_id: str = "session-a",
    extra_entries: int = 0,
    nested_uncompressed_bytes: int = 0,
) -> None:
    identity = {
        "schema_version": 3,
        "session_id": session_id,
        "segment_id": segment_id,
        "segment_ordinal": segment_ordinal,
        "player_uuid": PLAYER_UUID,
        "connection_id": CONNECTION_ID,
        "hotbar_snapshot_contract": "item_stack_copy_v1",
        "flashback_capture_contract": "client_visible_scene_v1",
    }
    with zipfile.ZipFile(path, "w", allowZip64=True) as archive:
        archive.writestr("metadata.json", "{}")
        archive.writestr("arcade_replay_meta.json", json.dumps({"mc_recorder": identity}))
        archive.writestr("chunks/c0.flashback", payload)
        for index in range(extra_entries):
            archive.writestr(f"extras/{index:06d}.bin", b"")
        if nested_uncompressed_bytes:
            info = zipfile.ZipInfo("extras/oversized.bin")
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, b"0" * nested_uncompressed_bytes)


def _mp4_box(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I4s", len(payload) + 8, kind) + payload


def _write_render(video: Path, timeline: Path) -> None:
    video.write_bytes(_mp4_box(b"ftyp", b"isom\x00\x00\x02\x00isom") + _mp4_box(b"moov", b"vide-avc1-yuv420p") + _mp4_box(b"mdat", b"frames"))
    timeline.write_bytes(
        b"".join(
            json.dumps(
                {
                    "frame_index": frame,
                    "pts": frame,
                    "server_tick": 10 + frame,
                    "replay_tick": 20 + frame,
                    "scene_frame": f"segment-0:{10 + frame}",
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            + b"\n"
            for frame in range(2)
        )
    )


def _identity() -> BundleIdentity:
    return BundleIdentity(
        server_name="Test Server",
        server_instance_id=SERVER_INSTANCE_ID,
        player_name="RecorderPlayer",
        player_uuid=PLAYER_UUID,
        session_id="session-a",
        connection_id=CONNECTION_ID,
        started_at="2026-07-24T01:02:03Z",
        ended_at="2026-07-24T01:02:03.1Z",
        start_tick=10,
        end_tick=11,
        sensitivity="private",
        known_modality_gaps=("raw_input",),
    )


def _request(
    root: Path,
    *,
    replay_count: int = 1,
    with_render: bool = False,
) -> BundleRequest:
    root.mkdir(parents=True, exist_ok=True)
    actions = root / "actions.jsonl"
    scene = root / "scene.sqlite3"
    _write_actions(actions)
    _write_scene(scene)
    replays: list[ReplaySegment] = []
    for ordinal in range(replay_count):
        path = root / f"source-{ordinal}.zip"
        _write_replay(
            path,
            f"segment-{ordinal}".encode(),
            segment_id=f"segment-{ordinal}",
            segment_ordinal=ordinal,
        )
        replays.append(
            ReplaySegment(
                segment_id=f"segment-{ordinal}",
                source=_artifact(path),
                source_segment_ordinal=ordinal,
                start_server_tick=9 + ordinal,
                end_server_tick=(12 if ordinal == replay_count - 1 else 10 + ordinal),
                start_replay_tick=0,
                end_replay_tick=(3 if replay_count == 1 else (1 if ordinal == 0 else 2)),
            )
        )
    render: RenderAttachment | None = None
    if with_render:
        video = root / "fpv.mp4"
        timeline = root / "fpv.timeline.jsonl"
        _write_render(video, timeline)
        render = RenderAttachment(
            video=_artifact(video),
            timeline=_artifact(timeline),
            frame_count=2,
            width=640,
            height=360,
        )
    return BundleRequest(
        identity=_identity(),
        actions=_artifact(actions),
        scene=_artifact(scene),
        replay_segments=tuple(replays),
        render=render,
    )


def _validate_scene(path: Path) -> None:
    connection = sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True)
    try:
        ticks = tuple(row[0] for row in connection.execute("SELECT server_tick FROM frame_ticks"))
    finally:
        connection.close()
    if ticks != (10, 11):
        raise ValueError("incomplete frame coverage")


def _rewrite_zip(
    source: Path,
    output: Path,
    *,
    omit: frozenset[str] = frozenset(),
    replacements: dict[str, bytes] | None = None,
    additions: tuple[tuple[zipfile.ZipInfo | str, bytes], ...] = (),
) -> None:
    replacements = replacements or {}
    with (
        zipfile.ZipFile(source, "r") as original,
        zipfile.ZipFile(
            output,
            "w",
            allowZip64=True,
        ) as rewritten,
    ):
        for info in original.infolist():
            if info.filename in omit:
                continue
            data = replacements.get(info.filename, original.read(info))
            rewritten.writestr(info, data)
        for info, data in additions:
            rewritten.writestr(info, data)


def _mark_first_entry_encrypted(path: Path) -> None:
    data = bytearray(path.read_bytes())
    end_record = data.rfind(b"PK\x05\x06")
    if end_record < 0:
        raise AssertionError("test ZIP lacks an end record")
    central = struct.unpack_from("<I", data, end_record + 16)[0]
    if data[central : central + 4] != b"PK\x01\x02":
        raise AssertionError("test ZIP lacks a central header")
    local = struct.unpack_from("<I", data, central + 42)[0]
    if data[local : local + 4] != b"PK\x03\x04":
        raise AssertionError("test ZIP lacks local or central headers")
    local_flags = struct.unpack_from("<H", data, local + 6)[0] | 0x1
    central_flags = struct.unpack_from("<H", data, central + 8)[0] | 0x1
    struct.pack_into("<H", data, local + 6, local_flags)
    struct.pack_into("<H", data, central + 8, central_flags)
    path.write_bytes(data)


class PlayBundleRoundTripTest(unittest.TestCase):
    def test_publishes_and_reopens_a_bundle_with_exact_layout_and_storage(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            request = _request(root)

            published = publish_bundle(
                request,
                root / "artifacts",
                scene_validator=_validate_scene,
            )

            self.assertFalse(published.reused)
            self.assertEqual(
                (
                    "v1",
                    f"Test Server--{SERVER_INSTANCE_ID}",
                    "players",
                    f"RecorderPlayer--{PLAYER_UUID}",
                    "plays",
                    f"2026-07-24T01:02:03Z--{CONNECTION_ID}",
                    f"{published.bundle_id}.mcplay.zip",
                ),
                published.path.relative_to(root / "artifacts").parts,
            )
            with zipfile.ZipFile(published.path) as archive:
                infos = {info.filename: info for info in archive.infolist()}
                self.assertEqual(
                    {
                        "metadata.json",
                        "actions.jsonl",
                        "scene.sqlite3",
                        "replays/000000--segment-0.zip",
                    },
                    set(infos),
                )
                self.assertEqual(zipfile.ZIP_DEFLATED, infos["metadata.json"].compress_type)
                self.assertEqual(zipfile.ZIP_DEFLATED, infos["actions.jsonl"].compress_type)
                self.assertEqual(zipfile.ZIP_STORED, infos["scene.sqlite3"].compress_type)
                self.assertEqual(
                    zipfile.ZIP_STORED,
                    infos["replays/000000--segment-0.zip"].compress_type,
                )
                self.assertGreaterEqual(infos["scene.sqlite3"].extract_version, 45)
                self.assertEqual(
                    request.replay_segments[0].source.path.read_bytes(),
                    archive.read("replays/000000--segment-0.zip"),
                )

            with open_bundle(published.path, scene_validator=_validate_scene) as opened:
                self.assertEqual(published.bundle_id, opened.bundle_id)
                self.assertTrue(opened.actions_path.is_file())
                self.assertTrue(opened.scene_path.is_file())
                self.assertEqual(1, len(opened.replay_descriptors))
                extraction_root = opened.extraction_root
            self.assertFalse(extraction_root.exists())

    def test_identical_inputs_reuse_id_and_render_creates_a_revision(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            request = _request(root)
            artifacts = root / "artifacts"

            first = publish_bundle(request, artifacts, scene_validator=_validate_scene)
            second = publish_bundle(request, artifacts, scene_validator=_validate_scene)
            rendered = publish_bundle(
                _request(root / "rendered", with_render=True),
                artifacts,
                scene_validator=_validate_scene,
            )

            self.assertEqual(first.path, second.path)
            self.assertEqual(first.bundle_id, second.bundle_id)
            self.assertTrue(second.reused)
            self.assertNotEqual(first.bundle_id, rendered.bundle_id)
            with open_bundle(rendered.path, scene_validator=_validate_scene) as opened:
                self.assertIsNotNone(opened.fpv_path)
                self.assertIsNotNone(opened.timeline_path)
                self.assertEqual(2, opened.metadata["render"]["video"]["frame_count"])

    def test_no_render_bundle_has_no_render_entries_or_descriptor(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            request = _request(root)
            published = publish_bundle(request, root / "artifacts", scene_validator=_validate_scene)

            with zipfile.ZipFile(published.path) as archive:
                metadata = json.loads(archive.read("metadata.json"))
                self.assertIsNone(metadata["render"])
                self.assertFalse(any(name.startswith("renders/") for name in archive.namelist()))

    def test_binds_validator_frame_pts_but_keeps_generic_reader_optional(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            request = _request(root, with_render=True)
            published = publish_bundle(
                request,
                root / "artifacts",
                scene_validator=_validate_scene,
            )

            with open_bundle(published.path, scene_validator=_validate_scene):
                pass

            def mismatched_pts(_path: Path, _descriptor: object) -> dict[str, object]:
                return {"frame_pts": (0, 2)}

            with self.assertRaisesRegex(BundleError, "exactly match its MP4 frame"):
                open_bundle(
                    published.path,
                    scene_validator=_validate_scene,
                    render_validator=mismatched_pts,
                )

    def test_preserves_multiple_independently_rotated_replay_segments(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            request = _request(root, replay_count=2)

            published = publish_bundle(
                request,
                root / "artifacts",
                scene_validator=_validate_scene,
            )

            with zipfile.ZipFile(published.path) as archive:
                replay_names = [
                    "replays/000000--segment-0.zip",
                    "replays/000001--segment-1.zip",
                ]
                self.assertEqual(
                    [segment.source.path.read_bytes() for segment in request.replay_segments],
                    [archive.read(name) for name in replay_names],
                )
            with open_bundle(published.path, scene_validator=_validate_scene) as opened:
                self.assertEqual(
                    ("segment-0", "segment-1"),
                    tuple(item.segment_id for item in opened.replay_descriptors),
                )

    def test_preserves_a_contributing_replay_with_no_selected_frames(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            request = _request(root, replay_count=2)
            first, second = request.replay_segments
            request = replace(
                request,
                replay_segments=(
                    replace(
                        first,
                        start_server_tick=9,
                        end_server_tick=12,
                        start_replay_tick=0,
                        end_replay_tick=3,
                    ),
                    replace(
                        second,
                        start_server_tick=None,
                        end_server_tick=None,
                        start_replay_tick=None,
                        end_replay_tick=None,
                    ),
                ),
            )

            published = publish_bundle(
                request,
                root / "artifacts",
                scene_validator=_validate_scene,
            )

            with open_bundle(published.path, scene_validator=_validate_scene) as opened:
                descriptor = opened.replay_descriptors[1]
                self.assertEqual(1, descriptor.source_segment_ordinal)
                self.assertIsNone(descriptor.start_server_tick)
                self.assertIsNone(descriptor.end_server_tick)
                self.assertIsNone(descriptor.start_replay_tick)
                self.assertIsNone(descriptor.end_replay_tick)

    def test_failed_reader_revalidation_never_promotes_a_partial_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            artifacts = root / "artifacts"

            def reject_scene(_path: Path) -> None:
                raise ValueError("missing authoritative player states")

            with self.assertRaisesRegex(BundleError, "scene validator"):
                publish_bundle(
                    _request(root),
                    artifacts,
                    scene_validator=reject_scene,
                )

            self.assertEqual([], list(artifacts.rglob("*.mcplay.zip")))
            self.assertFalse(any(path.name.startswith(".minerec-play-bundle-stage-") for path in artifacts.iterdir()))

    def test_rejects_incomplete_reconstructed_control_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            request = _request(root)
            lines = request.actions.path.read_bytes().splitlines(keepends=True)
            request.actions.path.write_bytes(lines[0])
            request = replace(request, actions=_artifact(request.actions.path))

            with self.assertRaisesRegex(BundleError, "control_state for every tick"):
                publish_bundle(
                    request,
                    root / "artifacts",
                    scene_validator=_validate_scene,
                )

    def test_rejects_actions_outside_the_exact_dataset_v2_schema(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            request = _request(root)
            rows = [json.loads(line) for line in request.actions.path.read_bytes().splitlines()]
            rows[0]["source"]["event_sequence"] += 1
            request.actions.path.write_bytes(b"".join(json.dumps(row, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n" for row in rows))
            request = replace(request, actions=_artifact(request.actions.path))

            with self.assertRaisesRegex(BundleError, "source provenance disagrees"):
                publish_bundle(
                    request,
                    root / "artifacts",
                    scene_validator=_validate_scene,
                )

    def test_binds_scene_validator_result_to_bundle_identity_and_ticks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)

            def mismatched_scene(_path: Path) -> SimpleNamespace:
                return SimpleNamespace(
                    identity=SimpleNamespace(
                        session_id="other-session",
                        player_uuid=PLAYER_UUID,
                        connection_id=CONNECTION_ID,
                    ),
                    start_tick=10,
                    end_tick=11,
                    ticks=(10, 11),
                )

            with self.assertRaisesRegex(BundleError, "identity does not match"):
                publish_bundle(
                    _request(root),
                    root / "artifacts",
                    scene_validator=mismatched_scene,
                )

    def test_binds_scene_replay_provenance_to_bundle_descriptors(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            request = _request(root)
            source = request.replay_segments[0]

            def mismatched_scene(_path: Path) -> SimpleNamespace:
                return SimpleNamespace(
                    identity=SimpleNamespace(
                        session_id=request.identity.session_id,
                        player_uuid=request.identity.player_uuid,
                        connection_id=request.identity.connection_id,
                    ),
                    start_tick=10,
                    end_tick=11,
                    ticks=(10, 11),
                    source_replays=(
                        {
                            "segment_id": source.segment_id,
                            "segment_ordinal": source.source_segment_ordinal,
                            "path": "replays/000000--segment-0.zip",
                            "sha256": "0" * 64,
                            "size_bytes": source.source.size_bytes,
                            "format": "flashback",
                        },
                    ),
                )

            with self.assertRaisesRegex(BundleError, "scene replay provenance"):
                publish_bundle(
                    request,
                    root / "artifacts",
                    scene_validator=mismatched_scene,
                )

    def test_rejects_nested_replay_identity_mismatch_before_publication(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            request = _request(root)
            replay = request.replay_segments[0]
            _write_replay(
                replay.source.path,
                b"segment-0",
                segment_id=replay.segment_id,
                segment_ordinal=replay.source_segment_ordinal,
                session_id="another-session",
            )
            request = replace(
                request,
                replay_segments=(replace(replay, source=_artifact(replay.source.path)),),
            )

            with self.assertRaisesRegex(BundleError, "identity does not match"):
                publish_bundle(
                    request,
                    root / "artifacts",
                    scene_validator=_validate_scene,
                )
            self.assertEqual([], list((root / "artifacts").rglob("*.mcplay.zip")))


class PlayBundleAdversarialTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        request = _request(self.root)
        self.valid = publish_bundle(
            request,
            self.root / "artifacts",
            scene_validator=_validate_scene,
        ).path

    def test_rejects_hash_tampering(self) -> None:
        tampered = self.root / "tampered.zip"
        with zipfile.ZipFile(self.valid) as archive:
            actions = archive.read("actions.jsonl").replace(b"true", b"fals", 1)
        _rewrite_zip(self.valid, tampered, replacements={"actions.jsonl": actions})

        with self.assertRaisesRegex(BundleError, "SHA-256"):
            open_bundle(tampered)

    def test_rejects_traversal_absolute_backslash_and_case_collisions(self) -> None:
        names = (
            "../escape",
            "/absolute",
            "C:/absolute",
            "replays\\escape.zip",
            "ACTIONS.JSONL",
        )
        for index, name in enumerate(names):
            with self.subTest(name=name):
                adversarial = self.root / f"bad-name-{index}.zip"
                _rewrite_zip(self.valid, adversarial, additions=((name, b"bad"),))
                with self.assertRaises(BundleError):
                    open_bundle(adversarial)

    def test_rejects_duplicate_entries(self) -> None:
        duplicate = self.root / "duplicate.zip"
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            _rewrite_zip(
                self.valid,
                duplicate,
                additions=(("actions.jsonl", b"duplicate"),),
            )

        with self.assertRaisesRegex(BundleError, "duplicate"):
            open_bundle(duplicate)

    def test_rejects_an_encrypted_entry(self) -> None:
        encrypted = self.root / "encrypted.zip"
        encrypted.write_bytes(self.valid.read_bytes())
        _mark_first_entry_encrypted(encrypted)

        with self.assertRaisesRegex(BundleError, "encrypted"):
            open_bundle(encrypted)

    def test_rejects_unicode_casefold_collisions(self) -> None:
        collision = self.root / "unicode-collision.zip"
        _rewrite_zip(
            self.valid,
            collision,
            additions=(("extra/straße", b"one"), ("extra/STRASSE", b"two")),
        )

        with self.assertRaisesRegex(BundleError, "colliding"):
            open_bundle(collision)

    def test_rejects_symlink_and_device_entries(self) -> None:
        for index, file_type in enumerate((stat.S_IFLNK, stat.S_IFCHR)):
            with self.subTest(file_type=file_type):
                info = zipfile.ZipInfo(f"unsafe-{index}")
                info.create_system = 3
                info.external_attr = (file_type | 0o600) << 16
                adversarial = self.root / f"unsafe-{index}.zip"
                _rewrite_zip(self.valid, adversarial, additions=((info, b"target"),))
                with self.assertRaisesRegex(BundleError, "non-regular"):
                    open_bundle(adversarial)

    def test_rejects_undeclared_and_missing_entries(self) -> None:
        undeclared = self.root / "undeclared.zip"
        missing = self.root / "missing.zip"
        _rewrite_zip(self.valid, undeclared, additions=(("extra.bin", b"extra"),))
        _rewrite_zip(self.valid, missing, omit=frozenset({"scene.sqlite3"}))

        with self.assertRaisesRegex(BundleError, "undeclared or missing"):
            open_bundle(undeclared)
        with self.assertRaisesRegex(BundleError, "undeclared or missing"):
            open_bundle(missing)

    def test_rejects_a_compression_bomb_before_extraction(self) -> None:
        bomb = self.root / "bomb.zip"
        info = zipfile.ZipInfo("bomb.json")
        info.compress_type = zipfile.ZIP_DEFLATED
        _rewrite_zip(self.valid, bomb, additions=((info, b"0" * 1024 * 1024),))
        limits = replace(BundleLimits(), max_compression_ratio=10)

        with self.assertRaisesRegex(BundleError, "over-expanded"):
            open_bundle(bomb, limits=limits)

    def test_rejects_entry_count_before_constructing_zipfile(self) -> None:
        limits = replace(BundleLimits(), max_entries=3)

        with mock.patch(
            "minerec.processing.bundle.reader.zipfile.ZipFile",
            side_effect=AssertionError("ZipFile must not allocate an oversized directory"),
        ):
            with self.assertRaisesRegex(BundleError, "entry count"):
                open_bundle(self.valid, limits=limits)

    def test_preflights_nested_replay_count_before_constructing_its_zipfile(self) -> None:
        request = _request(self.root / "many-nested-entries")
        replay = request.replay_segments[0]
        _write_replay(
            replay.source.path,
            b"segment-0",
            segment_id=replay.segment_id,
            segment_ordinal=replay.source_segment_ordinal,
            extra_entries=62,
        )
        request = replace(
            request,
            replay_segments=(replace(replay, source=_artifact(replay.source.path)),),
        )
        bundle = publish_bundle(
            request,
            self.root / "many-nested-artifacts",
            scene_validator=_validate_scene,
        ).path
        limits = replace(BundleLimits(), max_entries=4)
        real_zip_file = zipfile.ZipFile
        constructed = 0

        def reject_second_zipfile(*args: object, **kwargs: object) -> zipfile.ZipFile:
            nonlocal constructed
            constructed += 1
            if constructed > 1:
                raise AssertionError("oversized nested directory reached ZipFile")
            return cast(Any, real_zip_file)(*args, **kwargs)

        with mock.patch(
            "minerec.processing.bundle.reader.zipfile.ZipFile",
            side_effect=reject_second_zipfile,
        ):
            with self.assertRaisesRegex(BundleError, "entry count"):
                open_bundle(bundle, limits=limits)
        self.assertEqual(1, constructed)

    def test_rejects_nested_replay_cumulative_uncompressed_size(self) -> None:
        replay = self.root / "oversized-nested.zip"
        _write_replay(
            replay,
            b"segment-0",
            segment_id="segment-0",
            segment_ordinal=0,
            nested_uncompressed_bytes=4096,
        )
        with zipfile.ZipFile(self.valid) as archive:
            metadata = validate_metadata(archive.read("metadata.json"))
        limits = replace(
            BundleLimits(),
            max_total_uncompressed_bytes=1024,
            max_compression_ratio=10_000,
        )

        with self.assertRaisesRegex(BundleError, "uncompressed byte total"):
            _validate_flashback_replay(
                replay,
                limits,
                metadata,
                metadata.replays[0],
            )

    def test_rejects_truncated_upload_without_disturbing_previous_handle(self) -> None:
        opened = open_bundle(self.valid, scene_validator=_validate_scene)
        self.addCleanup(opened.close)
        truncated = self.root / "truncated.zip"
        truncated.write_bytes(self.valid.read_bytes()[:64])

        with self.assertRaises(BundleError):
            open_bundle(truncated)
        self.assertTrue(opened.scene_path.is_file())

    def test_insufficient_extraction_disk_cleans_the_failed_import(self) -> None:
        import_root = self.root / "imports"
        import_root.mkdir()
        real_open = os.open

        def fail_created_file(
            path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
            flags: int,
            mode: int = 0o777,
            *,
            dir_fd: int | None = None,
        ) -> int:
            if flags & os.O_CREAT:
                raise OSError(errno.ENOSPC, "test disk full")
            return real_open(path, flags, mode, dir_fd=dir_fd)

        with mock.patch(
            "minerec.processing.bundle.reader.os.open",
            side_effect=fail_created_file,
        ):
            with self.assertRaisesRegex(BundleError, "safely extracted"):
                open_bundle(self.valid, temp_root=import_root)
        self.assertEqual([], list(import_root.iterdir()))

    def test_rejects_metadata_identity_tampering_even_when_json_is_canonical(self) -> None:
        changed = self.root / "identity-tampered.zip"
        with zipfile.ZipFile(self.valid) as archive:
            metadata = json.loads(archive.read("metadata.json"))
        metadata["connection"]["id"] = "another-connection"
        _rewrite_zip(
            self.valid,
            changed,
            replacements={"metadata.json": canonical_json_bytes(metadata)},
        )

        with self.assertRaisesRegex(BundleError, "bundle id"):
            open_bundle(changed)

    def test_rejects_metadata_that_declares_an_inventory_hash_mismatch(self) -> None:
        changed = self.root / "manifest-tampered.zip"
        with zipfile.ZipFile(self.valid) as archive:
            metadata = json.loads(archive.read("metadata.json"))
        metadata["inventory"][0]["sha256"] = "0" * 64
        metadata["bundle_id"] = deterministic_bundle_id(metadata)
        _rewrite_zip(
            self.valid,
            changed,
            replacements={"metadata.json": canonical_json_bytes(metadata)},
        )

        with self.assertRaises(BundleError):
            open_bundle(changed)
