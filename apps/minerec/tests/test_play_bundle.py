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
            "server_tick": tick,
            "sequence": tick,
            "player_uuid": PLAYER_UUID,
            "connection_id": CONNECTION_ID,
            "action_type": "control_state",
            "payload": {"forward": tick == 10},
        }
        for tick in (10, 11)
    ]
    path.write_bytes(b"".join(json.dumps(row, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n" for row in rows))


def _write_replay(path: Path, payload: bytes) -> None:
    with zipfile.ZipFile(path, "w", allowZip64=True) as archive:
        archive.writestr("metadata.json", "{}")
        archive.writestr("arcade_replay_meta.json", "{}")
        archive.writestr("chunks/c0.flashback", payload)


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
                    "scene_frame": frame,
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
        _write_replay(path, f"segment-{ordinal}".encode())
        replays.append(
            ReplaySegment(
                segment_id=f"segment-{ordinal}",
                source=_artifact(path),
                start_server_tick=9 + ordinal,
                end_server_tick=(12 if ordinal == replay_count - 1 else 10 + ordinal),
                start_replay_tick=0,
                end_replay_tick=3,
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
