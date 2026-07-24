from __future__ import annotations

import hashlib
import json
import os
import stat
import struct
import tempfile
import unicodedata
import uuid
import zipfile
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, Iterator, Mapping, cast

from minerec.processing.bundle.contract import (
    OPTIONAL_RENDER_ENTRY_NAMES,
    ROLE_ACTIONS,
    ROLE_REPLAY,
    ROLE_SCENE,
    ROLE_TIMELINE,
    ROLE_VIDEO,
    InventoryEntry,
    ValidatedMetadata,
    validate_limits,
    validate_metadata,
)
from minerec.processing.bundle.model import (
    DEFAULT_BUNDLE_LIMITS,
    BundleError,
    BundleIdentity,
    BundleInput,
    BundleLimits,
    OpenedBundle,
    PathValidator,
    RenderValidator,
    ReplayDescriptor,
)

_COPY_CHUNK_BYTES = 1024 * 1024
_JSON_COMPRESSION = zipfile.ZIP_DEFLATED
_BINARY_COMPRESSION = zipfile.ZIP_STORED
_EOCD_SIGNATURE = b"PK\x05\x06"
_ZIP64_EOCD_SIGNATURE = b"PK\x06\x06"
_ZIP64_LOCATOR_SIGNATURE = b"PK\x06\x07"
_EOCD_STRUCT = struct.Struct("<4s4H2LH")
_ZIP64_EOCD_STRUCT = struct.Struct("<4sQ2H2L4Q")
_ZIP64_LOCATOR_STRUCT = struct.Struct("<4sLQL")
_ACTION_FIELDS = frozenset(
    {
        "schema_version",
        "session_id",
        "epoch_index",
        "server_tick",
        "sequence",
        "apply_sequence",
        "player_uuid",
        "connection_id",
        "action_type",
        "applied",
        "payload",
        "source",
    }
)
_ACTION_SOURCE_FIELDS = frozenset(
    {
        "epoch_index",
        "event_sequence",
        "events_sha256",
        "epoch_manifest_sha256",
        "record_type",
        "recorded_at_ns",
        "arrival_sequence",
    }
)


def _canonical_archive_name(name: str) -> str:
    if not name or "\\" in name or "\x00" in name or name.startswith("/"):
        raise BundleError("bundle contains a non-canonical ZIP entry name")
    if unicodedata.normalize("NFC", name) != name:
        raise BundleError("bundle contains a non-NFC ZIP entry name")
    if any(ord(character) < 32 or ord(character) == 127 for character in name):
        raise BundleError("bundle contains a control character in a ZIP entry name")
    path = PurePosixPath(name)
    parts = name.split("/")
    drive_prefixed = len(parts[0]) == 2 and parts[0][0].isalpha() and parts[0][1] == ":"
    if path.is_absolute() or drive_prefixed or any(part in {"", ".", ".."} for part in parts):
        raise BundleError("bundle contains an absolute or traversing ZIP entry name")
    return name


def _entry_is_regular(info: zipfile.ZipInfo) -> bool:
    if info.is_dir() or info.filename.endswith("/"):
        return False
    mode = info.external_attr >> 16
    file_type = stat.S_IFMT(mode)
    return file_type in {0, stat.S_IFREG}


def _entry_ratio(info: zipfile.ZipInfo) -> float:
    if info.file_size == 0:
        return 1.0
    if info.compress_size <= 0:
        return float("inf")
    return info.file_size / info.compress_size


def _archive_size(handle: BinaryIO) -> int:
    try:
        original = handle.tell()
        handle.seek(0, os.SEEK_END)
        size = handle.tell()
        handle.seek(original)
    except (AttributeError, OSError) as exc:
        raise BundleError("bundle input must be a seekable binary file") from exc
    if not isinstance(size, int) or size < 0:
        raise BundleError("bundle input has an invalid byte size")
    return size


def _preflight_zip_directory(
    handle: BinaryIO,
    archive_size: int,
    limits: BundleLimits,
) -> None:
    """Bound the central directory before ``zipfile`` allocates its entry list."""

    tail_size = min(archive_size, _EOCD_STRUCT.size + 65535)
    try:
        handle.seek(archive_size - tail_size)
        tail = handle.read(tail_size)
    except OSError as exc:
        raise BundleError("play bundle ZIP directory cannot be inspected") from exc
    if len(tail) != tail_size:
        raise BundleError("play bundle ZIP directory is truncated")
    offset = tail.rfind(_EOCD_SIGNATURE)
    end_record: tuple[bytes, int, int, int, int, int, int, int] | None = None
    while offset >= 0:
        if offset + _EOCD_STRUCT.size <= len(tail):
            candidate = _EOCD_STRUCT.unpack_from(tail, offset)
            if offset + _EOCD_STRUCT.size + candidate[-1] == len(tail):
                end_record = candidate
                break
        offset = tail.rfind(_EOCD_SIGNATURE, 0, offset)
    if end_record is None:
        raise BundleError("play bundle ZIP end record is missing")
    (
        _signature,
        disk_number,
        central_disk,
        entries_on_disk,
        total_entries,
        central_size,
        central_offset,
        _comment_size,
    ) = end_record
    eocd_offset = archive_size - tail_size + offset
    if disk_number != 0 or central_disk != 0 or entries_on_disk != total_entries:
        raise BundleError("multi-disk play bundle ZIPs are not supported")

    saturated = total_entries == 0xFFFF or central_size == 0xFFFFFFFF or central_offset == 0xFFFFFFFF
    directory_end = eocd_offset
    if saturated:
        locator_offset = eocd_offset - _ZIP64_LOCATOR_STRUCT.size
        if locator_offset < 0:
            raise BundleError("play bundle ZIP64 locator is missing")
        handle.seek(locator_offset)
        locator_data = handle.read(_ZIP64_LOCATOR_STRUCT.size)
        if len(locator_data) != _ZIP64_LOCATOR_STRUCT.size:
            raise BundleError("play bundle ZIP64 locator is truncated")
        locator_signature, locator_disk, zip64_offset, disk_count = _ZIP64_LOCATOR_STRUCT.unpack(locator_data)
        if locator_signature != _ZIP64_LOCATOR_SIGNATURE or locator_disk != 0 or disk_count != 1 or zip64_offset > locator_offset - _ZIP64_EOCD_STRUCT.size:
            raise BundleError("play bundle ZIP64 locator is invalid")
        handle.seek(zip64_offset)
        zip64_data = handle.read(_ZIP64_EOCD_STRUCT.size)
        if len(zip64_data) != _ZIP64_EOCD_STRUCT.size:
            raise BundleError("play bundle ZIP64 end record is truncated")
        (
            zip64_signature,
            zip64_record_size,
            _made_by,
            _needed,
            zip64_disk,
            zip64_central_disk,
            zip64_entries_on_disk,
            total_entries,
            central_size,
            central_offset,
        ) = _ZIP64_EOCD_STRUCT.unpack(zip64_data)
        if zip64_signature != _ZIP64_EOCD_SIGNATURE or zip64_record_size < 44 or zip64_disk != 0 or zip64_central_disk != 0 or zip64_entries_on_disk != total_entries:
            raise BundleError("play bundle ZIP64 end record is invalid")
        directory_end = zip64_offset

    if total_entries <= 0 or total_entries > limits.max_entries:
        raise BundleError("bundle ZIP entry count is outside the allowed range")
    if central_size > limits.max_central_directory_bytes:
        raise BundleError("bundle ZIP central directory exceeds the byte limit")
    if central_offset > directory_end or central_size > directory_end - central_offset:
        raise BundleError("bundle ZIP central directory range is invalid")
    handle.seek(0)


@contextmanager
def _open_input(source: BundleInput, limits: BundleLimits) -> Iterator[tuple[BinaryIO, Path | None]]:
    if isinstance(source, (str, Path)):
        path = Path(source)
        try:
            before = path.lstat()
        except OSError as exc:
            raise BundleError(f"cannot inspect play bundle: {path}") from exc
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
            raise BundleError("play bundle input must be a non-symlinked regular file")
        if before.st_size > limits.max_archive_bytes:
            raise BundleError("play bundle exceeds the archive byte limit")
        try:
            handle = path.open("rb")
        except OSError as exc:
            raise BundleError(f"cannot open play bundle: {path}") from exc
        try:
            opened = os.fstat(handle.fileno())
            if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
                raise BundleError("play bundle changed while it was being opened")
            yield handle, path.resolve()
            after_open = os.fstat(handle.fileno())
            after_path = path.lstat()
            identities = (
                (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns),
                (
                    after_open.st_dev,
                    after_open.st_ino,
                    after_open.st_size,
                    after_open.st_mtime_ns,
                ),
                (
                    after_path.st_dev,
                    after_path.st_ino,
                    after_path.st_size,
                    after_path.st_mtime_ns,
                ),
            )
            if identities[0] != identities[1] or identities[0] != identities[2]:
                raise BundleError("play bundle changed while it was being validated")
        finally:
            handle.close()
        return

    if not hasattr(source, "read") or not hasattr(source, "seek"):
        raise BundleError("play bundle input must be a path or seekable binary file")
    if _archive_size(source) > limits.max_archive_bytes:
        raise BundleError("play bundle exceeds the archive byte limit")
    yield source, None


def _inspect_entries(
    archive: zipfile.ZipFile,
    limits: BundleLimits,
) -> dict[str, zipfile.ZipInfo]:
    entries = archive.infolist()
    if not entries or len(entries) > limits.max_entries:
        raise BundleError("bundle ZIP entry count is outside the allowed range")
    by_name: dict[str, zipfile.ZipInfo] = {}
    collision_names: dict[str, str] = {}
    total_size = 0
    for info in entries:
        name = _canonical_archive_name(info.filename)
        if name in by_name:
            raise BundleError("bundle contains a duplicate ZIP entry")
        collision_key = unicodedata.normalize("NFC", name).casefold()
        collided = collision_names.get(collision_key)
        if collided is not None and collided != name:
            raise BundleError("bundle contains Unicode- or case-colliding ZIP entries")
        collision_names[collision_key] = name
        by_name[name] = info
        if info.flag_bits & 0x1:
            raise BundleError("bundle contains an encrypted ZIP entry")
        if not _entry_is_regular(info):
            raise BundleError("bundle contains a directory, link, device, or other non-regular entry")
        if info.compress_type not in {_JSON_COMPRESSION, _BINARY_COMPRESSION}:
            raise BundleError("bundle contains an unsupported compression method")
        if info.file_size < 0 or info.compress_size < 0:
            raise BundleError("bundle contains an entry with an invalid size")
        if _entry_ratio(info) > limits.max_compression_ratio:
            raise BundleError("bundle contains an over-expanded ZIP entry")
        total_size += info.file_size
        if total_size > limits.max_total_uncompressed_bytes:
            raise BundleError("bundle uncompressed byte total exceeds the limit")
    if set(by_name) == {"metadata.json"}:
        raise BundleError("bundle is missing all required artifacts")
    return by_name


def _read_bounded_entry(
    archive: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    maximum: int,
) -> bytes:
    if info.file_size > maximum:
        raise BundleError(f"bundle entry {info.filename!r} exceeds its byte limit")
    try:
        with archive.open(info, "r") as source:
            chunks: list[bytes] = []
            observed = 0
            while True:
                chunk = source.read(min(_COPY_CHUNK_BYTES, maximum + 1 - observed))
                if not chunk:
                    break
                chunks.append(chunk)
                observed += len(chunk)
                if observed > maximum:
                    raise BundleError(f"bundle entry {info.filename!r} exceeds its byte limit")
    except BundleError:
        raise
    except (EOFError, NotImplementedError, OSError, RuntimeError, zipfile.BadZipFile) as exc:
        raise BundleError(f"bundle entry {info.filename!r} cannot be decoded") from exc
    data = b"".join(chunks)
    if len(data) != info.file_size:
        raise BundleError(f"bundle entry {info.filename!r} has inconsistent sizes")
    return data


def _entry_limit(entry: InventoryEntry, limits: BundleLimits) -> int:
    return {
        ROLE_ACTIONS: limits.max_actions_bytes,
        ROLE_SCENE: limits.max_scene_bytes,
        ROLE_REPLAY: limits.max_replay_bytes,
        ROLE_VIDEO: limits.max_video_bytes,
        ROLE_TIMELINE: limits.max_timeline_bytes,
    }[entry.media_role]


def _expected_compression(entry: InventoryEntry) -> int:
    return _JSON_COMPRESSION if entry.media_role in {ROLE_ACTIONS, ROLE_TIMELINE} else _BINARY_COMPRESSION


def _cross_check_archive(
    entries: Mapping[str, zipfile.ZipInfo],
    metadata: ValidatedMetadata,
    limits: BundleLimits,
) -> None:
    expected_names = {"metadata.json", *(item.path for item in metadata.inventory)}
    if set(entries) != expected_names:
        raise BundleError("bundle contains an undeclared or missing ZIP entry")
    metadata_info = entries["metadata.json"]
    if metadata_info.compress_type != _JSON_COMPRESSION:
        raise BundleError("metadata.json must be deflated")
    for item in metadata.inventory:
        info = entries[item.path]
        if info.file_size != item.size_bytes:
            raise BundleError(f"bundle entry {item.path!r} size does not match the inventory")
        if item.size_bytes > _entry_limit(item, limits):
            raise BundleError(f"bundle entry {item.path!r} exceeds its media-role byte limit")
        if info.compress_type != _expected_compression(item):
            raise BundleError(f"bundle entry {item.path!r} uses the wrong compression method")


def _safe_destination(root: Path, name: str) -> Path:
    parts = PurePosixPath(name).parts
    destination = root.joinpath(*parts)
    current = root
    for part in parts[:-1]:
        current = current / part
        try:
            current.mkdir(mode=0o700)
        except FileExistsError:
            if current.is_symlink() or not current.is_dir():
                raise BundleError("bundle extraction path is not an owned directory") from None
    return destination


def _extract_verified_entry(
    archive: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    entry: InventoryEntry,
    root: Path,
) -> Path:
    destination = _safe_destination(root, entry.path)
    digest = hashlib.sha256()
    observed = 0
    try:
        descriptor = os.open(
            destination,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0),
            0o600,
        )
        with os.fdopen(descriptor, "wb") as target, archive.open(info, "r") as source:
            while chunk := source.read(_COPY_CHUNK_BYTES):
                observed += len(chunk)
                if observed > entry.size_bytes:
                    raise BundleError(f"bundle entry {entry.path!r} expands past its declared size")
                digest.update(chunk)
                target.write(chunk)
            target.flush()
            os.fsync(target.fileno())
    except BundleError:
        raise
    except (EOFError, NotImplementedError, OSError, RuntimeError, zipfile.BadZipFile) as exc:
        raise BundleError(f"bundle entry {entry.path!r} cannot be safely extracted") from exc
    if observed != entry.size_bytes or digest.hexdigest() != entry.sha256:
        raise BundleError(f"bundle entry {entry.path!r} failed its size or SHA-256 check")
    return destination


def _iter_bounded_lines(path: Path, maximum_line_bytes: int) -> Iterator[tuple[int, bytes]]:
    with path.open("rb") as handle:
        line_number = 0
        while True:
            line = handle.readline(maximum_line_bytes + 1)
            if not line:
                return
            line_number += 1
            if len(line) > maximum_line_bytes:
                raise BundleError(f"{path.name} line {line_number} exceeds the byte limit")
            yield line_number, line


def _action_int(value: object, description: str, *, nullable: bool = False) -> int | None:
    if value is None and nullable:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise BundleError(f"{description} must be a non-negative integer")
    return value


def _action_sha256(value: object, description: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise BundleError(f"{description} must be a lowercase SHA-256 digest")
    return value


def _action_integer(value: object, description: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise BundleError(f"{description} must be an integer")
    return value


def _validate_actions(
    path: Path,
    identity: BundleIdentity,
    maximum_line_bytes: int,
    *,
    expected_source_epochs: Mapping[int, tuple[str, str]] | None = None,
) -> int:
    previous_tick: int | None = None
    previous_sequence: int | None = None
    previous_apply_sequence: int | None = None
    expected_control_tick = identity.start_tick
    observed_records = 0
    try:
        for line_number, line in _iter_bounded_lines(path, maximum_line_bytes):
            if not line.endswith(b"\n"):
                raise BundleError("actions.jsonl must end every record with a newline")
            if not line.strip():
                raise BundleError("actions.jsonl contains a blank record")
            try:
                value = json.loads(
                    line,
                    parse_constant=lambda token: (_ for _ in ()).throw(ValueError(token)),
                )
            except (ValueError, UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
                raise BundleError(f"actions.jsonl line {line_number} is invalid JSON") from exc
            if not isinstance(value, dict):
                raise BundleError(f"actions.jsonl line {line_number} is not an object")
            if set(value) != _ACTION_FIELDS or value.get("schema_version") != 2:
                raise BundleError(f"actions.jsonl line {line_number} does not match the Dataset V2 action schema")
            tick = _action_int(value.get("server_tick"), f"actions.jsonl line {line_number} server_tick")
            assert tick is not None
            if not identity.start_tick <= tick <= identity.end_tick:
                raise BundleError(f"actions.jsonl line {line_number} is outside the connection tick range")
            if previous_tick is not None and tick < previous_tick:
                raise BundleError("actions.jsonl records are not ordered by server tick")
            previous_tick = tick

            sequence = _action_int(value.get("sequence"), f"actions.jsonl line {line_number} sequence")
            assert sequence is not None
            if previous_sequence is not None and sequence <= previous_sequence:
                raise BundleError("actions.jsonl source sequences must be strictly increasing")
            previous_sequence = sequence
            epoch_index = _action_int(value.get("epoch_index"), f"actions.jsonl line {line_number} epoch_index")
            assert epoch_index is not None
            apply_sequence = _action_int(
                value.get("apply_sequence"),
                f"actions.jsonl line {line_number} apply_sequence",
                nullable=True,
            )
            if apply_sequence is not None:
                if previous_apply_sequence is not None and apply_sequence <= previous_apply_sequence:
                    raise BundleError("actions.jsonl apply sequences must be strictly increasing")
                previous_apply_sequence = apply_sequence

            action_type = value.get("action_type")
            if not isinstance(action_type, str) or not action_type or len(action_type) > 255:
                raise BundleError(f"actions.jsonl line {line_number} has invalid action_type")
            if value.get("applied") is not True or not isinstance(value.get("payload"), dict):
                raise BundleError(f"actions.jsonl line {line_number} must contain one applied object payload")
            source = value.get("source")
            if not isinstance(source, dict) or set(source) != _ACTION_SOURCE_FIELDS:
                raise BundleError(f"actions.jsonl line {line_number} has invalid source provenance")
            if source.get("epoch_index") != epoch_index or source.get("event_sequence") != sequence:
                raise BundleError(f"actions.jsonl line {line_number} source provenance disagrees with its envelope")
            events_sha256 = _action_sha256(source.get("events_sha256"), f"actions.jsonl line {line_number} events hash")
            manifest_sha256 = _action_sha256(
                source.get("epoch_manifest_sha256"),
                f"actions.jsonl line {line_number} epoch manifest hash",
            )
            if expected_source_epochs is not None and expected_source_epochs.get(epoch_index) != (events_sha256, manifest_sha256):
                raise BundleError(f"actions.jsonl line {line_number} does not match Dataset V2 sealed epoch provenance")
            _action_integer(source.get("recorded_at_ns"), f"actions.jsonl line {line_number} recorded_at_ns")
            _action_int(
                source.get("arrival_sequence"),
                f"actions.jsonl line {line_number} arrival_sequence",
                nullable=True,
            )
            record_type = source.get("record_type")
            if record_type not in {"control_state", "packet_apply", "action"}:
                raise BundleError(f"actions.jsonl line {line_number} has unsupported source record_type")
            if record_type == "packet_apply" and apply_sequence is None:
                raise BundleError(f"actions.jsonl line {line_number} packet_apply lacks apply_sequence")
            if record_type != "packet_apply" and apply_sequence is not None:
                raise BundleError(f"actions.jsonl line {line_number} has an unexpected apply_sequence")

            if action_type == "control_state":
                if record_type != "control_state":
                    raise BundleError("actions.jsonl control_state has mismatched source provenance")
                if tick != expected_control_tick:
                    raise BundleError("actions.jsonl must contain exactly one ordered control_state for every tick")
                payload = value["payload"]
                if payload.get("player_uuid") != identity.player_uuid or payload.get("connection_id") != identity.connection_id:
                    raise BundleError("actions.jsonl control_state payload identity does not match the bundle")
                expected_control_tick += 1
            elif record_type == "control_state":
                raise BundleError("actions.jsonl control_state source has a mismatched action_type")
            for key, expected in (
                ("session_id", identity.session_id),
                ("player_uuid", identity.player_uuid),
                ("connection_id", identity.connection_id),
            ):
                if value.get(key) != expected:
                    raise BundleError(f"actions.jsonl line {line_number} has mismatched {key}")
            observed_records += 1
        if expected_control_tick != identity.end_tick + 1:
            raise BundleError("actions.jsonl must contain exactly one ordered control_state for every tick")
    except BundleError:
        raise
    except OSError as exc:
        raise BundleError("actions.jsonl cannot be read after extraction") from exc
    return observed_records


def validate_reconstructed_actions(
    path: Path,
    identity: BundleIdentity,
    *,
    expected_source_epochs: Mapping[int, tuple[str, str]] | None = None,
    maximum_line_bytes: int = DEFAULT_BUNDLE_LIMITS.max_json_line_bytes,
) -> int:
    """Validate the immutable Dataset V2 action contract and its provenance."""

    return _validate_actions(
        path,
        identity,
        maximum_line_bytes,
        expected_source_epochs=expected_source_epochs,
    )


def _validate_scene_header(path: Path) -> None:
    try:
        if path.stat().st_size < 100:
            raise BundleError("scene.sqlite3 is too small to be a SQLite database")
        with path.open("rb") as handle:
            if handle.read(16) != b"SQLite format 3\x00":
                raise BundleError("scene.sqlite3 does not have a SQLite database header")
    except BundleError:
        raise
    except OSError as exc:
        raise BundleError("scene.sqlite3 cannot be read after extraction") from exc


def _replay_uuid(value: object, description: str) -> str:
    if not isinstance(value, str):
        raise BundleError(f"nested replay {description} is not a canonical UUID")
    try:
        canonical = str(uuid.UUID(value))
    except ValueError as exc:
        raise BundleError(f"nested replay {description} is not a canonical UUID") from exc
    if canonical != value:
        raise BundleError(f"nested replay {description} is not a canonical UUID")
    return value


def _validate_flashback_replay(
    path: Path,
    limits: BundleLimits,
    metadata: ValidatedMetadata,
    descriptor: Mapping[str, Any],
) -> None:
    try:
        with path.open("rb") as source:
            replay_size = _archive_size(source)
            replay_limits = replace(
                limits,
                max_entries=min(limits.max_entries * 16, 65536),
            )
            _preflight_zip_directory(source, replay_size, replay_limits)
        with zipfile.ZipFile(path, "r") as replay:
            entries = replay.infolist()
            if not entries or len(entries) > min(limits.max_entries * 16, 65536):
                raise BundleError("nested replay ZIP entry count is outside the allowed range")
            names: set[str] = set()
            collision_names: dict[str, str] = {}
            has_metadata = False
            has_flashback = False
            recorder_info: zipfile.ZipInfo | None = None
            total_uncompressed_bytes = 0
            for info in entries:
                name = _canonical_archive_name(info.filename.rstrip("/"))
                if name in names:
                    raise BundleError("nested replay ZIP contains a duplicate entry")
                names.add(name)
                collision_key = unicodedata.normalize("NFC", name).casefold()
                collided = collision_names.get(collision_key)
                if collided is not None and collided != name:
                    raise BundleError("nested replay ZIP contains Unicode- or case-colliding entries")
                collision_names[collision_key] = name
                if info.flag_bits & 0x1 or not _entry_is_regular(info):
                    raise BundleError("nested replay ZIP contains an unsafe entry")
                if info.compress_type not in {_JSON_COMPRESSION, _BINARY_COMPRESSION}:
                    raise BundleError("nested replay ZIP contains an unsupported compression method")
                if info.file_size < 0 or info.compress_size < 0:
                    raise BundleError("nested replay ZIP contains an entry with an invalid size")
                if _entry_ratio(info) > limits.max_compression_ratio:
                    raise BundleError("nested replay ZIP contains an over-expanded entry")
                total_uncompressed_bytes += info.file_size
                if total_uncompressed_bytes > limits.max_total_uncompressed_bytes:
                    raise BundleError("nested replay ZIP uncompressed byte total exceeds the limit")
                if name == "metadata.json":
                    has_metadata = True
                if name.endswith(".flashback"):
                    has_flashback = True
                if name == "arcade_replay_meta.json":
                    recorder_info = info
            if not has_metadata or not has_flashback:
                raise BundleError("replay segment is not a complete Flashback ZIP")
            if recorder_info is None:
                raise BundleError("replay segment lacks arcade_replay_meta.json")
            raw_metadata = _read_bounded_entry(replay, recorder_info, 1024 * 1024)
            try:
                recorder_metadata = json.loads(
                    raw_metadata,
                    parse_constant=lambda token: (_ for _ in ()).throw(ValueError(token)),
                )
            except (ValueError, UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
                raise BundleError("replay arcade_replay_meta.json is invalid JSON") from exc
            mc_recorder = recorder_metadata.get("mc_recorder") if isinstance(recorder_metadata, dict) else None
            if not isinstance(mc_recorder, dict):
                raise BundleError("replay lacks exact mc_recorder segment identity")
            if mc_recorder.get("schema_version") != 3:
                raise BundleError("replay mc_recorder schema_version is not scene-capable v3")
            if mc_recorder.get("hotbar_snapshot_contract") != "item_stack_copy_v1" or mc_recorder.get("flashback_capture_contract") != "client_visible_scene_v1":
                raise BundleError("replay capture contracts do not support the published scene")
            expected_identity = {
                "session_id": metadata.identity.session_id,
                "player_uuid": metadata.identity.player_uuid,
                "connection_id": metadata.identity.connection_id,
                "segment_id": descriptor["segment_id"],
                "segment_ordinal": descriptor["source_segment_ordinal"],
            }
            observed_identity = {
                "session_id": mc_recorder.get("session_id"),
                "player_uuid": _replay_uuid(mc_recorder.get("player_uuid"), "player_uuid"),
                "connection_id": _replay_uuid(mc_recorder.get("connection_id"), "connection_id"),
                "segment_id": mc_recorder.get("segment_id"),
                "segment_ordinal": mc_recorder.get("segment_ordinal"),
            }
            if observed_identity != expected_identity:
                raise BundleError("replay mc_recorder identity does not match its bundle descriptor")
    except BundleError:
        raise
    except (EOFError, NotImplementedError, OSError, RuntimeError, zipfile.BadZipFile) as exc:
        raise BundleError("replay segment is not a readable Flashback ZIP") from exc


def _validate_timeline(
    path: Path,
    metadata: ValidatedMetadata,
    maximum_line_bytes: int,
    *,
    scene_path: Path,
    scene_result: object | None,
    validated_frame_pts: tuple[int, ...] | None,
) -> None:
    if metadata.render is None:
        raise BundleError("render timeline is present without a render descriptor")
    video = metadata.render["video"]
    expected_frames = video["frame_count"]
    previous_pts: int | None = None
    observed_frames = 0
    alignments: dict[int, object] | None = None
    if scene_result is not None:
        try:
            from minerec.processing.scene.store_v2 import SceneStoreV2

            with SceneStoreV2(scene_path) as store:
                alignments = {alignment.server_tick: alignment for alignment in store.iter_frame_alignments()}
        except BundleError:
            raise
        except Exception as exc:
            raise BundleError("render timeline cannot read validated Scene V2 frame alignment") from exc
    replay_by_segment = {descriptor["segment_id"]: descriptor for descriptor in metadata.replays}
    try:
        for line_number, line in _iter_bounded_lines(path, maximum_line_bytes):
            frame_index = line_number - 1
            if not line.endswith(b"\n") or not line.strip():
                raise BundleError("render timeline contains a blank or unterminated record")
            try:
                value = json.loads(
                    line,
                    parse_constant=lambda token: (_ for _ in ()).throw(ValueError(token)),
                )
            except (ValueError, UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
                raise BundleError(f"render timeline line {line_number} is invalid JSON") from exc
            if not isinstance(value, dict):
                raise BundleError(f"render timeline line {line_number} is not an object")
            for key in ("frame_index", "pts", "server_tick", "replay_tick"):
                item = value.get(key)
                if not isinstance(item, int) or isinstance(item, bool):
                    raise BundleError(f"render timeline line {line_number} has invalid {key}")
            scene_frame = value.get("scene_frame")
            if not isinstance(scene_frame, str) or not scene_frame or len(scene_frame) > 255:
                raise BundleError(f"render timeline line {line_number} has invalid scene_frame")
            if value["frame_index"] != frame_index:
                raise BundleError("render timeline frame indexes must be contiguous from zero")
            if value["server_tick"] != metadata.identity.start_tick + frame_index:
                raise BundleError("render timeline must map one frame to every connection tick")
            if value["replay_tick"] < 0:
                raise BundleError("render timeline contains a negative replay tick")
            if alignments is not None:
                alignment = alignments.get(value["server_tick"])
                if alignment is None:
                    raise BundleError("render timeline server tick has no Scene V2 frame")
                if scene_frame != getattr(alignment, "frame_id", None) or value["replay_tick"] != getattr(alignment, "replay_tick", None):
                    raise BundleError("render timeline does not exactly match its Scene V2 frame alignment")
                segment_id = getattr(alignment, "segment_id", None)
                descriptor = replay_by_segment.get(segment_id)
                if descriptor is None or descriptor["server_ticks"] is None or descriptor["replay_ticks"] is None:
                    raise BundleError("render timeline frame is not covered by a replay descriptor")
                if not descriptor["server_ticks"]["start"] <= value["server_tick"] <= descriptor["server_ticks"]["end"]:
                    raise BundleError("render timeline server tick is outside its replay descriptor")
                if not descriptor["replay_ticks"]["start"] <= value["replay_tick"] <= descriptor["replay_ticks"]["end"]:
                    raise BundleError("render timeline replay tick is outside its replay descriptor")
            if previous_pts is not None and value["pts"] <= previous_pts:
                raise BundleError("render timeline PTS values must be strictly increasing")
            if validated_frame_pts is not None and value["pts"] != validated_frame_pts[frame_index]:
                raise BundleError("render timeline PTS does not exactly match its MP4 frame")
            previous_pts = value["pts"]
            observed_frames += 1
    except BundleError:
        raise
    except OSError as exc:
        raise BundleError("render timeline cannot be read after extraction") from exc
    if observed_frames != expected_frames:
        raise BundleError("render timeline record count does not match the video descriptor")


def _iter_mp4_boxes(path: Path) -> Iterator[tuple[bytes, int, int]]:
    size = path.stat().st_size
    offset = 0
    with path.open("rb") as handle:
        while offset < size:
            handle.seek(offset)
            header = handle.read(8)
            if len(header) != 8:
                raise BundleError("FPV MP4 has a truncated box header")
            box_size, box_type = struct.unpack(">I4s", header)
            header_size = 8
            if box_size == 1:
                extended = handle.read(8)
                if len(extended) != 8:
                    raise BundleError("FPV MP4 has a truncated extended box header")
                box_size = struct.unpack(">Q", extended)[0]
                header_size = 16
            elif box_size == 0:
                box_size = size - offset
            if box_size < header_size or box_size > size - offset:
                raise BundleError("FPV MP4 has an invalid box size")
            yield box_type, offset, box_size
            offset += box_size


def _validate_mp4_shape(path: Path) -> None:
    try:
        boxes = list(_iter_mp4_boxes(path))
    except OSError as exc:
        raise BundleError("FPV MP4 cannot be read after extraction") from exc
    types = [box_type for box_type, _offset, _size in boxes]
    if not types or types[0] != b"ftyp" or b"moov" not in types or b"mdat" not in types:
        raise BundleError("FPV MP4 lacks required ftyp/moov/mdat boxes")
    if types.index(b"moov") > types.index(b"mdat"):
        raise BundleError("FPV MP4 is not fast-start (moov follows mdat)")


def _call_path_validator(
    validator: PathValidator | None,
    path: Path,
    description: str,
) -> object | None:
    if validator is None:
        return None
    try:
        result = validator(path)
    except BundleError:
        raise
    except Exception as exc:
        raise BundleError(f"{description} validator rejected the bundle") from exc
    if result is False:
        raise BundleError(f"{description} validator rejected the bundle")
    return result


def _validate_scene_binding(result: object | None, metadata: ValidatedMetadata) -> None:
    scene_identity = getattr(result, "identity", None)
    if scene_identity is None:
        return
    expected = metadata.identity
    observed_identity = (
        getattr(scene_identity, "session_id", None),
        getattr(scene_identity, "player_uuid", None),
        getattr(scene_identity, "connection_id", None),
    )
    if observed_identity != (expected.session_id, expected.player_uuid, expected.connection_id):
        raise BundleError("scene validator identity does not match bundle metadata")
    if getattr(result, "start_tick", None) != expected.start_tick or getattr(result, "end_tick", None) != expected.end_tick:
        raise BundleError("scene validator tick range does not match bundle metadata")
    ticks = getattr(result, "ticks", None)
    if ticks is not None:
        try:
            if len(ticks) != expected.end_tick - expected.start_tick + 1 or any(tick != expected.start_tick + index for index, tick in enumerate(ticks)):
                raise BundleError("scene validator frame ticks are not complete and contiguous")
        except TypeError as exc:
            raise BundleError("scene validator returned an invalid tick sequence") from exc
    source_replays = getattr(result, "source_replays", None)
    if source_replays is None:
        raise BundleError("scene validator did not return replay provenance")
    expected_sources = tuple(
        {
            "segment_id": descriptor["segment_id"],
            "segment_ordinal": descriptor["source_segment_ordinal"],
            "path": descriptor["path"],
            "sha256": descriptor["sha256"],
            "size_bytes": descriptor["size_bytes"],
            "format": "flashback",
        }
        for descriptor in metadata.replays
    )
    try:
        observed_sources = tuple(dict(source) for source in source_replays)
    except (TypeError, ValueError) as exc:
        raise BundleError("scene validator returned invalid replay provenance") from exc
    if observed_sources != expected_sources:
        raise BundleError("scene replay provenance does not match bundle replay descriptors")


def _call_render_validator(
    validator: RenderValidator | None,
    path: Path,
    descriptor: Mapping[str, Any],
) -> object | None:
    if validator is None:
        return None
    try:
        result = validator(path, descriptor)
    except BundleError:
        raise
    except Exception as exc:
        raise BundleError("render validator rejected the bundle") from exc
    if result is False:
        raise BundleError("render validator rejected the bundle")
    return result


def _validated_frame_pts(
    validator_result: object | None,
    *,
    expected_frames: int,
) -> tuple[int, ...] | None:
    if not isinstance(validator_result, Mapping) or "frame_pts" not in validator_result:
        return None
    result_mapping = cast(Mapping[str, object], validator_result)
    raw_pts = result_mapping["frame_pts"]
    if not isinstance(raw_pts, (list, tuple)):
        raise BundleError("render validator returned an invalid frame PTS sequence")
    if any(not isinstance(value, int) or isinstance(value, bool) for value in raw_pts):
        raise BundleError("render validator returned an invalid frame PTS sequence")
    values = cast(tuple[int, ...], tuple(raw_pts))
    if len(values) != expected_frames:
        raise BundleError("render validator frame PTS count does not match the video")
    return values


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def open_bundle(
    source: BundleInput,
    *,
    scene_validator: PathValidator | None = None,
    actions_validator: PathValidator | None = None,
    render_validator: RenderValidator | None = None,
    limits: BundleLimits = DEFAULT_BUNDLE_LIMITS,
    temp_root: Path | None = None,
) -> OpenedBundle:
    """Validate and privately extract one immutable portable play bundle."""

    validate_limits(limits)
    if temp_root is not None:
        try:
            root_status = temp_root.lstat()
        except OSError as exc:
            raise BundleError(f"cannot inspect bundle temporary root: {temp_root}") from exc
        if stat.S_ISLNK(root_status.st_mode) or not stat.S_ISDIR(root_status.st_mode):
            raise BundleError("bundle temporary root must be a non-symlinked directory")

    temporary: tempfile.TemporaryDirectory[str] | None = None
    try:
        with _open_input(source, limits) as (handle, source_path):
            archive_size = _archive_size(handle)
            _preflight_zip_directory(handle, archive_size, limits)
            handle.seek(0)
            try:
                with zipfile.ZipFile(handle, "r", allowZip64=True) as archive:
                    entries = _inspect_entries(archive, limits)
                    metadata_info = entries.get("metadata.json")
                    if metadata_info is None:
                        raise BundleError("bundle is missing metadata.json")
                    if metadata_info.compress_type != _JSON_COMPRESSION:
                        raise BundleError("metadata.json must be deflated")
                    metadata_bytes = _read_bounded_entry(
                        archive,
                        metadata_info,
                        limits.max_metadata_bytes,
                    )
                    metadata = validate_metadata(metadata_bytes)
                    _cross_check_archive(entries, metadata, limits)

                    temporary = tempfile.TemporaryDirectory(
                        prefix="minerec-play-bundle-",
                        dir=temp_root,
                    )
                    extraction_root = Path(temporary.name)
                    extraction_root.chmod(0o700)
                    extracted: dict[str, Path] = {}
                    for item in metadata.inventory:
                        extracted[item.path] = _extract_verified_entry(
                            archive,
                            entries[item.path],
                            item,
                            extraction_root,
                        )
            except BundleError:
                raise
            except (
                EOFError,
                NotImplementedError,
                OSError,
                RuntimeError,
                zipfile.BadZipFile,
                zipfile.LargeZipFile,
            ) as exc:
                raise BundleError("play bundle is not a readable ZIP64 archive") from exc

        actions_path = extracted["actions.jsonl"]
        scene_path = extracted["scene.sqlite3"]
        _validate_actions(actions_path, metadata.identity, limits.max_json_line_bytes)
        _call_path_validator(actions_validator, actions_path, "actions")
        _validate_scene_header(scene_path)
        scene_result = _call_path_validator(scene_validator, scene_path, "scene")
        _validate_scene_binding(scene_result, metadata)

        replay_descriptors: list[ReplayDescriptor] = []
        for descriptor in metadata.replays:
            path = extracted[descriptor["path"]]
            _validate_flashback_replay(path, limits, metadata, descriptor)
            replay_descriptors.append(
                ReplayDescriptor(
                    ordinal=descriptor["ordinal"],
                    segment_id=descriptor["segment_id"],
                    source_segment_ordinal=descriptor["source_segment_ordinal"],
                    archive_path=path,
                    sha256=descriptor["sha256"],
                    size_bytes=descriptor["size_bytes"],
                    start_server_tick=(None if descriptor["server_ticks"] is None else descriptor["server_ticks"]["start"]),
                    end_server_tick=(None if descriptor["server_ticks"] is None else descriptor["server_ticks"]["end"]),
                    start_replay_tick=(None if descriptor["replay_ticks"] is None else descriptor["replay_ticks"]["start"]),
                    end_replay_tick=(None if descriptor["replay_ticks"] is None else descriptor["replay_ticks"]["end"]),
                )
            )

        fpv_path: Path | None = None
        timeline_path: Path | None = None
        if metadata.render is not None:
            fpv_path = extracted[OPTIONAL_RENDER_ENTRY_NAMES[0]]
            timeline_path = extracted[OPTIONAL_RENDER_ENTRY_NAMES[1]]
            _validate_mp4_shape(fpv_path)
            validator_result = _call_render_validator(
                render_validator,
                fpv_path,
                metadata.render["video"],
            )
            frame_pts = _validated_frame_pts(
                validator_result,
                expected_frames=metadata.render["video"]["frame_count"],
            )
            _validate_timeline(
                timeline_path,
                metadata,
                limits.max_json_line_bytes,
                scene_path=scene_path,
                scene_result=scene_result,
                validated_frame_pts=frame_pts,
            )

        _fsync_directory(extraction_root)
        return OpenedBundle(
            source=source_path,
            bundle_id=metadata.value["bundle_id"],
            _metadata_json=metadata.canonical_json,
            extraction_root=extraction_root,
            actions_path=actions_path,
            scene_path=scene_path,
            replay_descriptors=tuple(replay_descriptors),
            fpv_path=fpv_path,
            timeline_path=timeline_path,
            _temporary_directory=temporary,
        )
    except Exception:
        if temporary is not None:
            temporary.cleanup()
        raise
