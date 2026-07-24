from __future__ import annotations

import hashlib
import json
import math
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, cast

from minerec.processing.bundle.model import (
    BUNDLE_EXTENSION,
    BUNDLE_FORMAT,
    BUNDLE_FORMAT_VERSION,
    SAFE_SEGMENT_ID_RE,
    SHA256_RE,
    TICK_RATE_HZ,
    ArtifactSource,
    BundleError,
    BundleIdentity,
    BundleLimits,
    BundleRequest,
)

ROLE_ACTIONS = "reconstructed-actions"
ROLE_SCENE = "scene-store"
ROLE_REPLAY = "flashback-replay-segment"
ROLE_VIDEO = "first-person-video"
ROLE_TIMELINE = "first-person-timeline"

REQUIRED_ENTRY_NAMES = ("actions.jsonl", "scene.sqlite3")
OPTIONAL_RENDER_ENTRY_NAMES = ("renders/fpv.mp4", "renders/fpv.timeline.jsonl")


@dataclass(frozen=True)
class InventoryEntry:
    path: str
    sha256: str
    size_bytes: int
    media_role: str


@dataclass(frozen=True)
class ValidatedMetadata:
    value: dict[str, Any]
    canonical_json: bytes
    identity: BundleIdentity
    inventory: tuple[InventoryEntry, ...]
    replays: tuple[dict[str, Any], ...]
    render: dict[str, Any] | None


def canonical_json_bytes(value: object) -> bytes:
    try:
        encoded = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError, RecursionError) as exc:
        raise BundleError("bundle metadata cannot be encoded as canonical JSON") from exc
    return (encoded + "\n").encode("utf-8")


def _required_text(value: object, description: str, *, path_safe: bool = False) -> str:
    if not isinstance(value, str):
        raise BundleError(f"{description} must be a string")
    normalized = unicodedata.normalize("NFC", value)
    if value != normalized:
        raise BundleError(f"{description} must use NFC Unicode normalization")
    if not value or value.strip() != value or len(value) > 255:
        raise BundleError(f"{description} is empty or invalid")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise BundleError(f"{description} contains a control character")
    if path_safe and (value in {".", ".."} or "/" in value or "\\" in value):
        raise BundleError(f"{description} is not a safe path component")
    return value


def _required_int(value: object, description: str, *, minimum: int | None = None) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise BundleError(f"{description} must be an integer")
    if minimum is not None and value < minimum:
        raise BundleError(f"{description} must be at least {minimum}")
    return value


def _required_bool(value: object, description: str) -> bool:
    if not isinstance(value, bool):
        raise BundleError(f"{description} must be a boolean")
    return value


def _required_sha256(value: object, description: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise BundleError(f"{description} must be a lowercase SHA-256 digest")
    return value


def _canonical_utc(value: object, description: str) -> str:
    text = _required_text(value, description)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise BundleError(f"{description} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise BundleError(f"{description} must include a UTC offset")
    parsed = parsed.astimezone(UTC)
    encoded = parsed.isoformat(timespec="microseconds").replace("+00:00", "Z")
    encoded = encoded.replace(".000000Z", "Z")
    if "." in encoded:
        head, fraction = encoded[:-1].split(".", 1)
        encoded = f"{head}.{fraction.rstrip('0')}Z"
    return encoded


def _parse_canonical_utc(value: object, description: str) -> tuple[str, datetime]:
    text = _required_text(value, description)
    canonical = _canonical_utc(text, description)
    if canonical != text:
        raise BundleError(f"{description} is not canonical UTC")
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return text, parsed


def _artifact(source: ArtifactSource, description: str) -> None:
    if not isinstance(source, ArtifactSource):
        raise BundleError(f"{description} must be an ArtifactSource")
    if not isinstance(source.path, Path):
        raise BundleError(f"{description} path must be a pathlib.Path")
    _required_sha256(source.sha256, f"{description} sha256")
    _required_int(source.size_bytes, f"{description} size", minimum=0)


def _identity_values(identity: BundleIdentity) -> dict[str, Any]:
    server_name = _required_text(identity.server_name, "server name", path_safe=True)
    server_instance_id = _required_text(
        identity.server_instance_id,
        "server instance id",
        path_safe=True,
    )
    player_name = _required_text(identity.player_name, "player name", path_safe=True)
    player_uuid = _required_text(identity.player_uuid, "player UUID", path_safe=True)
    session_id = _required_text(identity.session_id, "session id", path_safe=True)
    connection_id = _required_text(identity.connection_id, "connection id", path_safe=True)
    started_at = _canonical_utc(identity.started_at, "connection start")
    ended_at = _canonical_utc(identity.ended_at, "connection end")
    if datetime.fromisoformat(ended_at.replace("Z", "+00:00")) < datetime.fromisoformat(started_at.replace("Z", "+00:00")):
        raise BundleError("connection end precedes connection start")
    start_tick = _required_int(identity.start_tick, "start tick", minimum=0)
    end_tick = _required_int(identity.end_tick, "end tick", minimum=start_tick)
    sensitivity = _required_text(identity.sensitivity, "sensitivity")
    gaps = tuple(sorted({_required_text(gap, "known modality gap") for gap in identity.known_modality_gaps}))
    return {
        "server": {"name": server_name, "instance_id": server_instance_id},
        "player": {"name": player_name, "uuid": player_uuid},
        "session": {"id": session_id},
        "connection": {"id": connection_id},
        "utc_range": {"start": started_at, "end": ended_at},
        "tick_range": {
            "start": start_tick,
            "end": end_tick,
            "tick_rate_hz": TICK_RATE_HZ,
        },
        "sensitivity": sensitivity,
        "known_modality_gaps": list(gaps),
    }


def replay_entry_name(ordinal: int, segment_id: str) -> str:
    if SAFE_SEGMENT_ID_RE.fullmatch(segment_id) is None:
        raise BundleError("replay segment id is not safe for a bundle entry")
    return f"replays/{ordinal:06d}--{segment_id}.zip"


def _validate_request(request: BundleRequest) -> None:
    if not isinstance(request, BundleRequest):
        raise BundleError("bundle request has the wrong type")
    identity = request.identity
    values = _identity_values(identity)
    start_tick = values["tick_range"]["start"]
    end_tick = values["tick_range"]["end"]
    _artifact(request.actions, "actions")
    _artifact(request.scene, "scene")
    if not request.replay_segments:
        raise BundleError("a play bundle requires at least one replay segment")
    seen_segment_ids: set[str] = set()
    previous_end: int | None = None
    previous_start: int | None = None
    first_start: int | None = None
    for ordinal, segment in enumerate(request.replay_segments):
        if SAFE_SEGMENT_ID_RE.fullmatch(segment.segment_id) is None:
            raise BundleError("replay segment id is not safe for a bundle entry")
        if segment.segment_id in seen_segment_ids:
            raise BundleError("replay segment ids must be unique")
        seen_segment_ids.add(segment.segment_id)
        _artifact(segment.source, f"replay segment {ordinal}")
        segment_start = _required_int(
            segment.start_server_tick,
            f"replay segment {ordinal} start server tick",
            minimum=0,
        )
        segment_end = _required_int(
            segment.end_server_tick,
            f"replay segment {ordinal} end server tick",
            minimum=segment_start,
        )
        replay_start = _required_int(
            segment.start_replay_tick,
            f"replay segment {ordinal} start replay tick",
            minimum=0,
        )
        _required_int(
            segment.end_replay_tick,
            f"replay segment {ordinal} end replay tick",
            minimum=replay_start,
        )
        if previous_end is not None and segment_start > previous_end + 1:
            raise BundleError("replay segment server-tick coverage has a gap")
        if previous_start is not None and segment_start < previous_start:
            raise BundleError("replay segments are not ordered by server tick")
        first_start = segment_start if first_start is None else first_start
        previous_start = segment_start
        previous_end = max(previous_end or segment_end, segment_end)
    if first_start is None or first_start > start_tick or previous_end is None or previous_end < end_tick:
        raise BundleError("replay segments do not cover the connection tick range")

    render = request.render
    if render is None:
        return
    _artifact(render.video, "FPV video")
    _artifact(render.timeline, "FPV timeline")
    expected_frames = end_tick - start_tick + 1
    if _required_int(render.frame_count, "render frame count", minimum=1) != expected_frames:
        raise BundleError("a v1 render must contain exactly one frame per connection tick")
    _required_int(render.width, "render width", minimum=1)
    _required_int(render.height, "render height", minimum=1)
    if render.codec != "h264" or render.pixel_format != "yuv420p":
        raise BundleError("a v1 render must use H.264 with yuv420p pixels")
    if render.fps != TICK_RATE_HZ:
        raise BundleError("a v1 render must use constant 20 FPS")
    if render.fast_start is not True or render.audio is not False:
        raise BundleError("a v1 render must be fast-start and contain no audio")


def build_metadata(request: BundleRequest) -> dict[str, Any]:
    _validate_request(request)
    identity = _identity_values(request.identity)
    inventory: list[dict[str, Any]] = [
        {
            "path": "actions.jsonl",
            "sha256": request.actions.sha256,
            "size_bytes": request.actions.size_bytes,
            "media_role": ROLE_ACTIONS,
        },
        {
            "path": "scene.sqlite3",
            "sha256": request.scene.sha256,
            "size_bytes": request.scene.size_bytes,
            "media_role": ROLE_SCENE,
        },
    ]
    replay_values: list[dict[str, Any]] = []
    for ordinal, segment in enumerate(request.replay_segments):
        path = replay_entry_name(ordinal, segment.segment_id)
        descriptor = {
            "ordinal": ordinal,
            "segment_id": segment.segment_id,
            "path": path,
            "sha256": segment.source.sha256,
            "size_bytes": segment.source.size_bytes,
            "server_ticks": {
                "start": segment.start_server_tick,
                "end": segment.end_server_tick,
            },
            "replay_ticks": {
                "start": segment.start_replay_tick,
                "end": segment.end_replay_tick,
            },
        }
        replay_values.append(descriptor)
        inventory.append(
            {
                "path": path,
                "sha256": segment.source.sha256,
                "size_bytes": segment.source.size_bytes,
                "media_role": ROLE_REPLAY,
            }
        )

    render_value: dict[str, Any] | None = None
    if request.render is not None:
        render = request.render
        render_value = {
            "video": {
                "path": "renders/fpv.mp4",
                "sha256": render.video.sha256,
                "size_bytes": render.video.size_bytes,
                "media_type": "video/mp4",
                "codec": render.codec,
                "pixel_format": render.pixel_format,
                "fps": render.fps,
                "frame_count": render.frame_count,
                "width": render.width,
                "height": render.height,
                "fast_start": render.fast_start,
                "audio": render.audio,
            },
            "timeline": {
                "path": "renders/fpv.timeline.jsonl",
                "sha256": render.timeline.sha256,
                "size_bytes": render.timeline.size_bytes,
                "media_type": "application/x-ndjson",
                "records": render.frame_count,
            },
        }
        inventory.extend(
            (
                {
                    "path": "renders/fpv.mp4",
                    "sha256": render.video.sha256,
                    "size_bytes": render.video.size_bytes,
                    "media_role": ROLE_VIDEO,
                },
                {
                    "path": "renders/fpv.timeline.jsonl",
                    "sha256": render.timeline.sha256,
                    "size_bytes": render.timeline.size_bytes,
                    "media_role": ROLE_TIMELINE,
                },
            )
        )

    metadata: dict[str, Any] = {
        "format": BUNDLE_FORMAT,
        "format_version": BUNDLE_FORMAT_VERSION,
        **identity,
        "replays": replay_values,
        "render": render_value,
        "inventory": sorted(inventory, key=lambda item: item["path"]),
    }
    metadata["bundle_id"] = deterministic_bundle_id(metadata)
    return metadata


def deterministic_bundle_id(metadata: Mapping[str, Any]) -> str:
    payload = dict(metadata)
    payload.pop("bundle_id", None)
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def _exact_keys(value: Mapping[str, Any], expected: set[str], description: str) -> None:
    if set(value) != expected:
        raise BundleError(f"{description} has unexpected or missing fields")


def _object(value: object, description: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise BundleError(f"{description} must be an object")
    return cast(dict[str, Any], value)


def _canonical_entry_name(value: object, description: str) -> str:
    name = _required_text(value, description)
    if "\\" in name or name.startswith("/"):
        raise BundleError(f"{description} is not a canonical POSIX path")
    path = PurePosixPath(name)
    parts = name.split("/")
    drive_prefixed = len(parts[0]) == 2 and parts[0][0].isalpha() and parts[0][1] == ":"
    if path.is_absolute() or drive_prefixed or not parts or any(part in {"", ".", ".."} for part in parts):
        raise BundleError(f"{description} is not a canonical POSIX path")
    return name


def _identity_from_metadata(metadata: Mapping[str, Any]) -> BundleIdentity:
    server = _object(metadata.get("server"), "server identity")
    player = _object(metadata.get("player"), "player identity")
    session = _object(metadata.get("session"), "session identity")
    connection = _object(metadata.get("connection"), "connection identity")
    utc_range = _object(metadata.get("utc_range"), "UTC range")
    tick_range = _object(metadata.get("tick_range"), "tick range")
    _exact_keys(server, {"name", "instance_id"}, "server identity")
    _exact_keys(player, {"name", "uuid"}, "player identity")
    _exact_keys(session, {"id"}, "session identity")
    _exact_keys(connection, {"id"}, "connection identity")
    _exact_keys(utc_range, {"start", "end"}, "UTC range")
    _exact_keys(tick_range, {"start", "end", "tick_rate_hz"}, "tick range")
    started_at, started = _parse_canonical_utc(utc_range.get("start"), "connection start")
    ended_at, ended = _parse_canonical_utc(utc_range.get("end"), "connection end")
    if ended < started:
        raise BundleError("connection end precedes connection start")
    start_tick = _required_int(tick_range.get("start"), "start tick", minimum=0)
    end_tick = _required_int(tick_range.get("end"), "end tick", minimum=start_tick)
    if _required_int(tick_range.get("tick_rate_hz"), "tick rate", minimum=1) != TICK_RATE_HZ:
        raise BundleError("bundle tick rate must be 20 Hz")
    gaps_value = metadata.get("known_modality_gaps")
    if not isinstance(gaps_value, list):
        raise BundleError("known modality gaps must be an array")
    gaps = tuple(_required_text(gap, "known modality gap") for gap in gaps_value)
    if tuple(sorted(set(gaps))) != gaps:
        raise BundleError("known modality gaps must be sorted and unique")
    return BundleIdentity(
        server_name=_required_text(server.get("name"), "server name", path_safe=True),
        server_instance_id=_required_text(
            server.get("instance_id"),
            "server instance id",
            path_safe=True,
        ),
        player_name=_required_text(player.get("name"), "player name", path_safe=True),
        player_uuid=_required_text(player.get("uuid"), "player UUID", path_safe=True),
        session_id=_required_text(session.get("id"), "session id", path_safe=True),
        connection_id=_required_text(
            connection.get("id"),
            "connection id",
            path_safe=True,
        ),
        started_at=started_at,
        ended_at=ended_at,
        start_tick=start_tick,
        end_tick=end_tick,
        sensitivity=_required_text(metadata.get("sensitivity"), "sensitivity"),
        known_modality_gaps=gaps,
    )


def _inventory(metadata: Mapping[str, Any]) -> tuple[InventoryEntry, ...]:
    raw_inventory = metadata.get("inventory")
    if not isinstance(raw_inventory, list):
        raise BundleError("bundle inventory must be an array")
    values: list[InventoryEntry] = []
    paths: set[str] = set()
    for index, raw in enumerate(raw_inventory):
        item = _object(raw, f"inventory item {index}")
        _exact_keys(
            item,
            {"path", "sha256", "size_bytes", "media_role"},
            f"inventory item {index}",
        )
        path = _canonical_entry_name(item.get("path"), f"inventory item {index} path")
        if path == "metadata.json" or path in paths:
            raise BundleError("bundle inventory contains a duplicate or reserved path")
        paths.add(path)
        values.append(
            InventoryEntry(
                path=path,
                sha256=_required_sha256(
                    item.get("sha256"),
                    f"inventory item {index} sha256",
                ),
                size_bytes=_required_int(
                    item.get("size_bytes"),
                    f"inventory item {index} size",
                    minimum=0,
                ),
                media_role=_required_text(
                    item.get("media_role"),
                    f"inventory item {index} media role",
                ),
            )
        )
    if [item.path for item in values] != sorted(paths):
        raise BundleError("bundle inventory must be sorted by path")
    return tuple(values)


def _descriptor_file(
    value: Mapping[str, Any],
    inventory_by_path: Mapping[str, InventoryEntry],
    *,
    role: str,
    description: str,
) -> InventoryEntry:
    path = _canonical_entry_name(value.get("path"), f"{description} path")
    entry = inventory_by_path.get(path)
    if entry is None or entry.media_role != role:
        raise BundleError(f"{description} does not match the inventory")
    if _required_sha256(value.get("sha256"), f"{description} sha256") != entry.sha256:
        raise BundleError(f"{description} hash does not match the inventory")
    if _required_int(value.get("size_bytes"), f"{description} size", minimum=0) != entry.size_bytes:
        raise BundleError(f"{description} size does not match the inventory")
    return entry


def _replays(
    metadata: Mapping[str, Any],
    identity: BundleIdentity,
    inventory_by_path: Mapping[str, InventoryEntry],
) -> tuple[dict[str, Any], ...]:
    raw_replays = metadata.get("replays")
    if not isinstance(raw_replays, list) or not raw_replays:
        raise BundleError("bundle must describe at least one replay segment")
    values: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    first_start: int | None = None
    previous_end: int | None = None
    previous_start: int | None = None
    for ordinal, raw in enumerate(raw_replays):
        value = _object(raw, f"replay descriptor {ordinal}")
        _exact_keys(
            value,
            {
                "ordinal",
                "segment_id",
                "path",
                "sha256",
                "size_bytes",
                "server_ticks",
                "replay_ticks",
            },
            f"replay descriptor {ordinal}",
        )
        if _required_int(value.get("ordinal"), "replay ordinal", minimum=0) != ordinal:
            raise BundleError("replay ordinals must be contiguous and ordered")
        segment_id = _required_text(value.get("segment_id"), "replay segment id")
        if SAFE_SEGMENT_ID_RE.fullmatch(segment_id) is None or segment_id in seen_ids:
            raise BundleError("replay segment ids must be safe and unique")
        seen_ids.add(segment_id)
        expected_path = replay_entry_name(ordinal, segment_id)
        entry = _descriptor_file(
            value,
            inventory_by_path,
            role=ROLE_REPLAY,
            description=f"replay descriptor {ordinal}",
        )
        if entry.path != expected_path:
            raise BundleError("replay descriptor path does not match its ordinal and segment id")
        server_ticks = _object(value.get("server_ticks"), "replay server ticks")
        replay_ticks = _object(value.get("replay_ticks"), "replay-local ticks")
        _exact_keys(server_ticks, {"start", "end"}, "replay server ticks")
        _exact_keys(replay_ticks, {"start", "end"}, "replay-local ticks")
        segment_start = _required_int(server_ticks.get("start"), "replay start server tick", minimum=0)
        segment_end = _required_int(
            server_ticks.get("end"),
            "replay end server tick",
            minimum=segment_start,
        )
        replay_start = _required_int(replay_ticks.get("start"), "replay start tick", minimum=0)
        _required_int(replay_ticks.get("end"), "replay end tick", minimum=replay_start)
        if previous_end is not None and segment_start > previous_end + 1:
            raise BundleError("replay segment server-tick coverage has a gap")
        if previous_start is not None and segment_start < previous_start:
            raise BundleError("replay descriptors are not ordered by server tick")
        first_start = segment_start if first_start is None else first_start
        previous_start = segment_start
        previous_end = max(previous_end or segment_end, segment_end)
        values.append(value)
    if first_start is None or first_start > identity.start_tick or previous_end is None or previous_end < identity.end_tick:
        raise BundleError("replay descriptors do not cover the connection tick range")
    return tuple(values)


def _render(
    metadata: Mapping[str, Any],
    identity: BundleIdentity,
    inventory_by_path: Mapping[str, InventoryEntry],
) -> dict[str, Any] | None:
    raw_render = metadata.get("render")
    if raw_render is None:
        return None
    render = _object(raw_render, "render descriptor")
    _exact_keys(render, {"video", "timeline"}, "render descriptor")
    video = _object(render.get("video"), "render video descriptor")
    timeline = _object(render.get("timeline"), "render timeline descriptor")
    _exact_keys(
        video,
        {
            "path",
            "sha256",
            "size_bytes",
            "media_type",
            "codec",
            "pixel_format",
            "fps",
            "frame_count",
            "width",
            "height",
            "fast_start",
            "audio",
        },
        "render video descriptor",
    )
    _exact_keys(
        timeline,
        {"path", "sha256", "size_bytes", "media_type", "records"},
        "render timeline descriptor",
    )
    video_entry = _descriptor_file(
        video,
        inventory_by_path,
        role=ROLE_VIDEO,
        description="render video descriptor",
    )
    timeline_entry = _descriptor_file(
        timeline,
        inventory_by_path,
        role=ROLE_TIMELINE,
        description="render timeline descriptor",
    )
    if video_entry.path != OPTIONAL_RENDER_ENTRY_NAMES[0] or timeline_entry.path != OPTIONAL_RENDER_ENTRY_NAMES[1]:
        raise BundleError("render descriptor uses a non-v1 path")
    if video.get("media_type") != "video/mp4" or timeline.get("media_type") != "application/x-ndjson":
        raise BundleError("render descriptor has an invalid media type")
    if video.get("codec") != "h264" or video.get("pixel_format") != "yuv420p":
        raise BundleError("v1 render descriptor must declare H.264/yuv420p")
    if _required_int(video.get("fps"), "render FPS", minimum=1) != TICK_RATE_HZ:
        raise BundleError("v1 render descriptor must declare 20 FPS")
    expected_frames = identity.end_tick - identity.start_tick + 1
    frame_count = _required_int(video.get("frame_count"), "render frame count", minimum=1)
    if frame_count != expected_frames:
        raise BundleError("v1 render frame count must equal the connection tick count")
    if _required_int(timeline.get("records"), "render timeline record count", minimum=1) != frame_count:
        raise BundleError("render timeline record count does not match the video")
    _required_int(video.get("width"), "render width", minimum=1)
    _required_int(video.get("height"), "render height", minimum=1)
    if not _required_bool(video.get("fast_start"), "render fast-start"):
        raise BundleError("v1 render must be fast-start")
    if _required_bool(video.get("audio"), "render audio"):
        raise BundleError("v1 render must not contain audio")
    return render


def validate_metadata(metadata_bytes: bytes) -> ValidatedMetadata:
    try:
        value = json.loads(metadata_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise BundleError("metadata.json is not valid JSON") from exc
    if not isinstance(value, dict):
        raise BundleError("metadata.json must contain an object")
    if canonical_json_bytes(value) != metadata_bytes:
        raise BundleError("metadata.json is not canonical JSON")
    _exact_keys(
        value,
        {
            "format",
            "format_version",
            "bundle_id",
            "server",
            "player",
            "session",
            "connection",
            "utc_range",
            "tick_range",
            "sensitivity",
            "known_modality_gaps",
            "replays",
            "render",
            "inventory",
        },
        "bundle metadata",
    )
    if value.get("format") != BUNDLE_FORMAT or value.get("format_version") != BUNDLE_FORMAT_VERSION:
        raise BundleError("unsupported play bundle format or version")
    bundle_id = _required_sha256(value.get("bundle_id"), "bundle id")
    if deterministic_bundle_id(value) != bundle_id:
        raise BundleError("bundle id does not match canonical identity and artifact hashes")
    identity = _identity_from_metadata(value)
    inventory = _inventory(value)
    inventory_by_path = {item.path: item for item in inventory}
    if inventory_by_path.get("actions.jsonl") is None or inventory_by_path["actions.jsonl"].media_role != ROLE_ACTIONS:
        raise BundleError("bundle inventory lacks required reconstructed actions")
    if inventory_by_path.get("scene.sqlite3") is None or inventory_by_path["scene.sqlite3"].media_role != ROLE_SCENE:
        raise BundleError("bundle inventory lacks the required scene store")
    replays = _replays(value, identity, inventory_by_path)
    render = _render(value, identity, inventory_by_path)
    expected_paths = {"actions.jsonl", "scene.sqlite3"}
    expected_paths.update(descriptor["path"] for descriptor in replays)
    if render is not None:
        expected_paths.update(OPTIONAL_RENDER_ENTRY_NAMES)
    if set(inventory_by_path) != expected_paths:
        raise BundleError("bundle inventory contains an undeclared media role or path")
    return ValidatedMetadata(
        value=value,
        canonical_json=metadata_bytes,
        identity=identity,
        inventory=inventory,
        replays=replays,
        render=render,
    )


def validate_limits(limits: BundleLimits) -> None:
    integer_values = (
        limits.max_archive_bytes,
        limits.max_entries,
        limits.max_central_directory_bytes,
        limits.max_metadata_bytes,
        limits.max_actions_bytes,
        limits.max_scene_bytes,
        limits.max_replay_bytes,
        limits.max_video_bytes,
        limits.max_timeline_bytes,
        limits.max_total_uncompressed_bytes,
        limits.max_json_line_bytes,
    )
    if any(not isinstance(value, int) or isinstance(value, bool) or value <= 0 for value in integer_values):
        raise BundleError("bundle limits must be positive integers")
    if not isinstance(limits.max_compression_ratio, (int, float)) or isinstance(limits.max_compression_ratio, bool) or not math.isfinite(limits.max_compression_ratio) or limits.max_compression_ratio < 1:
        raise BundleError("bundle compression-ratio limit must be finite and at least one")


def bundle_output_path(artifacts_root: Path, identity: BundleIdentity, bundle_id: str) -> Path:
    values = _identity_values(identity)
    _required_sha256(bundle_id, "bundle id")
    server_component = f"{values['server']['name']}--{values['server']['instance_id']}"
    player_component = f"{values['player']['name']}--{values['player']['uuid']}"
    play_component = f"{values['utc_range']['start']}--{values['connection']['id']}"
    for component, description in (
        (server_component, "server layout component"),
        (player_component, "player layout component"),
        (play_component, "play layout component"),
    ):
        _required_text(component, description, path_safe=True)
        if len(component.encode("utf-8")) > 255:
            raise BundleError(f"{description} is too long")
    return artifacts_root / "v1" / server_component / "players" / player_component / "plays" / play_component / f"{bundle_id}{BUNDLE_EXTENSION}"
