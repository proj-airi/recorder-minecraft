from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import stat
import subprocess
import tempfile
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from fractions import Fraction
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, cast

from minerec.config import RecorderConfig
from minerec.errors import RecorderError
from minerec.processing.bundle.model import (
    ArtifactSource,
    BundleIdentity,
    BundleRequest,
    PublishedBundle,
    RenderAttachment,
    ReplaySegment,
)
from minerec.processing.bundle.publisher import publish_bundle
from minerec.processing.bundle.reader import validate_reconstructed_actions
from minerec.processing.capture.exporter import (
    canonical_action_jsonl_line,
    canonical_state_jsonl_line,
    source_record_selected,
)
from minerec.processing.scene.store import (
    SceneStoreInfo,
    validate_scene_attachment_provenance,
    validate_scene_store,
)
from minerec.processing.scene.store_v2 import finalize_scene_store_v2, validate_scene_store_v2
from minerec.render.control.sources import ReplaySegmentSource, resolve_replay_segments

MAX_DATASET_MANIFEST_BYTES = 4 * 1024 * 1024
MAX_DATASET_FILES = 64
MAX_STATE_LINE_BYTES = 64 * 1024 * 1024
MAX_SOURCE_EVENT_LINE_BYTES = 16 * 1024 * 1024
MAX_STATES = 20 * 60 * 60 * 24
KNOWN_MODALITY_GAPS = (
    "audio_not_captured",
    "particle_lifecycle_not_captured",
    "raw_device_input_not_captured",
    "unopened_container_contents_unknown",
)


@dataclass(frozen=True)
class _Dataset:
    directory: Path
    manifest: Mapping[str, Any]
    files: Mapping[str, ArtifactSource]


@dataclass(frozen=True)
class _StateIdentity:
    session_id: str
    player_uuid: str
    player_name: str
    connection_id: str
    ticks: tuple[int, ...]
    started_at: str
    ended_at: str


@dataclass(frozen=True)
class _DatasetProvenance:
    epoch_hashes: Mapping[int, tuple[str, str]]
    action_records: int
    selection_start_tick: int
    selection_end_tick: int


def _strict_json(data: bytes, description: str) -> Any:  # noqa: ANN401
    try:
        return json.loads(
            data,
            parse_constant=lambda token: (_ for _ in ()).throw(ValueError(f"non-finite {token}")),
        )
    except (ValueError, UnicodeDecodeError, RecursionError) as exc:
        raise RecorderError(f"{description} is invalid JSON") from exc


def _canonical_uuid(value: object, description: str) -> str:
    if not isinstance(value, str):
        raise RecorderError(f"{description} must be a canonical UUID")
    try:
        canonical = str(uuid.UUID(value))
    except ValueError as exc:
        raise RecorderError(f"{description} must be a canonical UUID") from exc
    if value != canonical:
        raise RecorderError(f"{description} must use canonical UUID spelling")
    return canonical


def _regular_file(path: Path, description: str) -> os.stat_result:
    try:
        status = path.lstat()
    except OSError as exc:
        raise RecorderError(f"cannot inspect {description}: {path}") from exc
    if stat.S_ISLNK(status.st_mode) or not stat.S_ISREG(status.st_mode) or status.st_nlink != 1:
        raise RecorderError(f"{description} must be a non-linked regular file: {path}")
    return status


def _file_identity(status: os.stat_result) -> tuple[int, int, int, int]:
    return status.st_dev, status.st_ino, status.st_size, status.st_mtime_ns


def _stable_artifact(path: Path, description: str) -> ArtifactSource:
    before = _regular_file(path, description)
    digest = hashlib.sha256()
    observed = 0
    try:
        with path.open("rb") as handle:
            opened = os.fstat(handle.fileno())
            if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
                raise RecorderError(f"{description} changed while being opened")
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
                observed += len(chunk)
            after_open = os.fstat(handle.fileno())
        after = path.lstat()
    except OSError as exc:
        raise RecorderError(f"cannot hash {description}: {path}") from exc
    identities = (
        (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns),
        (after_open.st_dev, after_open.st_ino, after_open.st_size, after_open.st_mtime_ns),
        (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns),
    )
    if identities[0] != identities[1] or identities[0] != identities[2] or observed != before.st_size:
        raise RecorderError(f"{description} changed while being hashed")
    return ArtifactSource(path=path, sha256=digest.hexdigest(), size_bytes=observed)


def _dataset_entry_name(name: object) -> str:
    if not isinstance(name, str) or not name or "\\" in name or "\x00" in name:
        raise RecorderError("dataset manifest contains an invalid file name")
    value = PurePosixPath(name)
    if value.is_absolute() or any(part in {"", ".", ".."} for part in name.split("/")):
        raise RecorderError("dataset manifest contains an escaping file name")
    return name


def _load_dataset(directory: Path) -> _Dataset:
    root = directory.expanduser().resolve()
    if directory.is_symlink() or not root.is_dir() or not root.name.endswith(".dataset"):
        raise RecorderError("bundle finalization requires a non-symlinked *.dataset directory")
    manifest_path = root / "manifest.json"
    status = _regular_file(manifest_path, "dataset manifest")
    if status.st_size > MAX_DATASET_MANIFEST_BYTES:
        raise RecorderError("dataset manifest exceeds the byte limit")
    manifest = _strict_json(manifest_path.read_bytes(), "dataset manifest")
    if not isinstance(manifest, dict):
        raise RecorderError("dataset manifest must be an object")
    if manifest.get("schema_version") != 2 or manifest.get("owner") != "mc-recorder" or manifest.get("format") != "mc-recorder-jsonl-v2":
        raise RecorderError("bundle finalization requires a Dataset V2 export")
    declarations = manifest.get("files")
    if not isinstance(declarations, dict) or not 1 <= len(declarations) <= MAX_DATASET_FILES:
        raise RecorderError("dataset manifest file inventory is invalid")

    actual: set[str] = set()
    for candidate in root.rglob("*"):
        if candidate.is_symlink():
            raise RecorderError(f"dataset contains a symlink: {candidate.relative_to(root)}")
        if candidate.is_dir():
            continue
        if not candidate.is_file():
            raise RecorderError(f"dataset contains a non-regular entry: {candidate.relative_to(root)}")
        relative = candidate.relative_to(root).as_posix()
        if relative != "manifest.json":
            actual.add(relative)
    declared = {_dataset_entry_name(name) for name in declarations}
    if actual != declared:
        raise RecorderError("dataset files do not exactly match manifest inventory")

    files: dict[str, ArtifactSource] = {}
    for name in sorted(declared):
        descriptor = declarations[name]
        if not isinstance(descriptor, dict) or set(descriptor) != {"sha256", "size_bytes"}:
            raise RecorderError(f"dataset manifest file descriptor is invalid: {name}")
        source = _stable_artifact(root.joinpath(*name.split("/")), f"dataset file {name}")
        if descriptor.get("sha256") != source.sha256 or descriptor.get("size_bytes") != source.size_bytes:
            raise RecorderError(f"dataset file fails its manifest hash or size: {name}")
        files[name] = source
    for required in (
        "actions.jsonl",
        "modalities.jsonl",
        "samples.jsonl",
        "states.jsonl",
        "scene/scene-v1.sqlite3",
    ):
        if required not in files:
            raise RecorderError(f"dataset lacks required bundle source {required}")
    if _file_identity(_regular_file(manifest_path, "dataset manifest")) != _file_identity(status):
        raise RecorderError("dataset manifest changed during validation")
    return _Dataset(root, manifest, files)


def _utc_from_millis(value: object, description: str) -> tuple[int, str]:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise RecorderError(f"{description} must be a non-negative Unix millisecond timestamp")
    try:
        moment = datetime.fromtimestamp(value / 1000, UTC)
    except (OSError, OverflowError, ValueError) as exc:
        raise RecorderError(f"{description} is outside the supported UTC range") from exc
    return value, moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _mapping(value: object, description: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise RecorderError(f"{description} must be an object")
    return cast(dict[str, Any], value)


def _nonnegative_int(value: object, description: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise RecorderError(f"{description} must be a non-negative integer")
    return value


def _sha256_text(value: object, description: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise RecorderError(f"{description} must be a lowercase SHA-256 digest")
    return value


def _inspect_states(path: Path) -> _StateIdentity:
    values: tuple[str, str, str, str] | None = None
    ticks: list[int] = []
    wall_times: list[int] = []
    before = _regular_file(path, "authoritative states JSONL")
    try:
        with path.open("rb") as handle:
            line_number = 0
            while raw := handle.readline(MAX_STATE_LINE_BYTES + 1):
                line_number += 1
                if line_number > MAX_STATES:
                    raise RecorderError("authoritative states exceed the connection tick limit")
                if len(raw) > MAX_STATE_LINE_BYTES:
                    raise RecorderError(f"states.jsonl line {line_number} exceeds the byte limit")
                if not raw.endswith(b"\n") or not raw.strip():
                    raise RecorderError(f"states.jsonl line {line_number} is blank or unterminated")
                record = _strict_json(raw, f"states.jsonl line {line_number}")
                if not isinstance(record, dict):
                    raise RecorderError(f"states.jsonl line {line_number} must be an object")
                identity = (
                    record.get("session_id"),
                    record.get("player_uuid"),
                    record.get("player_name"),
                    record.get("connection_id"),
                )
                if not all(isinstance(item, str) and item for item in identity):
                    raise RecorderError(f"states.jsonl line {line_number} lacks connection identity")
                typed_identity = (identity[0], identity[1], identity[2], identity[3])
                if values is None:
                    values = typed_identity  # type: ignore[assignment]
                elif values != typed_identity:
                    raise RecorderError("states.jsonl contains more than one player connection")
                tick = record.get("server_tick")
                if not isinstance(tick, int) or isinstance(tick, bool) or tick < 0:
                    raise RecorderError(f"states.jsonl line {line_number} has invalid server_tick")
                if ticks and tick != ticks[-1] + 1:
                    raise RecorderError("states.jsonl ticks must be contiguous and strictly increasing")
                ticks.append(tick)
                wall_time, _timestamp = _utc_from_millis(
                    record.get("recorded_at_unix_ms"),
                    f"states.jsonl line {line_number} recorded_at_unix_ms",
                )
                wall_times.append(wall_time)
    except OSError as exc:
        raise RecorderError("cannot read authoritative states JSONL") from exc
    after = path.lstat()
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ):
        raise RecorderError("authoritative states changed while being inspected")
    if values is None or not ticks:
        raise RecorderError("states.jsonl contains no player states")
    session_id, player_uuid, player_name, connection_id = values
    _canonical_uuid(player_uuid, "state player_uuid")
    _canonical_uuid(connection_id, "state connection_id")
    _start_ms, started_at = _utc_from_millis(min(wall_times), "connection start")
    _end_ms, ended_at = _utc_from_millis(max(wall_times), "connection end")
    return _StateIdentity(
        session_id=session_id,
        player_uuid=player_uuid,
        player_name=player_name,
        connection_id=connection_id,
        ticks=tuple(ticks),
        started_at=started_at,
        ended_at=ended_at,
    )


def _validate_dataset_provenance(
    dataset: _Dataset,
    states: _StateIdentity,
) -> _DatasetProvenance:
    """Require the exporter-created snapshot that proves a full closed connection.

    Dataset V2 is an intermediate and its inventory hashes alone are not closure
    evidence.  The append-prefix snapshot is created only after observing the
    exact connection's join and leave records; its selected bounds must be the
    bounds published by this bundle.
    """

    manifest = dataset.manifest
    if manifest.get("session_id") != states.session_id:
        raise RecorderError("dataset manifest session_id does not match authoritative states")
    source = _mapping(manifest.get("source"), "dataset source provenance")
    if _nonnegative_int(source.get("active_epochs_skipped"), "dataset active_epochs_skipped") != 0:
        raise RecorderError("bundle publication requires a closed snapshot with no skipped active epoch")
    snapshot = _mapping(source.get("snapshot"), "dataset closed-connection snapshot")
    if snapshot.get("format") != "append_prefix_v1":
        raise RecorderError("dataset lacks the append-prefix closed-connection snapshot")
    expected_snapshot_identity = {
        "session_id": states.session_id,
        "player_uuid": states.player_uuid,
        "connection_id": states.connection_id,
    }
    if any(snapshot.get(key) != expected for key, expected in expected_snapshot_identity.items()):
        raise RecorderError("dataset snapshot identity does not match authoritative states")
    selection_start_tick = _nonnegative_int(
        snapshot.get("selection_start_tick"),
        "dataset snapshot selection start tick",
    )
    selection_end_tick = _nonnegative_int(
        snapshot.get("selection_end_tick"),
        "dataset snapshot selection end tick",
    )
    if selection_start_tick > selection_end_tick:
        raise RecorderError("dataset snapshot connection leave precedes its join")
    if not selection_start_tick <= states.ticks[0] <= states.ticks[-1] <= selection_end_tick:
        raise RecorderError("authoritative state coverage is outside the closed connection envelope")

    selection = _mapping(manifest.get("selection"), "dataset selection")
    if selection.get("players") != [states.player_uuid] or selection.get("connections") != [states.connection_id]:
        raise RecorderError("bundle publication requires exactly one selected player connection")
    if selection.get("from_tick") != selection_start_tick or selection.get("to_tick") != selection_end_tick:
        raise RecorderError("Dataset V2 selection does not match its full closed connection envelope")
    if selection.get("scene_attachment") is None:
        raise RecorderError("dataset selection lacks verified scene attachment provenance")

    raw_epochs = source.get("epochs")
    raw_segments = snapshot.get("segments")
    if not isinstance(raw_epochs, list) or not raw_epochs or not isinstance(raw_segments, list) or len(raw_segments) != len(raw_epochs):
        raise RecorderError("dataset snapshot and sealed epoch provenance are incomplete")
    if _nonnegative_int(source.get("sealed_epochs"), "dataset sealed epoch count") != len(raw_epochs):
        raise RecorderError("dataset sealed epoch count does not match its provenance")
    epoch_hashes: dict[int, tuple[str, str]] = {}
    previous_epoch_index: int | None = None
    for ordinal, raw_epoch in enumerate(raw_epochs):
        epoch = _mapping(raw_epoch, f"dataset source epoch {ordinal}")
        epoch_index = _nonnegative_int(epoch.get("epoch_index"), f"dataset source epoch {ordinal} index")
        if epoch_index in epoch_hashes:
            raise RecorderError("dataset source provenance contains a duplicate epoch index")
        if previous_epoch_index is not None and epoch_index <= previous_epoch_index:
            raise RecorderError("dataset source epochs are not strictly ordered")
        previous_epoch_index = epoch_index
        manifest_sha256 = _sha256_text(epoch.get("manifest_sha256"), f"dataset source epoch {ordinal} manifest hash")
        events_sha256 = _sha256_text(epoch.get("events_sha256"), f"dataset source epoch {ordinal} events hash")
        if _nonnegative_int(epoch.get("events_bytes"), f"dataset source epoch {ordinal} byte count") <= 0:
            raise RecorderError("dataset source epoch byte count must be positive")
        if _nonnegative_int(epoch.get("record_count"), f"dataset source epoch {ordinal} record count") <= 0:
            raise RecorderError("dataset source epoch record count must be positive")
        epoch_hashes[epoch_index] = (events_sha256, manifest_sha256)

        segment = _mapping(raw_segments[ordinal], f"dataset snapshot segment {ordinal}")
        if segment.get("kind") not in {"finalized", "active_prefix"}:
            raise RecorderError("dataset snapshot segment kind is invalid")
        if segment.get("epoch_index") != epoch_index or segment.get("sha256") != events_sha256 or segment.get("bytes") != epoch.get("events_bytes") or segment.get("record_count") != epoch.get("record_count"):
            raise RecorderError("dataset snapshot segment does not match sealed epoch provenance")

    top_manifest_sha = _sha256_text(manifest.get("source_manifest_sha256"), "dataset source manifest hash")
    if _sha256_text(source.get("manifest_sha256"), "dataset source manifest provenance hash") != top_manifest_sha:
        raise RecorderError("dataset source manifest hashes disagree")

    timeline = _mapping(manifest.get("timeline"), "dataset timeline")
    if timeline.get("tick_rate_hz") != 20 or timeline.get("sample_rate_hz") != 20:
        raise RecorderError("bundle publication requires the Dataset V2 20 Hz timeline")
    modalities = _mapping(manifest.get("modalities"), "dataset modalities")
    state_modality = _mapping(modalities.get("state"), "dataset state modality")
    actions_modality = _mapping(modalities.get("actions"), "dataset actions modality")
    scene_modality = _mapping(modalities.get("scene"), "dataset scene modality")
    if state_modality.get("available") is not True or state_modality.get("file") != "states.jsonl" or state_modality.get("records") != len(states.ticks):
        raise RecorderError("dataset state modality does not match authoritative state coverage")
    action_records = _nonnegative_int(actions_modality.get("records"), "dataset action record count")
    if actions_modality.get("available") is not True or actions_modality.get("file") != "actions.jsonl":
        raise RecorderError("dataset actions modality is incomplete")
    if scene_modality.get("availability") != "per-sample" or scene_modality.get("store") != "scene/scene-v1.sqlite3" or scene_modality.get("records_attached") != len(states.ticks):
        raise RecorderError("dataset scene modality does not cover every authoritative state")
    return _DatasetProvenance(
        epoch_hashes=epoch_hashes,
        action_records=action_records,
        selection_start_tick=selection_start_tick,
        selection_end_tick=selection_end_tick,
    )


def _capture_episode_path(
    snapshot: Mapping[str, Any],
    source: Mapping[str, Any],
    *,
    captures_root: Path,
    session_id: str,
) -> Path:
    value = snapshot.get("source_episode")
    if not isinstance(value, str) or not value or "\\" in value:
        raise RecorderError("dataset snapshot source episode is invalid")
    unresolved = Path(value)
    if not unresolved.is_absolute():
        raise RecorderError("dataset snapshot source episode must be absolute")
    try:
        resolved = unresolved.resolve(strict=True)
        status = unresolved.lstat()
    except OSError as exc:
        raise RecorderError("dataset snapshot source episode is unavailable") from exc
    if str(resolved) != value or stat.S_ISLNK(status.st_mode) or not stat.S_ISDIR(status.st_mode):
        raise RecorderError("dataset snapshot source episode is not canonical and regular")
    capture_root = captures_root.expanduser().resolve()
    if resolved.parent != capture_root or resolved.name != session_id:
        raise RecorderError("dataset snapshot source episode is outside the configured capture root")
    if source.get("episode") != value:
        raise RecorderError("dataset source episode paths disagree")
    return resolved


def _snapshot_source_path(
    source_episode: Path,
    raw_path: object,
    *,
    kind: str,
) -> Path:
    if not isinstance(raw_path, str) or not raw_path or "\\" in raw_path:
        raise RecorderError("dataset snapshot segment source path is invalid")
    relative = PurePosixPath(raw_path)
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in raw_path.split("/")):
        raise RecorderError("dataset snapshot segment source path is not contained")
    candidate = source_episode.joinpath(*relative.parts)
    if not candidate.exists() and kind == "active_prefix" and candidate.name == "events.jsonl.inprogress":
        candidate = candidate.with_name("events.jsonl")
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(source_episode)
    except (OSError, ValueError) as exc:
        raise RecorderError("dataset snapshot segment source is unavailable or escaping") from exc
    if candidate.is_symlink() or resolved != candidate:
        raise RecorderError("dataset snapshot segment source is not canonical")
    return resolved


def _generated_snapshot_epoch_manifest(
    *,
    session_id: str,
    epoch_index: int,
    events_bytes: int,
    events_sha256: str,
    record_count: int,
    first_sequence: int,
    last_sequence: int,
    first_server_tick: int,
    last_server_tick: int,
    record_counts: Counter[str],
) -> bytes:
    value = {
        "schema_version": 1,
        "session_id": session_id,
        "epoch_index": epoch_index,
        "sealed": True,
        "rotation_reason": "consumer_snapshot",
        "forced_seal": False,
        "record_count": record_count,
        "events_bytes": events_bytes,
        "events_sha256": events_sha256,
        "first_server_tick": first_server_tick,
        "last_server_tick": last_server_tick,
        "first_sequence": first_sequence,
        "last_sequence": last_sequence,
        "record_counts": dict(sorted(record_counts.items())),
    }
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _compare_dataset_row(
    handle: Any,  # noqa: ANN401
    expected: bytes,
    *,
    stream_name: str,
) -> None:
    observed = handle.readline(MAX_STATE_LINE_BYTES + 1)
    if observed != expected:
        raise RecorderError(f"dataset {stream_name} does not exactly reconstruct from its capture snapshot")


def _verify_dataset_source_snapshot(
    dataset: _Dataset,
    states: _StateIdentity,
    provenance: _DatasetProvenance,
    *,
    captures_root: Path,
) -> None:
    """Re-read the retained capture prefixes and reproduce canonical Dataset V2 rows."""

    source = _mapping(dataset.manifest.get("source"), "dataset source provenance")
    snapshot = _mapping(source.get("snapshot"), "dataset closed-connection snapshot")
    source_episode = _capture_episode_path(
        snapshot,
        source,
        captures_root=captures_root,
        session_id=states.session_id,
    )
    session_manifest_path = source_episode / "manifest.json"
    session_manifest = _stable_artifact(session_manifest_path, "capture session manifest")
    expected_manifest_sha = _sha256_text(
        source.get("manifest_sha256"),
        "dataset source manifest provenance hash",
    )
    if session_manifest.sha256 != expected_manifest_sha:
        raise RecorderError("capture session manifest no longer matches Dataset V2 provenance")
    session_manifest_value = _strict_json(session_manifest_path.read_bytes(), "capture session manifest")
    if not isinstance(session_manifest_value, dict) or session_manifest_value.get("session_id") != states.session_id:
        raise RecorderError("capture session manifest identity does not match Dataset V2")

    raw_epochs = source.get("epochs")
    raw_segments = snapshot.get("segments")
    assert isinstance(raw_epochs, list) and isinstance(raw_segments, list)
    previous_sequence: int | None = None
    previous_tick: int | None = None
    joins: list[tuple[int, int]] = []
    leaves: list[tuple[int, int]] = []
    state_records = 0
    action_records = 0
    states_path = dataset.files["states.jsonl"].path
    actions_path = dataset.files["actions.jsonl"].path
    try:
        with states_path.open("rb") as states_handle, actions_path.open("rb") as actions_handle:
            for ordinal, raw_segment in enumerate(raw_segments):
                segment = _mapping(raw_segment, f"dataset snapshot segment {ordinal}")
                epoch = _mapping(raw_epochs[ordinal], f"dataset source epoch {ordinal}")
                epoch_index = _nonnegative_int(segment.get("epoch_index"), f"snapshot segment {ordinal} epoch")
                kind = segment.get("kind")
                assert isinstance(kind, str)
                retained_bytes = _nonnegative_int(segment.get("bytes"), f"snapshot segment {ordinal} byte count")
                observed_bytes = _nonnegative_int(
                    segment.get("observed_bytes"),
                    f"snapshot segment {ordinal} observed byte count",
                )
                if retained_bytes <= 0 or observed_bytes < retained_bytes:
                    raise RecorderError("dataset snapshot segment byte envelope is invalid")
                source_path = _snapshot_source_path(
                    source_episode,
                    segment.get("source"),
                    kind=kind,
                )
                before = _regular_file(source_path, f"capture snapshot segment {ordinal}")
                if before.st_size < observed_bytes or (kind == "finalized" and before.st_size != retained_bytes):
                    raise RecorderError("capture snapshot segment size no longer matches its retained prefix")

                digest = hashlib.sha256()
                record_counts: Counter[str] = Counter()
                segment_records = 0
                first_sequence: int | None = None
                last_sequence: int | None = None
                first_server_tick: int | None = None
                last_server_tick: int | None = None
                remaining = retained_bytes
                with source_path.open("rb") as source_handle:
                    opened = os.fstat(source_handle.fileno())
                    if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                        raise RecorderError("capture snapshot segment changed while being opened")
                    while remaining:
                        raw = source_handle.readline(min(MAX_SOURCE_EVENT_LINE_BYTES + 1, remaining))
                        if not raw:
                            raise RecorderError("capture snapshot segment is truncated")
                        if len(raw) > MAX_SOURCE_EVENT_LINE_BYTES or not raw.endswith(b"\n") or not raw.strip():
                            raise RecorderError("capture snapshot segment contains an invalid event line")
                        remaining -= len(raw)
                        digest.update(raw)
                        record = _strict_json(raw, f"capture snapshot segment {ordinal} event")
                        if not isinstance(record, dict):
                            raise RecorderError("capture snapshot segment event must be an object")
                        if record.get("session_id") != states.session_id or record.get("epoch_index") != epoch_index:
                            raise RecorderError("capture snapshot event identity does not match its segment")
                        sequence = _nonnegative_int(record.get("sequence"), "capture snapshot event sequence")
                        tick = _nonnegative_int(record.get("server_tick"), "capture snapshot event server tick")
                        if previous_sequence is not None and sequence <= previous_sequence:
                            raise RecorderError("capture snapshot event sequence is not strictly increasing")
                        if previous_tick is not None and tick < previous_tick:
                            raise RecorderError("capture snapshot server tick moved backwards")
                        previous_sequence = sequence
                        previous_tick = tick
                        first_sequence = sequence if first_sequence is None else first_sequence
                        last_sequence = sequence
                        first_server_tick = tick if first_server_tick is None else min(first_server_tick, tick)
                        last_server_tick = tick if last_server_tick is None else max(last_server_tick, tick)
                        record_type = record.get("record_type")
                        if not isinstance(record_type, str) or not record_type:
                            raise RecorderError("capture snapshot event lacks record_type")
                        record_counts[record_type] += 1
                        segment_records += 1

                        matches_connection = record.get("player_uuid") == states.player_uuid and record.get("connection_id") == states.connection_id
                        if matches_connection and record_type == "player_join":
                            joins.append((tick, sequence))
                        elif matches_connection and record_type == "player_leave":
                            leaves.append((tick, sequence))
                        if not source_record_selected(
                            record,
                            player_uuid=states.player_uuid,
                            connection_id=states.connection_id,
                            first_tick=provenance.selection_start_tick,
                            last_tick=provenance.selection_end_tick,
                        ):
                            continue
                        if record_type == "player_state":
                            _compare_dataset_row(
                                states_handle,
                                canonical_state_jsonl_line(record, provenance.epoch_hashes),
                                stream_name="states.jsonl",
                            )
                            state_records += 1
                        elif record_type in {"control_state", "packet_apply", "action"}:
                            _compare_dataset_row(
                                actions_handle,
                                canonical_action_jsonl_line(record, provenance.epoch_hashes),
                                stream_name="actions.jsonl",
                            )
                            action_records += 1
                    after_open = os.fstat(source_handle.fileno())
                after = source_path.lstat()
                if (before.st_dev, before.st_ino) != (after_open.st_dev, after_open.st_ino) or (
                    before.st_dev,
                    before.st_ino,
                ) != (after.st_dev, after.st_ino):
                    raise RecorderError("capture snapshot segment changed while being verified")
                if after.st_size < observed_bytes or (kind == "finalized" and _file_identity(after) != _file_identity(before)):
                    raise RecorderError("capture snapshot segment changed while being verified")

                expected_events_sha = _sha256_text(segment.get("sha256"), f"snapshot segment {ordinal} hash")
                if digest.hexdigest() != expected_events_sha or segment_records != segment.get("record_count"):
                    raise RecorderError("capture snapshot retained prefix hash or record count does not match")
                observed_bounds = (
                    first_sequence,
                    last_sequence,
                    first_server_tick,
                    last_server_tick,
                )
                declared_bounds = (
                    segment.get("first_sequence"),
                    segment.get("last_sequence"),
                    segment.get("first_server_tick"),
                    segment.get("last_server_tick"),
                )
                if observed_bounds != declared_bounds or None in observed_bounds:
                    raise RecorderError("capture snapshot retained prefix bounds do not match")
                assert first_sequence is not None
                assert last_sequence is not None
                assert first_server_tick is not None
                assert last_server_tick is not None
                generated_manifest = _generated_snapshot_epoch_manifest(
                    session_id=states.session_id,
                    epoch_index=epoch_index,
                    events_bytes=retained_bytes,
                    events_sha256=expected_events_sha,
                    record_count=segment_records,
                    first_sequence=first_sequence,
                    last_sequence=last_sequence,
                    first_server_tick=first_server_tick,
                    last_server_tick=last_server_tick,
                    record_counts=record_counts,
                )
                if hashlib.sha256(generated_manifest).hexdigest() != epoch.get("manifest_sha256"):
                    raise RecorderError("Dataset V2 epoch manifest does not reconstruct from its capture prefix")

            if states_handle.read(1) or actions_handle.read(1):
                raise RecorderError("dataset state/action streams contain records absent from the capture snapshot")
    except OSError as exc:
        raise RecorderError("cannot verify Dataset V2 against its capture snapshot") from exc

    if len(joins) != 1 or len(leaves) != 1 or joins[0][1] >= leaves[0][1]:
        raise RecorderError("capture snapshot does not contain one ordered join and leave for the connection")
    if joins[0][0] != provenance.selection_start_tick or leaves[0][0] != provenance.selection_end_tick:
        raise RecorderError("capture join/leave ticks do not match the Dataset V2 selection envelope")
    if state_records != len(states.ticks) or action_records != provenance.action_records:
        raise RecorderError("capture snapshot record counts do not match Dataset V2 modalities")
    if _stable_artifact(session_manifest_path, "capture session manifest").sha256 != session_manifest.sha256:
        raise RecorderError("capture session manifest changed during bundle finalization")


def _replay_segments(
    scene_path: Path,
    scene: SceneStoreInfo,
    sources: list[ReplaySegmentSource],
) -> tuple[ReplaySegment, ...]:
    expected = tuple(
        (
            item["segment_id"],
            item["segment_ordinal"],
            item["sha256"],
            item["size_bytes"],
            item["format"],
        )
        for item in scene.source_replays
    )
    actual = tuple(
        (
            item.segment_id,
            item.segment_ordinal,
            item.sha256,
            item.size_bytes,
            item.replay_format,
        )
        for item in sources
    )
    if actual != expected:
        raise RecorderError("cataloged replay segments do not exactly match Scene V1 provenance")
    frames: dict[str, list[tuple[int, int]]] = {source.segment_id: [] for source in sources}
    source_ordinals = {source.segment_id: source.segment_ordinal for source in sources}
    previous_source_ordinal: int | None = None
    connection = sqlite3.connect(f"file:{scene_path}?mode=ro&immutable=1", uri=True)
    try:
        for server_tick, frame_id, replay_tick in connection.execute("SELECT server_tick, frame_id, replay_tick FROM frames ORDER BY server_tick"):
            matches = [source.segment_id for source in sources if frame_id.startswith(f"{source.segment_id}:")]
            if len(matches) != 1 or not isinstance(replay_tick, int) or isinstance(replay_tick, bool) or replay_tick < 0:
                raise RecorderError(f"scene frame at tick {server_tick} lacks exact replay-segment alignment")
            segment_id = matches[0]
            source_ordinal = source_ordinals[segment_id]
            if previous_source_ordinal is not None and source_ordinal < previous_source_ordinal:
                raise RecorderError("scene frame alignment returns to an earlier replay segment")
            previous_source_ordinal = source_ordinal
            aligned = frames[segment_id]
            if aligned:
                previous_server_tick, previous_replay_tick = aligned[-1]
                if server_tick != previous_server_tick + 1 or replay_tick != previous_replay_tick + 1:
                    raise RecorderError(f"scene frames for replay segment {segment_id} are not contiguous")
            aligned.append((server_tick, replay_tick))
    except sqlite3.Error as exc:
        raise RecorderError("cannot read replay alignment from Scene V1") from exc
    finally:
        connection.close()

    result: list[ReplaySegment] = []
    for source in sorted(sources, key=lambda item: item.segment_ordinal):
        aligned = frames[source.segment_id]
        result.append(
            ReplaySegment(
                segment_id=source.segment_id,
                source=ArtifactSource(source.path, source.sha256, source.size_bytes),
                source_segment_ordinal=source.segment_ordinal,
                start_server_tick=(aligned[0][0] if aligned else None),
                end_server_tick=(aligned[-1][0] if aligned else None),
                start_replay_tick=(aligned[0][1] if aligned else None),
                end_replay_tick=(aligned[-1][1] if aligned else None),
            )
        )
    return tuple(result)


def _probe_render(path: Path) -> dict[str, Any]:
    executable = shutil.which("ffprobe")
    if executable is None:
        raise RecorderError("ffprobe is required to validate an FPV attachment; run through Pixi")
    try:
        process = subprocess.run(
            (
                executable,
                "-v",
                "error",
                "-count_frames",
                "-show_entries",
                "stream=codec_type,codec_name,pix_fmt,width,height,avg_frame_rate,r_frame_rate,nb_read_frames",
                "-of",
                "json",
                str(path),
            ),
            check=False,
            capture_output=True,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RecorderError("ffprobe could not inspect the FPV attachment") from exc
    if process.returncode != 0:
        raise RecorderError("ffprobe rejected the FPV attachment")
    value = _strict_json(process.stdout, "ffprobe output")
    streams = value.get("streams") if isinstance(value, dict) else None
    if not isinstance(streams, list) or len(streams) != 1 or not isinstance(streams[0], dict):
        raise RecorderError("FPV attachment must contain exactly one video stream and no audio")
    stream = streams[0]
    if stream.get("codec_type") != "video" or stream.get("codec_name") != "h264" or stream.get("pix_fmt") != "yuv420p":
        raise RecorderError("FPV attachment must be H.264 with yuv420p pixels")
    try:
        fps = Fraction(str(stream.get("avg_frame_rate")))
        frame_count = int(stream.get("nb_read_frames"))
        width = int(stream.get("width"))
        height = int(stream.get("height"))
    except (ValueError, TypeError, ZeroDivisionError) as exc:
        raise RecorderError("FPV attachment lacks usable frame-rate, count, or dimensions") from exc
    if fps != 20 or width <= 0 or height <= 0 or frame_count <= 0:
        raise RecorderError("FPV attachment must be constant 20 FPS with positive dimensions and frames")
    return {"frame_count": frame_count, "width": width, "height": height}


def validate_fpv_render(path: Path, descriptor: Mapping[str, Any]) -> None:
    observed = _probe_render(path)
    expected = {
        "frame_count": descriptor.get("frame_count"),
        "width": descriptor.get("width"),
        "height": descriptor.get("height"),
    }
    if observed != expected:
        raise RecorderError("FPV attachment does not match its bundle descriptor")


def _render_attachment(
    video_path: Path,
    timeline_path: Path,
    *,
    expected_frames: int,
) -> RenderAttachment:
    video = _stable_artifact(video_path.expanduser().resolve(), "FPV render")
    timeline = _stable_artifact(timeline_path.expanduser().resolve(), "FPV render timeline")
    observed = _probe_render(video.path)
    if observed["frame_count"] != expected_frames:
        raise RecorderError("FPV frame count must equal the connection tick count")
    return RenderAttachment(
        video=video,
        timeline=timeline,
        frame_count=observed["frame_count"],
        width=observed["width"],
        height=observed["height"],
    )


def finalize_dataset_bundle(
    config: RecorderConfig,
    dataset_path: Path,
    *,
    fpv_path: Path | None = None,
    fpv_timeline_path: Path | None = None,
) -> PublishedBundle:
    """Finalize one verified, connection-scoped Dataset V2 as an immutable bundle."""

    if config.server.instance_id is None:
        raise RecorderError("server.instance_id is missing; add one canonical UUID to recorder.toml before publishing")
    server_instance_id = _canonical_uuid(config.server.instance_id, "server.instance_id")
    if (fpv_path is None) != (fpv_timeline_path is None):
        raise RecorderError("--fpv and --fpv-timeline must be provided together")
    dataset = _load_dataset(dataset_path)
    states = _inspect_states(dataset.files["states.jsonl"].path)
    dataset_provenance = _validate_dataset_provenance(dataset, states)
    _verify_dataset_source_snapshot(
        dataset,
        states,
        dataset_provenance,
        captures_root=config.paths.captures,
    )
    scene_v1 = dataset.files["scene/scene-v1.sqlite3"].path
    scene_info = validate_scene_store(scene_v1)
    validate_scene_attachment_provenance(scene_info)
    expected_identity = (
        states.session_id,
        states.player_uuid,
        states.connection_id,
    )
    observed_identity = (
        scene_info.identity.session_id,
        scene_info.identity.player_uuid,
        scene_info.identity.connection_id,
    )
    if observed_identity != expected_identity or scene_info.ticks != states.ticks or not scene_info.coverage_complete:
        raise RecorderError("Scene V1 identity or complete tick coverage does not match authoritative states")
    sources = resolve_replay_segments(
        replays_root=config.paths.replays,
        session_id=states.session_id,
        player_uuid=states.player_uuid,
        connection_id=states.connection_id,
    )
    replay_segments = _replay_segments(scene_v1, scene_info, sources)
    identity = BundleIdentity(
        server_name=config.server.name,
        server_instance_id=server_instance_id,
        player_name=states.player_name,
        player_uuid=states.player_uuid,
        session_id=states.session_id,
        connection_id=states.connection_id,
        started_at=states.started_at,
        ended_at=states.ended_at,
        start_tick=states.ticks[0],
        end_tick=states.ticks[-1],
        sensitivity="private" if scene_info.sensitive else "internal",
        known_modality_gaps=KNOWN_MODALITY_GAPS,
    )
    action_records = validate_reconstructed_actions(
        dataset.files["actions.jsonl"].path,
        identity,
        expected_source_epochs=dataset_provenance.epoch_hashes,
    )
    if action_records != dataset_provenance.action_records:
        raise RecorderError("dataset action record count does not match actions.jsonl")

    config.paths.runtime.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="play-bundle-finalize-", dir=config.paths.runtime) as temporary:
        scene_v2_path = Path(temporary) / "scene.sqlite3"
        scene_v2 = finalize_scene_store_v2(
            scene_v1,
            dataset.files["states.jsonl"].path,
            scene_v2_path,
        )
        render = (
            _render_attachment(
                fpv_path,
                fpv_timeline_path,
                expected_frames=len(states.ticks),
            )
            if fpv_path is not None and fpv_timeline_path is not None
            else None
        )
        if scene_v2.sensitive != scene_info.sensitive:
            raise RecorderError("Scene V2 sensitivity does not match its verified Scene V1 source")
        request = BundleRequest(
            identity=identity,
            actions=dataset.files["actions.jsonl"],
            scene=ArtifactSource(scene_v2_path, scene_v2.sha256, scene_v2.size_bytes),
            replay_segments=replay_segments,
            render=render,
        )
        return publish_bundle(
            request,
            config.paths.bundles,
            scene_validator=validate_scene_store_v2,
            actions_validator=lambda path: validate_reconstructed_actions(
                path,
                identity,
                expected_source_epochs=dataset_provenance.epoch_hashes,
            ),
            render_validator=validate_fpv_render if render is not None else None,
        )


__all__ = ["finalize_dataset_bundle", "validate_fpv_render"]
