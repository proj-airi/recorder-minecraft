from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from minerec.config import ENV_RENDER_JOB
from minerec.errors import RecorderError
from minerec.processing.capture.episodes import sha256_file, validate_episode
from minerec.processing.capture.exporter import (
    CANONICAL_RENDER_RESULT_TYPE,
    _load_frame_attachments,
)
from minerec.processing.render.hud import (
    hud_result_envelope,
    validate_hud_result_envelope,
    validate_hud_sidecar_envelope,
)
from minerec.processing.render.job import (
    OWNER,
    RENDER_JOB_TYPE,
    RenderJobResult,
    _detect_replay_format,
    _owned_render_directory,
    _select_connection,
    _stable_file_digest,
)
from minerec.render.control.contract import FULL_CLIENT_PRESENTATION_CONTRACT

PORTABLE_REQUEST_TYPE = "mc-recorder-portable-render-request-v1"
RENDER_BUNDLE_TYPE = "mc-recorder-render-bundle-v1"
RENDER_IMPORT_TYPE = "mc-recorder-render-import-v1"
MAX_REQUEST_BYTES = 256 * 1024
MAX_BUNDLE_MANIFEST_BYTES = 256 * 1024
MAX_PAYLOAD_INDEX_BYTES = 64 * 1024 * 1024
MAX_PAYLOAD_INDEX_LINE_BYTES = 16 * 1024
DEFAULT_MAX_FILES = 100_010
DEFAULT_MAX_TOTAL_BYTES = 256 * 1024 * 1024 * 1024
MAX_IDENTIFIER_LENGTH = 160
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SEGMENT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,159}$")
_FRAME_RE = re.compile(r"^frames/frame_[0-9]+\.png$")
_ALLOWED_POLICIES = {"exact", "intersection"}


@dataclass(frozen=True)
class PortableRenderRequest:
    path: Path | None
    data: dict[str, Any]
    sha256: str
    request_id: str


@dataclass(frozen=True)
class RenderBundle:
    directory: Path
    manifest: Path
    payload_index: Path
    sha256: str
    request_id: str


@dataclass(frozen=True)
class ImportedRenderResult:
    directory: Path
    result: Path
    manifest: Path
    request_id: str
    status: str
    reused: bool


@dataclass(frozen=True)
class _PayloadFile:
    path: str
    size_bytes: int
    sha256: str

    def row(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
        }


def _json_bytes(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _required_keys(value: Mapping[str, Any], required: set[str], optional: set[str], context: str) -> None:
    keys = set(value)
    missing = required - keys
    extras = keys - required - optional
    if missing:
        raise RecorderError(f"{context} is missing fields: {', '.join(sorted(missing))}")
    if extras:
        raise RecorderError(f"{context} contains unsupported fields: {', '.join(sorted(extras))}")


def _object(value: Any, context: str) -> dict[str, Any]:  # noqa: ANN401
    if not isinstance(value, dict):
        raise RecorderError(f"{context} must be an object")
    return value


def _integer(value: Any, context: str, minimum: int, maximum: int = 2**63 - 1) -> int:  # noqa: ANN401
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum or value > maximum:
        raise RecorderError(f"{context} must be an integer in {minimum}..{maximum}")
    return value


def _uuid(value: Any, context: str) -> str:  # noqa: ANN401
    try:
        normalized = str(uuid.UUID(value))
    except (ValueError, TypeError, AttributeError) as exc:
        raise RecorderError(f"{context} must be a UUID") from exc
    if value != normalized:
        raise RecorderError(f"{context} must use the canonical UUID spelling")
    return normalized


def _opaque(value: Any, context: str) -> str:  # noqa: ANN401
    if not isinstance(value, str) or not value or len(value) > MAX_IDENTIFIER_LENGTH or any(ord(character) < 0x20 for character in value) or "/" in value or "\\" in value:
        raise RecorderError(f"{context} must be a bounded opaque identifier")
    return value


def _validate_request(value: Any) -> dict[str, Any]:  # noqa: ANN401
    request = _object(value, "portable render request")
    _required_keys(
        request,
        {
            "schema_version",
            "request_type",
            "request_id",
            "episode",
            "subject",
            "timeline",
            "source_replay",
            "render",
        },
        {"structured_hud"},
        "portable render request",
    )
    if request.get("schema_version") != 1 or isinstance(request.get("schema_version"), bool):
        raise RecorderError("portable render request schema_version must be 1")
    if request.get("request_type") != PORTABLE_REQUEST_TYPE:
        raise RecorderError("unsupported portable render request type")
    _uuid(request.get("request_id"), "portable render request request_id")

    episode = _object(request.get("episode"), "portable render request episode")
    _required_keys(episode, {"session_id", "manifest_sha256"}, set(), "request episode")
    _opaque(episode.get("session_id"), "request episode session_id")
    if not isinstance(episode.get("manifest_sha256"), str) or _SHA256_RE.fullmatch(episode["manifest_sha256"]) is None:
        raise RecorderError("request episode manifest_sha256 must be lowercase SHA-256")

    subject = _object(request.get("subject"), "portable render request subject")
    _required_keys(subject, {"player_uuid", "connection_id"}, set(), "request subject")
    _uuid(subject.get("player_uuid"), "request subject player_uuid")
    _uuid(subject.get("connection_id"), "request subject connection_id")

    source = _object(request.get("source_replay"), "portable render request source_replay")
    _required_keys(
        source,
        {"segment_id", "segment_ordinal", "format", "sha256", "size_bytes"},
        set(),
        "request source_replay",
    )
    segment_id = source.get("segment_id")
    if not isinstance(segment_id, str) or _SEGMENT_ID_RE.fullmatch(segment_id) is None:
        raise RecorderError("request source_replay segment_id is invalid")
    _integer(source.get("segment_ordinal"), "request segment_ordinal", 0, 2**31 - 1)
    if source.get("format") != "flashback":
        raise RecorderError("portable rendering supports Flashback replay ZIPs only")
    if not isinstance(source.get("sha256"), str) or _SHA256_RE.fullmatch(source["sha256"]) is None:
        raise RecorderError("request source_replay sha256 must be lowercase SHA-256")
    _integer(source.get("size_bytes"), "request source_replay size_bytes", 1)

    timeline = _object(request.get("timeline"), "portable render request timeline")
    _required_keys(
        timeline,
        {
            "global_start_tick",
            "global_end_tick",
            "observed_connection_range",
            "server_tick_rate_hz",
            "range_policy",
            "newer_cutoff",
        },
        set(),
        "request timeline",
    )
    first_tick = _integer(timeline.get("global_start_tick"), "request global_start_tick", 0)
    last_tick = _integer(timeline.get("global_end_tick"), "request global_end_tick", 0)
    if first_tick > last_tick:
        raise RecorderError("request global tick range is reversed")
    observed = timeline.get("observed_connection_range")
    if not isinstance(observed, list) or len(observed) != 2:
        raise RecorderError("request observed_connection_range must contain two ticks")
    observed_first = _integer(observed[0], "request observed range start", 0)
    observed_last = _integer(observed[1], "request observed range end", 0)
    if observed_first > first_tick or observed_last < last_tick or observed_first > observed_last:
        raise RecorderError("request range is outside its observed connection range")
    if timeline.get("server_tick_rate_hz") != 20:
        raise RecorderError("portable render requests require a 20 Hz server timeline")
    policy = timeline.get("range_policy")
    if policy not in _ALLOWED_POLICIES:
        raise RecorderError("request range_policy must be exact or intersection")
    cutoff = timeline.get("newer_cutoff")
    if cutoff is not None:
        cutoff = _integer(cutoff, "request newer_cutoff", 0)
        if policy != "intersection":
            raise RecorderError("newer_cutoff is valid only with intersection range_policy")
        if cutoff < first_tick or cutoff > last_tick + 1:
            raise RecorderError("request newer_cutoff is outside the requested range")

    render = _object(request.get("render"), "portable render request render")
    _required_keys(
        render,
        {"width", "height", "fps", "camera"},
        {"no_gui", "presentation_contract"},
        "request render",
    )
    _integer(render.get("width"), "request render width", 64, 16_384)
    _integer(render.get("height"), "request render height", 64, 16_384)
    if render.get("fps") != 20:
        raise RecorderError("portable renderer v1 supports exactly 20 FPS")
    if render.get("camera") != "first_person_head":
        raise RecorderError("portable renderer v1 supports first_person_head only")
    if "no_gui" in render and not isinstance(render["no_gui"], bool):
        raise RecorderError("request render no_gui must be a boolean")
    presentation_contract = render.get("presentation_contract")
    if presentation_contract is not None:
        if presentation_contract != FULL_CLIENT_PRESENTATION_CONTRACT:
            raise RecorderError("request render presentation_contract is unsupported")
        if render.get("no_gui", True):
            raise RecorderError("request render presentation_contract requires no_gui=false")
    structured_hud = request.get("structured_hud")
    if structured_hud is not None:
        structured_hud = validate_hud_sidecar_envelope(structured_hud, "portable request structured_hud")
        if presentation_contract != FULL_CLIENT_PRESENTATION_CONTRACT:
            raise RecorderError("portable request structured_hud requires the current presentation contract")
        if structured_hud["session_id"] != episode["session_id"]:
            raise RecorderError("portable request structured_hud session is inconsistent")
        if structured_hud["player_uuid"] != subject["player_uuid"] or structured_hud["connection_id"] != subject["connection_id"]:
            raise RecorderError("portable request structured_hud subject is inconsistent")
        if structured_hud["first_tick"] > first_tick or structured_hud["last_tick"] < last_tick:
            raise RecorderError("portable request structured_hud does not cover its render range")
    elif presentation_contract == FULL_CLIENT_PRESENTATION_CONTRACT:
        raise RecorderError("current full-client presentation requests require structured_hud")
    return request


def _request_from_value(
    request: Path | Mapping[str, Any] | PortableRenderRequest,
) -> PortableRenderRequest:
    if isinstance(request, PortableRenderRequest):
        validated = _validate_request(request.data)
        if request.path is not None:
            loaded = load_portable_render_request(request.path)
            if loaded.sha256 != request.sha256 or loaded.data != validated:
                raise RecorderError("portable request object no longer matches its persisted bytes")
        elif _sha256_bytes(_json_bytes(validated)) != request.sha256:
            raise RecorderError("portable request object has an invalid byte binding")
        if request.request_id != validated["request_id"]:
            raise RecorderError("portable request object has an inconsistent request_id")
        return request
    if isinstance(request, Path):
        return load_portable_render_request(request)
    validated = _validate_request(dict(request))
    encoded = _json_bytes(validated)
    if len(encoded) > MAX_REQUEST_BYTES:
        raise RecorderError("portable render request exceeds the size limit")
    return PortableRenderRequest(
        path=None,
        data=validated,
        sha256=_sha256_bytes(encoded),
        request_id=validated["request_id"],
    )


def create_portable_render_request(
    episode: Path,
    replay: Path,
    *,
    segment_id: str,
    segment_ordinal: int,
    player_uuid: str,
    connection_id: str | None,
    width: int,
    height: int,
    fps: int = 20,
    first_tick: int | None = None,
    last_tick: int | None = None,
    request_id: str | None = None,
    range_policy: str = "exact",
    newer_cutoff: int | None = None,
    no_gui: bool = False,
    presentation_contract: str | None = None,
    structured_hud: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    validation = validate_episode(episode)
    if not validation.valid or validation.sealed_epochs == 0:
        raise RecorderError("episode must have at least one valid sealed epoch before rendering")
    try:
        normalized_player = str(uuid.UUID(player_uuid))
    except (ValueError, TypeError, AttributeError) as exc:
        raise RecorderError(f"invalid player UUID: {player_uuid!r}") from exc
    if not isinstance(no_gui, bool):
        raise RecorderError("no_gui must be a boolean")
    connection, observed_first, observed_last = _select_connection(episode, normalized_player, connection_id)
    selected_first = observed_first if first_tick is None else first_tick
    selected_last = observed_last if last_tick is None else last_tick
    if (
        not isinstance(selected_first, int)
        or isinstance(selected_first, bool)
        or not isinstance(selected_last, int)
        or isinstance(selected_last, bool)
        or selected_first < observed_first
        or selected_last > observed_last
        or selected_first > selected_last
    ):
        raise RecorderError(f"render tick range must be within connection range {observed_first}..{observed_last}")
    unresolved_replay = replay.expanduser()
    if unresolved_replay.is_symlink() or not unresolved_replay.is_file():
        raise RecorderError(f"replay not found or is symlinked: {unresolved_replay}")
    resolved_replay = unresolved_replay.resolve()
    replay_format = _detect_replay_format(resolved_replay)
    if replay_format != "flashback":
        raise RecorderError("portable rendering supports Flashback replay ZIPs only")
    replay_sha, replay_bytes = _stable_file_digest(resolved_replay, "replay archive")
    if not isinstance(segment_id, str) or _SEGMENT_ID_RE.fullmatch(segment_id) is None:
        raise RecorderError("segment_id must be an opaque path-free identifier")
    try:
        request_uuid = str(uuid.uuid4()) if request_id is None else str(uuid.UUID(request_id))
    except (ValueError, TypeError, AttributeError) as exc:
        raise RecorderError(f"invalid render request UUID: {request_id!r}") from exc
    value: dict[str, Any] = {
        "schema_version": 1,
        "request_type": PORTABLE_REQUEST_TYPE,
        "request_id": request_uuid,
        "episode": {
            "session_id": validation.session_id,
            "manifest_sha256": sha256_file(episode / "manifest.json"),
        },
        "subject": {"player_uuid": normalized_player, "connection_id": connection},
        "timeline": {
            "global_start_tick": selected_first,
            "global_end_tick": selected_last,
            "observed_connection_range": [observed_first, observed_last],
            "server_tick_rate_hz": 20,
            "range_policy": range_policy,
            "newer_cutoff": newer_cutoff,
        },
        "source_replay": {
            "segment_id": segment_id,
            "segment_ordinal": segment_ordinal,
            "format": replay_format,
            "sha256": replay_sha,
            "size_bytes": replay_bytes,
        },
        "render": {
            "width": width,
            "height": height,
            "fps": fps,
            "camera": "first_person_head",
            "no_gui": no_gui,
        },
    }
    if presentation_contract is not None:
        value["render"]["presentation_contract"] = presentation_contract
    if structured_hud is not None:
        value["structured_hud"] = dict(structured_hud)
    return _validate_request(value)


def write_portable_render_request(path: Path, request: Mapping[str, Any]) -> PortableRenderRequest:
    validated = _validate_request(dict(request))
    encoded = _json_bytes(validated)
    if len(encoded) > MAX_REQUEST_BYTES:
        raise RecorderError("portable render request exceeds the size limit")
    target = path.expanduser()
    if target.is_symlink():
        raise RecorderError(f"portable render request path may not be a symlink: {target}")
    target = target.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        existing = load_portable_render_request(target)
        if target.read_bytes() != encoded and not _requests_match_with_legacy_gui_default(existing.data, validated):
            raise RecorderError(f"portable render request already exists with different bytes: {target}")
        return existing
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.tmp-", dir=target.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, target)
        except FileExistsError:
            existing = load_portable_render_request(target)
            if target.read_bytes() != encoded and not _requests_match_with_legacy_gui_default(existing.data, validated):
                raise RecorderError(f"portable render request was concurrently published with different bytes: {target}")
            return existing
    finally:
        temporary.unlink(missing_ok=True)
    return PortableRenderRequest(
        path=target,
        data=validated,
        sha256=_sha256_bytes(encoded),
        request_id=validated["request_id"],
    )


def _requests_match_with_legacy_gui_default(existing: Mapping[str, Any], requested: Mapping[str, Any]) -> bool:
    existing_render = existing.get("render")
    requested_render = requested.get("render")
    existing_has_no_gui = isinstance(existing_render, dict) and "no_gui" in existing_render
    requested_has_no_gui = isinstance(requested_render, dict) and "no_gui" in requested_render
    if existing_has_no_gui == requested_has_no_gui:
        return False
    explicit_render = existing_render if existing_has_no_gui else requested_render
    if not isinstance(explicit_render, dict) or explicit_render.get("no_gui") is not True:
        return False
    existing_value = json.loads(json.dumps(existing))
    requested_value = json.loads(json.dumps(requested))
    for value in (existing_value, requested_value):
        render = value.get("render")
        if isinstance(render, dict) and "no_gui" not in render:
            render["no_gui"] = True
    return existing_value == requested_value


def load_portable_render_request(path: Path) -> PortableRenderRequest:
    unresolved = path.expanduser()
    if unresolved.is_symlink() or not unresolved.is_file():
        raise RecorderError(f"portable render request is missing or symlinked: {unresolved}")
    resolved = unresolved.resolve()
    try:
        if resolved.stat().st_size > MAX_REQUEST_BYTES:
            raise RecorderError("portable render request exceeds the size limit")
        encoded = resolved.read_bytes()
        value = json.loads(encoded)
    except OSError as exc:
        raise RecorderError(f"cannot read portable render request: {resolved}") from exc
    except (ValueError, RecursionError) as exc:
        raise RecorderError(f"portable render request is invalid JSON: {resolved}") from exc
    validated = _validate_request(value)
    return PortableRenderRequest(
        path=resolved,
        data=validated,
        sha256=_sha256_bytes(encoded),
        request_id=validated["request_id"],
    )


def _verify_replay(path: Path, request: PortableRenderRequest) -> Path:
    unresolved = path.expanduser()
    if unresolved.is_symlink() or not unresolved.is_file():
        raise RecorderError(f"authoritative replay is missing or symlinked: {unresolved}")
    resolved = unresolved.resolve()
    if _detect_replay_format(resolved) != request.data["source_replay"]["format"]:
        raise RecorderError("replay format does not match the portable request")
    digest, size = _stable_file_digest(resolved, "authoritative replay")
    source = request.data["source_replay"]
    if digest != source["sha256"] or size != source["size_bytes"]:
        raise RecorderError("replay size or SHA-256 does not match the portable request")
    return resolved


def _verify_hud_sidecar(path: Path, request: PortableRenderRequest) -> tuple[Path, dict[str, Any]]:
    envelope = request.data.get("structured_hud")
    if not isinstance(envelope, dict):
        raise RecorderError("portable request has no structured HUD sidecar envelope")
    unresolved = path.expanduser()
    if unresolved.is_symlink() or not unresolved.is_file():
        raise RecorderError(f"structured HUD sidecar is missing or symlinked: {unresolved}")
    resolved = unresolved.resolve()
    digest, size = _stable_file_digest(resolved, "structured HUD sidecar")
    if digest != envelope["sha256"] or size != envelope["size_bytes"]:
        raise RecorderError("structured HUD sidecar size or SHA-256 does not match the portable request")
    return resolved, envelope


def materialize_portable_render_job(
    request: Path | Mapping[str, Any] | PortableRenderRequest,
    replay: Path,
    output: Path,
    *,
    structured_hud: Path | None = None,
    force: bool = False,
) -> RenderJobResult:
    portable = _request_from_value(request)
    resolved_replay = _verify_replay(replay, portable)
    hud_path: Path | None = None
    hud_envelope: dict[str, Any] | None = None
    if portable.data.get("structured_hud") is not None:
        if structured_hud is None:
            raise RecorderError("portable render job requires its structured HUD sidecar")
        hud_path, hud_envelope = _verify_hud_sidecar(structured_hud, portable)
    elif structured_hud is not None:
        raise RecorderError("portable render request does not declare a structured HUD sidecar")
    requested_output = output.expanduser()
    if requested_output.is_symlink():
        raise RecorderError(f"render job output may not be a symlink: {requested_output}")
    resolved_output = requested_output.resolve()
    resolved_output.parent.mkdir(parents=True, exist_ok=True)
    if resolved_output.exists() or resolved_output.is_symlink():
        if not force:
            raise RecorderError(f"render job output exists: {resolved_output}; pass force=True to replace it")
        if resolved_output.is_symlink() or not resolved_output.is_dir() or not _owned_render_directory(resolved_output):
            raise RecorderError(f"refusing to replace non-owned render directory: {resolved_output}")
        shutil.rmtree(resolved_output)

    value = portable.data
    timeline = value["timeline"]
    render = value["render"]
    requested_no_gui = render.get("no_gui", True)
    source = value["source_replay"]
    subject = value["subject"]
    staging = Path(tempfile.mkdtemp(prefix=f".{resolved_output.name}.tmp-", dir=resolved_output.parent))
    try:
        (staging / "frames").mkdir()
        job: dict[str, Any] = {
            "schema_version": 1,
            "owner": OWNER,
            "job_type": RENDER_JOB_TYPE,
            "status": "prepared",
            "created_at": datetime.now(UTC).isoformat(),
            "replay": str(resolved_replay),
            "output": str((resolved_output / "frames").resolve()),
            "result": str((resolved_output / "result.json").resolve()),
            "session_id": value["episode"]["session_id"],
            "connection_id": subject["connection_id"],
            "player_uuid": subject["player_uuid"],
            "global_start_tick": timeline["global_start_tick"],
            "global_end_tick": timeline["global_end_tick"],
            "width": render["width"],
            "height": render["height"],
            "fps": render["fps"],
            # Requests authored before no_gui became portable omit the field and
            # retain the original world-only renderer behavior.
            "no_gui": requested_no_gui,
            "stop_when_done": True,
            "episode": dict(value["episode"]),
            "source_replay": {**source, "path": str(resolved_replay)},
            "subject": dict(subject),
            "timeline": {
                "from_global_server_tick": timeline["global_start_tick"],
                "to_global_server_tick": timeline["global_end_tick"],
                "observed_connection_range": timeline["observed_connection_range"],
                "server_tick_rate_hz": 20,
                "output_fps": render["fps"],
                "range_policy": timeline["range_policy"],
                "newer_cutoff": timeline["newer_cutoff"],
                "alignment": "exact mc_recorder:timeline payload recorded inside Flashback",
            },
            "camera": {
                "perspective": render["camera"],
                "width": render["width"],
                "height": render["height"],
            },
            "output_metadata": {
                "frames_directory": str((resolved_output / "frames").resolve()),
                "frame_index": str((resolved_output / "frames" / "frames.jsonl").resolve()),
                "result_manifest": str((resolved_output / "result.json").resolve()),
            },
            "portable_request": {
                "request_id": portable.request_id,
                "sha256": portable.sha256,
                "request_type": PORTABLE_REQUEST_TYPE,
            },
            "renderer_contract": {
                "minecraft_version": "1.21.8",
                "flashback_version": "0.39.5",
                "implementation": "renderer-mod",
                "job_property": "mc.recorder.renderJob",
                "job_environment": ENV_RENDER_JOB,
                "completion": "result.json is atomically published after frames.jsonl is durable",
            },
            "limitations": [
                "The rendered view is reconstructed from server-visible packets, not original client pixels.",
                "A replay segment may cover only part of a longer player connection.",
            ],
        }
        if "presentation_contract" in render:
            job["presentation_contract"] = render["presentation_contract"]
        if hud_path is not None and hud_envelope is not None:
            local_hud = staging / "hud-states.jsonl"
            shutil.copyfile(hud_path, local_hud, follow_symlinks=False)
            copied_sha, copied_size = _stable_file_digest(local_hud, "materialized structured HUD sidecar")
            if copied_sha != hud_envelope["sha256"] or copied_size != hud_envelope["size_bytes"]:
                raise RecorderError("materialized structured HUD sidecar failed verification")
            job["structured_hud"] = {
                **hud_result_envelope(hud_envelope),
                "path": str((resolved_output / "hud-states.jsonl").resolve()),
            }
        (staging / "render-job.json").write_text(json.dumps(job, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        staging.rename(resolved_output)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        raise
    return RenderJobResult(
        directory=resolved_output,
        manifest=resolved_output / "render-job.json",
        replay=resolved_replay,
        connection_id=subject["connection_id"],
    )


def _read_json_object(path: Path, description: str, maximum: int) -> dict[str, Any]:
    try:
        if path.stat().st_size > maximum:
            raise RecorderError(f"{description} exceeds the size limit: {path}")
        value = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise RecorderError(f"cannot read {description}: {path}") from exc
    except (ValueError, RecursionError) as exc:
        raise RecorderError(f"invalid JSON in {description}: {path}") from exc
    if not isinstance(value, dict):
        raise RecorderError(f"{description} must be an object: {path}")
    return value


def _raw_result_range(result: Mapping[str, Any], request: PortableRenderRequest) -> tuple[str, int | None, int | None]:
    status_value = result.get("status")
    if status_value not in {"complete", "no_coverage"}:
        raise RecorderError("worker result must be complete or no_coverage")
    status_text = str(status_value)
    value = request.data
    source = value["source_replay"]
    subject = value["subject"]
    timeline = value["timeline"]
    render = value["render"]
    requested_no_gui = render.get("no_gui", True)
    if result.get("session_id") != value["episode"]["session_id"]:
        raise RecorderError("worker result session_id does not match its request")
    if result.get("player_uuid") != subject["player_uuid"]:
        raise RecorderError("worker result player_uuid does not match its request")
    if result.get("connection_id") != subject["connection_id"]:
        raise RecorderError("worker result connection_id does not match its request")
    if result.get("replay_sha256") != source["sha256"]:
        raise RecorderError("worker result replay_sha256 does not match its request")
    if result.get("replay_bytes") != source["size_bytes"]:
        raise RecorderError("worker result replay_bytes does not match its request")
    result_no_gui = result.get("no_gui", True)
    if not isinstance(result_no_gui, bool) or result_no_gui != requested_no_gui:
        raise RecorderError("worker result no_gui does not match its request")
    requested_presentation = render.get("presentation_contract")
    result_presentation = result.get("presentation_contract")
    if result_presentation is not None and result_presentation != FULL_CLIENT_PRESENTATION_CONTRACT:
        raise RecorderError("worker result presentation_contract is unsupported")
    if result_presentation is not None and result_no_gui:
        raise RecorderError("worker result presentation_contract requires no_gui=false")
    if requested_presentation is not None and result_presentation != requested_presentation:
        raise RecorderError("worker result presentation_contract does not match its request")
    if requested_presentation is None and result_presentation is not None:
        raise RecorderError("worker result presentation_contract was not requested")
    requested_hud = value.get("structured_hud")
    result_hud = result.get("structured_hud")
    if requested_presentation == FULL_CLIENT_PRESENTATION_CONTRACT:
        assert isinstance(requested_hud, dict)
        actual_hud = validate_hud_result_envelope(result_hud, "worker result structured_hud")
        if actual_hud != hud_result_envelope(requested_hud):
            raise RecorderError("worker result structured_hud does not match its request")
    elif result_hud is not None:
        raise RecorderError("worker result structured_hud was not requested")
    if status_text == "no_coverage":
        if timeline["range_policy"] != "intersection":
            raise RecorderError("no_coverage is valid only for an intersection request")
        return status_text, None, None

    expected_ints = {
        "fps": render["fps"],
        "width": render["width"],
        "height": render["height"],
    }
    for key, expected in expected_ints.items():
        if result.get(key) != expected or isinstance(result.get(key), bool):
            raise RecorderError(f"worker result {key} does not match its request")
    first = _integer(result.get("global_start_tick"), "worker global_start_tick", 0)
    last = _integer(result.get("global_end_tick"), "worker global_end_tick", 0)
    if first > last:
        raise RecorderError("worker result global tick range is reversed")
    requested_first = timeline["global_start_tick"]
    requested_last = timeline["global_end_tick"]
    if timeline["range_policy"] == "exact":
        if (first, last) != (requested_first, requested_last):
            raise RecorderError("worker result does not cover the exact requested range")
    else:
        cutoff = timeline["newer_cutoff"]
        effective_last = requested_last if cutoff is None else min(requested_last, cutoff - 1)
        if first < requested_first or last > effective_last:
            raise RecorderError("worker result is outside the requested segment intersection")
    replay_start = _integer(result.get("replay_start_tick"), "worker replay_start_tick", 0)
    replay_end = _integer(result.get("replay_end_tick"), "worker replay_end_tick", 0)
    offset = _integer(result.get("global_tick_offset"), "worker global_tick_offset", -(2**63))
    if replay_end < replay_start or replay_end - replay_start != last - first:
        raise RecorderError("worker replay/global ranges have different lengths")
    if offset + replay_start != first or offset + replay_end != last:
        raise RecorderError("worker global tick offset is inconsistent")
    return status_text, first, last


def _safe_source_files(job: Path, status_value: str) -> list[tuple[str, Path]]:
    result = job / "result.json"
    if result.is_symlink() or not result.is_file():
        raise RecorderError(f"render job has no completed result.json: {job}")
    files: list[tuple[str, Path]] = [("worker-result.json", result)]
    frames = job / "frames"
    if status_value == "no_coverage":
        if frames.exists() and any(frames.iterdir()):
            raise RecorderError("no_coverage render job must not contain frame payloads")
        return files
    if frames.is_symlink() or not frames.is_dir():
        raise RecorderError(f"render job has no frames directory: {job}")
    for root, directories, names in os.walk(frames, followlinks=False):
        root_path = Path(root)
        for directory in directories:
            candidate = root_path / directory
            if candidate.is_symlink():
                raise RecorderError(f"render payload contains a symlink: {candidate}")
        for name in names:
            candidate = root_path / name
            relative = "frames/" + candidate.relative_to(frames).as_posix()
            if candidate.is_symlink() or not candidate.is_file():
                raise RecorderError(f"render payload is not a regular file: {candidate}")
            if not _allowed_payload_path(relative):
                raise RecorderError(f"render payload contains an unsupported file: {relative}")
            files.append((relative, candidate))
    if not any(relative == "frames/frames.jsonl" for relative, _path in files):
        raise RecorderError("complete render payload lacks frames/frames.jsonl")
    return sorted(files)


def _copy_or_link(source: Path, destination: Path, use_hardlinks: bool) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if use_hardlinks:
        try:
            os.link(source, destination)
            return
        except OSError:
            pass
    shutil.copyfile(source, destination, follow_symlinks=False)


def create_render_bundle(
    render_job: Path | RenderJobResult,
    output: Path,
    request: Path | Mapping[str, Any] | PortableRenderRequest,
    *,
    use_hardlinks: bool = True,
) -> RenderBundle:
    portable = _request_from_value(request)
    unresolved_job = render_job.directory if isinstance(render_job, RenderJobResult) else render_job
    if unresolved_job.is_symlink() or not unresolved_job.is_dir():
        raise RecorderError(f"render job is missing or symlinked: {unresolved_job}")
    job = unresolved_job.resolve()
    raw_result = _read_json_object(job / "result.json", "worker result", MAX_BUNDLE_MANIFEST_BYTES)
    status_value, first_tick, last_tick = _raw_result_range(raw_result, portable)
    files = _safe_source_files(job, status_value)
    requested_output = output.expanduser()
    if requested_output.exists() or requested_output.is_symlink():
        raise RecorderError(f"render bundle output already exists: {requested_output}")
    destination = requested_output.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{destination.name}.tmp-", dir=destination.parent))
    try:
        payload: list[_PayloadFile] = []
        for relative, source_path in files:
            target = staging.joinpath(*PurePosixPath(relative).parts)
            _copy_or_link(source_path, target, use_hardlinks)
            digest, size = _stable_file_digest(target, f"bundle payload {relative}")
            payload.append(_PayloadFile(relative, size, digest))
        payload.sort(key=lambda item: item.path)
        _assert_payload_inventory(staging, payload, portable, status_value)
        index_bytes = b"".join(_json_bytes(item.row()) for item in payload)
        if len(index_bytes) > MAX_PAYLOAD_INDEX_BYTES:
            raise RecorderError("render bundle payload index exceeds the size limit")
        index_path = staging / "payload-files.jsonl"
        index_path.write_bytes(index_bytes)
        bundle_data: dict[str, Any] = {
            "schema_version": 1,
            "bundle_type": RENDER_BUNDLE_TYPE,
            "request_id": portable.request_id,
            "request_sha256": portable.sha256,
            "status": status_value,
            "source_replay": dict(portable.data["source_replay"]),
            "global_start_tick": first_tick,
            "global_end_tick": last_tick,
            "payload_index": "payload-files.jsonl",
            "payload_index_sha256": _sha256_bytes(index_bytes),
            "payload_file_count": len(payload),
            "payload_bytes": sum(item.size_bytes for item in payload),
        }
        manifest_path = staging / "bundle.json"
        manifest_path.write_bytes(_json_bytes(bundle_data))
        staging.rename(destination)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        raise
    return RenderBundle(
        directory=destination,
        manifest=destination / "bundle.json",
        payload_index=destination / "payload-files.jsonl",
        sha256=sha256_file(destination / "bundle.json"),
        request_id=portable.request_id,
    )


def _allowed_payload_path(relative: str) -> bool:
    return (
        relative
        in {
            "worker-result.json",
            "frames/frames.jsonl",
        }
        or _FRAME_RE.fullmatch(relative) is not None
    )


def _safe_relative_path(value: Any, context: str) -> str:  # noqa: ANN401
    if not isinstance(value, str) or not value or len(value.encode("utf-8")) > 4096:
        raise RecorderError(f"{context} must be a bounded relative path")
    if "\\" in value or value.startswith("/"):
        raise RecorderError(f"{context} must be a relative POSIX path")
    path = PurePosixPath(value)
    if any(part in {"", ".", ".."} for part in path.parts) or path.as_posix() != value:
        raise RecorderError(f"{context} contains invalid path components")
    if any(len(part.encode("utf-8")) > 255 for part in path.parts):
        raise RecorderError(f"{context} contains an oversized path component")
    return value


def _safe_bundle_tree(root: Path, expected_files: set[str] | None = None) -> set[str]:
    actual: set[str] = set()
    actual_directories: set[str] = set()
    for current, directories, names in os.walk(root, followlinks=False):
        current_path = Path(current)
        for directory in directories:
            candidate = current_path / directory
            metadata = candidate.lstat()
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
                raise RecorderError(f"render bundle contains a non-directory or symlink: {candidate}")
            actual_directories.add(candidate.relative_to(root).as_posix())
        for name in names:
            candidate = current_path / name
            metadata = candidate.lstat()
            relative = candidate.relative_to(root).as_posix()
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
                raise RecorderError(f"render bundle contains a non-regular file or symlink: {relative}")
            if metadata.st_nlink != 1:
                raise RecorderError(f"render bundle contains a hard-linked file: {relative}")
            actual.add(relative)
    if expected_files is not None:
        expected_directories = {PurePosixPath(relative).parent.as_posix() for relative in expected_files if PurePosixPath(relative).parent.as_posix() != "."}
        expected_directories |= {parent.as_posix() for relative in expected_files for parent in PurePosixPath(relative).parents if parent.as_posix() not in {".", ""}}
        if actual_directories != expected_directories:
            raise RecorderError("render bundle contains unexpected or missing directories")
    return actual


def _load_bundle(
    bundle: Path,
    request: PortableRenderRequest,
    *,
    max_files: int,
    max_total_bytes: int,
) -> tuple[Path, dict[str, Any], list[_PayloadFile], str]:
    unresolved = bundle.expanduser()
    if unresolved.is_symlink() or not unresolved.is_dir():
        raise RecorderError(f"render bundle is missing or symlinked: {unresolved}")
    root = unresolved.resolve()
    manifest_path = root / "bundle.json"
    index_path = root / "payload-files.jsonl"
    if manifest_path.is_symlink() or index_path.is_symlink():
        raise RecorderError("render bundle metadata may not be symlinked")
    manifest = _read_json_object(manifest_path, "render bundle manifest", MAX_BUNDLE_MANIFEST_BYTES)
    _required_keys(
        manifest,
        {
            "schema_version",
            "bundle_type",
            "request_id",
            "request_sha256",
            "status",
            "source_replay",
            "global_start_tick",
            "global_end_tick",
            "payload_index",
            "payload_index_sha256",
            "payload_file_count",
            "payload_bytes",
        },
        set(),
        "render bundle manifest",
    )
    if manifest.get("schema_version") != 1 or manifest.get("bundle_type") != RENDER_BUNDLE_TYPE:
        raise RecorderError("unsupported render bundle manifest")
    if manifest.get("request_id") != request.request_id:
        raise RecorderError("render bundle request_id does not match the authoritative request")
    if manifest.get("request_sha256") != request.sha256:
        raise RecorderError("render bundle request SHA-256 does not match the authoritative request")
    if manifest.get("source_replay") != request.data["source_replay"]:
        raise RecorderError("render bundle replay identity does not match the authoritative request")
    if manifest.get("status") not in {"complete", "no_coverage"}:
        raise RecorderError("render bundle status is invalid")
    if manifest.get("payload_index") != "payload-files.jsonl":
        raise RecorderError("render bundle payload index name is not canonical")
    if not isinstance(manifest.get("payload_index_sha256"), str) or _SHA256_RE.fullmatch(manifest["payload_index_sha256"]) is None:
        raise RecorderError("render bundle payload index SHA-256 is invalid")
    count = _integer(manifest.get("payload_file_count"), "bundle payload_file_count", 1, max_files)
    total = _integer(manifest.get("payload_bytes"), "bundle payload_bytes", 1, max_total_bytes)
    try:
        if index_path.stat().st_size > MAX_PAYLOAD_INDEX_BYTES:
            raise RecorderError("render bundle payload index exceeds the size limit")
        index_bytes = index_path.read_bytes()
    except OSError as exc:
        raise RecorderError(f"cannot read render bundle payload index: {index_path}") from exc
    if _sha256_bytes(index_bytes) != manifest["payload_index_sha256"]:
        raise RecorderError("render bundle payload index SHA-256 does not match its manifest")
    payload: list[_PayloadFile] = []
    seen: set[str] = set()
    seen_casefold: set[str] = set()
    for line_number, line in enumerate(index_bytes.splitlines(), 1):
        if not line.strip():
            raise RecorderError(f"payload index line {line_number} is empty")
        if len(line) > MAX_PAYLOAD_INDEX_LINE_BYTES:
            raise RecorderError(f"payload index line {line_number} exceeds the size limit")
        try:
            row = json.loads(line)
        except (ValueError, RecursionError) as exc:
            raise RecorderError(f"payload index line {line_number} is invalid JSON") from exc
        row = _object(row, f"payload index line {line_number}")
        _required_keys(row, {"path", "sha256", "size_bytes"}, set(), f"payload index line {line_number}")
        relative = _safe_relative_path(row.get("path"), f"payload index line {line_number} path")
        if not _allowed_payload_path(relative):
            raise RecorderError(f"payload index contains an unsupported file: {relative}")
        folded = relative.casefold()
        if relative in seen or folded in seen_casefold:
            raise RecorderError(f"payload index contains a duplicate or case collision: {relative}")
        seen.add(relative)
        seen_casefold.add(folded)
        digest = row.get("sha256")
        if not isinstance(digest, str) or _SHA256_RE.fullmatch(digest) is None:
            raise RecorderError(f"payload index has an invalid SHA-256 for {relative}")
        size = _integer(row.get("size_bytes"), f"payload size for {relative}", 1, max_total_bytes)
        payload.append(_PayloadFile(relative, size, digest))
    if [item.path for item in payload] != sorted(item.path for item in payload):
        raise RecorderError("render bundle payload index must be sorted by path")
    if len(payload) != count or sum(item.size_bytes for item in payload) != total:
        raise RecorderError("render bundle payload count or byte total does not match its manifest")
    if "worker-result.json" not in seen:
        raise RecorderError("render bundle lacks worker-result.json")
    if manifest["status"] == "complete" and "frames/frames.jsonl" not in seen:
        raise RecorderError("complete render bundle lacks frames/frames.jsonl")
    if manifest["status"] == "no_coverage" and seen != {"worker-result.json"}:
        raise RecorderError("no_coverage bundle may contain only worker-result.json")
    expected = seen | {"bundle.json", "payload-files.jsonl"}
    actual = _safe_bundle_tree(root, expected)
    if actual != expected:
        extras = actual - expected
        missing = expected - actual
        detail = ", ".join(sorted(extras or missing)[:5])
        raise RecorderError(f"render bundle has unexpected or missing files: {detail}")
    for item in payload:
        path = root.joinpath(*PurePosixPath(item.path).parts)
        digest, size = _stable_file_digest(path, f"bundle payload {item.path}")
        if digest != item.sha256 or size != item.size_bytes:
            raise RecorderError(f"render bundle payload integrity mismatch: {item.path}")
    return root, manifest, payload, sha256_file(manifest_path)


def _copy_verified(source: Path, destination: Path, expected: _PayloadFile) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        source_descriptor = os.open(source, flags)
    except OSError as exc:
        raise RecorderError(f"cannot open bundle payload without following links: {expected.path}") from exc
    digest = hashlib.sha256()
    copied = 0
    try:
        before = os.fstat(source_descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise RecorderError(f"bundle payload is not a single-linked regular file: {expected.path}")
        try:
            target_descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except OSError as exc:
            raise RecorderError(f"cannot create imported payload: {expected.path}") from exc
        try:
            with os.fdopen(os.dup(source_descriptor), "rb") as source_handle, os.fdopen(target_descriptor, "wb") as target_handle:
                while True:
                    block = source_handle.read(1024 * 1024)
                    if not block:
                        break
                    digest.update(block)
                    copied += len(block)
                    target_handle.write(block)
                target_handle.flush()
                os.fsync(target_handle.fileno())
        finally:
            # target_descriptor is owned by fdopen after it succeeds.
            pass
        after = os.fstat(source_descriptor)
    finally:
        os.close(source_descriptor)
    identity_before = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
    identity_after = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    if identity_before != identity_after:
        raise RecorderError(f"bundle payload changed while copying: {expected.path}")
    if copied != expected.size_bytes or digest.hexdigest() != expected.sha256:
        raise RecorderError(f"bundle payload failed verification while copying: {expected.path}")


def _declared_payload_files(staging: Path, request: PortableRenderRequest, status_value: str) -> set[str]:
    expected = {"worker-result.json"}
    if status_value == "no_coverage":
        return expected
    expected.add("frames/frames.jsonl")
    frame_index = staging / "frames" / "frames.jsonl"
    try:
        if frame_index.stat().st_size > MAX_PAYLOAD_INDEX_BYTES:
            raise RecorderError("frame index exceeds the transfer size limit")
        lines = frame_index.read_bytes().splitlines()
    except OSError as exc:
        raise RecorderError("cannot read transferred frame index") from exc
    for line_number, line in enumerate(lines, 1):
        if not line or len(line) > MAX_PAYLOAD_INDEX_LINE_BYTES:
            raise RecorderError(f"frame index line {line_number} is empty or oversized")
        try:
            row = json.loads(line)
        except (ValueError, RecursionError) as exc:
            raise RecorderError(f"frame index line {line_number} is invalid JSON") from exc
        row = _object(row, f"frame index line {line_number}")
        relative = _safe_relative_path(row.get("path"), f"frame index line {line_number} path")
        payload_path = f"frames/{relative}"
        if _FRAME_RE.fullmatch(payload_path) is None:
            raise RecorderError(f"frame index line {line_number} names a non-canonical artifact")
        if payload_path in expected:
            raise RecorderError(f"frame index contains duplicate artifact {payload_path}")
        expected.add(payload_path)

    return expected


def _assert_payload_inventory(staging: Path, payload: list[_PayloadFile], request: PortableRenderRequest, status_value: str) -> None:
    declared = _declared_payload_files(staging, request, status_value)
    actual = {item.path for item in payload}
    if actual != declared:
        detail = ", ".join(sorted((actual - declared) or (declared - actual))[:5])
        raise RecorderError(f"render payload contains unreferenced or missing artifacts: {detail}")


def _canonical_result(
    request: PortableRenderRequest,
    raw: Mapping[str, Any],
    status_value: str,
    first_tick: int | None,
    last_tick: int | None,
) -> dict[str, Any]:
    value = request.data
    timeline = value["timeline"]
    result: dict[str, Any] = {
        "schema_version": 2,
        "result_type": CANONICAL_RENDER_RESULT_TYPE,
        "status": status_value,
        "session_id": value["episode"]["session_id"],
        "player_uuid": value["subject"]["player_uuid"],
        "connection_id": value["subject"]["connection_id"],
        "portable_request": {
            "request_id": request.request_id,
            "sha256": request.sha256,
            "request_type": PORTABLE_REQUEST_TYPE,
        },
        "source_replay": dict(value["source_replay"]),
        "range_policy": timeline["range_policy"],
        "newer_cutoff": timeline["newer_cutoff"],
        "requested_global_start_tick": timeline["global_start_tick"],
        "requested_global_end_tick": timeline["global_end_tick"],
        "no_gui": value["render"].get("no_gui", True),
        "global_start_tick": first_tick,
        "global_end_tick": last_tick,
        "artifact_root": ".",
        "worker_result": "worker-result.json",
    }
    if "presentation_contract" in raw:
        result["presentation_contract"] = raw["presentation_contract"]
    if "structured_hud" in raw:
        result["structured_hud"] = raw["structured_hud"]
    if status_value == "no_coverage":
        reason = raw.get("reason")
        result["reason"] = reason if isinstance(reason, str) and reason else "segment_has_no_coverage"
        return result
    for key in (
        "replay_start_tick",
        "replay_end_tick",
        "global_tick_offset",
        "fps",
        "width",
        "height",
    ):
        result[key] = raw[key]
    result["output"] = "frames"
    result["frames_index"] = "frames/frames.jsonl"
    return result


def _write_import_manifest(
    staging: Path,
    request: PortableRenderRequest,
    bundle_sha: str,
    status_value: str,
) -> Path:
    files: dict[str, dict[str, Any]] = {}
    for current, _directories, names in os.walk(staging):
        for name in names:
            path = Path(current) / name
            relative = path.relative_to(staging).as_posix()
            if relative == "import-manifest.json":
                continue
            files[relative] = {
                "sha256": sha256_file(path),
                "size_bytes": path.stat().st_size,
            }
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "owner": OWNER,
        "import_type": RENDER_IMPORT_TYPE,
        "request_id": request.request_id,
        "request_sha256": request.sha256,
        "bundle_sha256": bundle_sha,
        "status": status_value,
        "source_replay": dict(request.data["source_replay"]),
        "files": dict(sorted(files.items())),
    }
    path = staging / "import-manifest.json"
    path.write_bytes(_json_bytes(manifest))
    with path.open("rb") as handle:
        os.fsync(handle.fileno())
    return path


def _validate_imported(
    directory: Path,
    request: PortableRenderRequest,
    bundle_sha: str,
) -> ImportedRenderResult:
    if directory.is_symlink() or not directory.is_dir():
        raise RecorderError(f"render import destination is not a regular directory: {directory}")
    root = directory.resolve()
    manifest_path = root / "import-manifest.json"
    result_path = root / "result.json"
    manifest = _read_json_object(manifest_path, "render import manifest", MAX_BUNDLE_MANIFEST_BYTES)
    if (
        manifest.get("schema_version") != 1
        or manifest.get("owner") != OWNER
        or manifest.get("import_type") != RENDER_IMPORT_TYPE
        or manifest.get("request_id") != request.request_id
        or manifest.get("request_sha256") != request.sha256
        or manifest.get("bundle_sha256") != bundle_sha
        or manifest.get("source_replay") != request.data["source_replay"]
    ):
        raise RecorderError(f"existing render import conflicts with this request: {root}")
    files = manifest.get("files")
    if not isinstance(files, dict) or not files:
        raise RecorderError(f"existing render import has an invalid file inventory: {root}")
    expected = set(files) | {"import-manifest.json"}
    actual = _safe_bundle_tree(root, expected)  # ty:ignore[invalid-argument-type]
    if actual != expected:
        raise RecorderError(f"existing render import contains unexpected or missing files: {root}")
    for relative, metadata in files.items():
        if not isinstance(metadata, dict):
            raise RecorderError(f"existing render import has invalid metadata for {relative}")
        safe_relative = _safe_relative_path(relative, "render import file")
        path = root.joinpath(*PurePosixPath(safe_relative).parts)
        digest, size = _stable_file_digest(path, f"imported render file {relative}")
        if digest != metadata.get("sha256") or size != metadata.get("size_bytes"):
            raise RecorderError(f"existing render import failed integrity verification: {relative}")
    result = _read_json_object(result_path, "canonical renderer result", MAX_BUNDLE_MANIFEST_BYTES)
    raw = _read_json_object(root / "worker-result.json", "worker result", MAX_BUNDLE_MANIFEST_BYTES)
    status_value, first_tick, last_tick = _raw_result_range(raw, request)
    if manifest.get("status") != status_value:
        raise RecorderError(f"existing render import status is inconsistent: {root}")
    if result != _canonical_result(request, raw, status_value, first_tick, last_tick):
        raise RecorderError(f"existing canonical renderer result is not derived from its request: {root}")
    payload: list[_PayloadFile] = []
    for relative, metadata in files.items():
        if relative == "result.json":
            continue
        assert isinstance(metadata, dict)
        payload.append(_PayloadFile(relative, int(metadata["size_bytes"]), str(metadata["sha256"])))
    _assert_payload_inventory(root, payload, request, status_value)
    if status_value == "complete":
        _load_frame_attachments([root], request.data["episode"]["session_id"])
    return ImportedRenderResult(
        directory=root,
        result=result_path,
        manifest=manifest_path,
        request_id=request.request_id,
        status=status_value,
        reused=True,
    )


def import_render_bundle(
    request: Path | Mapping[str, Any] | PortableRenderRequest,
    bundle: Path,
    authoritative_replay: Path,
    destination: Path,
    *,
    max_files: int = DEFAULT_MAX_FILES,
    max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES,
) -> ImportedRenderResult:
    if max_files < 1 or max_files > DEFAULT_MAX_FILES:
        raise RecorderError(f"max_files must be in 1..{DEFAULT_MAX_FILES}")
    if max_total_bytes < 1 or max_total_bytes > DEFAULT_MAX_TOTAL_BYTES:
        raise RecorderError(f"max_total_bytes must be in 1..{DEFAULT_MAX_TOTAL_BYTES}")
    portable = _request_from_value(request)
    replay = _verify_replay(authoritative_replay, portable)
    bundle_root, bundle_manifest, payload, bundle_sha = _load_bundle(
        bundle,
        portable,
        max_files=max_files,
        max_total_bytes=max_total_bytes,
    )
    requested_destination = destination.expanduser()
    if requested_destination.is_symlink():
        raise RecorderError(f"render import destination may not be a symlink: {requested_destination}")
    resolved_destination = requested_destination.resolve()
    resolved_destination.parent.mkdir(parents=True, exist_ok=True)
    if resolved_destination.exists():
        return _validate_imported(resolved_destination, portable, bundle_sha)
    required = bundle_manifest["payload_bytes"] + 64 * 1024 * 1024
    if shutil.disk_usage(resolved_destination.parent).free < required:
        raise RecorderError("insufficient free space to import the render bundle")

    staging = Path(tempfile.mkdtemp(prefix=f".{resolved_destination.name}.tmp-", dir=resolved_destination.parent))
    try:
        for item in payload:
            source = bundle_root.joinpath(*PurePosixPath(item.path).parts)
            target = staging.joinpath(*PurePosixPath(item.path).parts)
            _copy_verified(source, target, item)
        raw_path = staging / "worker-result.json"
        raw_result = _read_json_object(raw_path, "worker result", MAX_BUNDLE_MANIFEST_BYTES)
        status_value, first_tick, last_tick = _raw_result_range(raw_result, portable)
        if status_value != bundle_manifest["status"]:
            raise RecorderError("worker result status does not match its bundle manifest")
        if first_tick != bundle_manifest["global_start_tick"] or last_tick != bundle_manifest["global_end_tick"]:
            raise RecorderError("worker result coverage does not match its bundle manifest")
        _assert_payload_inventory(staging, payload, portable, status_value)
        canonical = _canonical_result(portable, raw_result, status_value, first_tick, last_tick)
        result_path = staging / "result.json"
        result_path.write_bytes(_json_bytes(canonical))
        if status_value == "complete":
            _load_frame_attachments([staging], portable.data["episode"]["session_id"])
        if _stable_file_digest(replay, "authoritative replay") != (
            portable.data["source_replay"]["sha256"],
            portable.data["source_replay"]["size_bytes"],
        ):
            raise RecorderError("authoritative replay changed during render import")
        _write_import_manifest(staging, portable, bundle_sha, status_value)
        try:
            staging.rename(resolved_destination)
        except FileExistsError:
            return _validate_imported(resolved_destination, portable, bundle_sha)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        raise
    return ImportedRenderResult(
        directory=resolved_destination,
        result=resolved_destination / "result.json",
        manifest=resolved_destination / "import-manifest.json",
        request_id=portable.request_id,
        status=status_value,
        reused=False,
    )
