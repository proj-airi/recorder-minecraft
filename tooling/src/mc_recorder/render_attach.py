from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import RecorderConfig
from .dataset_viewer import DatasetMetadata, DatasetViewer, DatasetViewerError, opaque_dataset_id
from .episodes import resolve_episode
from .errors import RecorderError
from .exporter import CANONICAL_RENDER_RESULT_TYPE, export_episode
from .operations import operation_lock
from .render_contract import FULL_CLIENT_PRESENTATION_CONTRACT
from .render_hud import validate_hud_result_envelope
from .render_transfer import ImportedRenderResult, PORTABLE_REQUEST_TYPE, RENDER_IMPORT_TYPE


_DATASET_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_RECORDING_ID_RE = re.compile(r"^[0-9a-f]{24}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SEGMENT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,159}$")
_MAX_JSON_BYTES = 4 * 1024 * 1024


@dataclass(frozen=True)
class RenderAttachmentResult:
    output: Path
    dataset_id: str
    sample_count: int
    state_count: int
    action_count: int
    rgb_sample_count: int
    scene_sample_count: int
    requested_start_tick: int
    requested_end_tick: int
    rendered_tick_count: int
    coverage_start_tick: int | None
    coverage_end_tick: int | None
    complete_import_count: int
    no_coverage_import_count: int
    partial: bool

    def as_json(self) -> dict[str, Any]:
        return {
            "output": str(self.output),
            "dataset_id": self.dataset_id,
            "sample_count": self.sample_count,
            "state_count": self.state_count,
            "action_count": self.action_count,
            "rgb_sample_count": self.rgb_sample_count,
            "scene_sample_count": self.scene_sample_count,
            "requested_start_tick": self.requested_start_tick,
            "requested_end_tick": self.requested_end_tick,
            "rendered_tick_count": self.rendered_tick_count,
            "coverage_start_tick": self.coverage_start_tick,
            "coverage_end_tick": self.coverage_end_tick,
            "complete_import_count": self.complete_import_count,
            "no_coverage_import_count": self.no_coverage_import_count,
            "partial": self.partial,
        }


@dataclass(frozen=True)
class _JobIdentity:
    job_id: str
    session_id: str
    player_uuid: str
    connection_id: str
    dataset_id: str
    start_tick: int
    end_tick: int
    selection_start_tick: int
    selection_end_tick: int
    output: Path


@dataclass(frozen=True)
class _CompleteImport:
    directory: Path
    segment_id: str
    request_id: str
    start_tick: int
    end_tick: int


@dataclass(frozen=True)
class _VerifiedDataset:
    metadata: DatasetMetadata
    manifest_sha256: str
    samples_sha256: str


def _canonical_uuid(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise RecorderError(f"render job {label} must be a canonical UUID")
    try:
        canonical = str(uuid.UUID(value))
    except (ValueError, TypeError, AttributeError) as exc:
        raise RecorderError(f"render job {label} must be a canonical UUID") from exc
    if value != canonical:
        raise RecorderError(f"render job {label} must be a canonical UUID")
    return canonical


def _required_tick(value: object, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise RecorderError(f"render job {label} must be a non-negative integer")
    return value


def _read_json(path: Path, label: str) -> tuple[dict[str, Any], bytes]:
    if path.is_symlink() or not path.is_file():
        raise RecorderError(f"{label} is missing or symlinked: {path}")
    try:
        size = path.stat().st_size
        if size > _MAX_JSON_BYTES:
            raise RecorderError(f"{label} exceeds the JSON size limit: {path}")
        encoded = path.read_bytes()
        value = json.loads(encoded)
    except OSError as exc:
        raise RecorderError(f"cannot read {label}: {path}") from exc
    except (ValueError, RecursionError) as exc:
        raise RecorderError(f"{label} is invalid JSON: {path}") from exc
    if not isinstance(value, dict):
        raise RecorderError(f"{label} must be a JSON object: {path}")
    try:
        stable = path.stat().st_size == size and path.read_bytes() == encoded
    except OSError as exc:
        raise RecorderError(f"{label} changed while it was being verified: {path}") from exc
    if not stable:
        raise RecorderError(f"{label} changed while it was being verified: {path}")
    return value, encoded


def _job_identity(config: RecorderConfig, job: Mapping[str, Any]) -> _JobIdentity:
    if not isinstance(job, Mapping):
        raise RecorderError("render queue job must be an object")
    job_id = _canonical_uuid(job.get("id"), "ID")
    if job.get("state") not in {"verifying", "attaching"}:
        raise RecorderError("render queue job must be in the verifying or attaching state")
    payload = job.get("payload")
    if not isinstance(payload, Mapping):
        raise RecorderError("render queue job payload must be an object")
    recording_id = payload.get("recording_id")
    if not isinstance(recording_id, str) or _RECORDING_ID_RE.fullmatch(recording_id) is None:
        raise RecorderError("render queue job has an invalid recording ID")
    if job.get("recording_id") != recording_id:
        raise RecorderError("render queue job recording identity is inconsistent")
    session_id = payload.get("session_id")
    if (
        not isinstance(session_id, str)
        or not session_id
        or session_id.startswith(".")
        or "/" in session_id
        or "\\" in session_id
    ):
        raise RecorderError("render queue job has an invalid session ID")
    player_uuid = _canonical_uuid(payload.get("player_uuid"), "player UUID")
    connection_id = _canonical_uuid(payload.get("connection_id"), "connection UUID")
    dataset_id = payload.get("dataset_id")
    if not isinstance(dataset_id, str) or _DATASET_ID_RE.fullmatch(dataset_id) is None:
        raise RecorderError("render queue job has an invalid opaque dataset ID")
    start_tick = _required_tick(payload.get("start_tick"), "start tick")
    end_tick = _required_tick(payload.get("end_tick"), "end tick")
    if start_tick > end_tick:
        raise RecorderError("render queue job start tick exceeds its end tick")
    if ("selection_start_tick" in payload) != ("selection_end_tick" in payload):
        raise RecorderError("render queue job selection tick bounds must be supplied together")
    selection_start_tick = _required_tick(
        payload.get("selection_start_tick", start_tick), "selection start tick"
    )
    selection_end_tick = _required_tick(
        payload.get("selection_end_tick", end_tick), "selection end tick"
    )
    if not selection_start_tick <= start_tick <= end_tick <= selection_end_tick:
        raise RecorderError("render queue job range is outside its dataset selection")
    output = (
        config.paths.exports
        / f"{session_id}-{player_uuid}-{connection_id}.dataset"
    )
    expected_id = opaque_dataset_id(config.paths.exports, output.name)
    if dataset_id != expected_id:
        raise RecorderError("render queue job opaque dataset ID does not match its selection")
    return _JobIdentity(
        job_id=job_id,
        session_id=session_id,
        player_uuid=player_uuid,
        connection_id=connection_id,
        dataset_id=dataset_id,
        start_tick=start_tick,
        end_tick=end_tick,
        selection_start_tick=selection_start_tick,
        selection_end_tick=selection_end_tick,
        output=output,
    )


def _verify_dataset(
    config: RecorderConfig, identity: _JobIdentity
) -> _VerifiedDataset:
    output = identity.output
    if output.is_symlink() or not output.is_dir():
        raise RecorderError(f"refusing to attach RGB: deterministic dataset is missing: {output}")
    try:
        metadata = DatasetViewer(
            config.paths.exports, config.paths.runtime
        ).get_dataset_metadata(identity.dataset_id)
    except DatasetViewerError as exc:
        raise RecorderError(
            f"refusing to overwrite missing or tampered dataset {output}: {exc}"
        ) from exc
    manifest, encoded = _read_json(output / "manifest.json", "dataset manifest")
    selection = manifest.get("selection")
    if (
        metadata.session_id != identity.session_id
        or manifest.get("session_id") != identity.session_id
        or not isinstance(selection, dict)
        or selection.get("players") != [identity.player_uuid]
        or selection.get("connections") != [identity.connection_id]
        or selection.get("from_tick") != identity.selection_start_tick
        or selection.get("to_tick") != identity.selection_end_tick
    ):
        raise RecorderError(
            "refusing to overwrite dataset with a conflicting session or connection selection"
        )
    if metadata.selected_players != (identity.player_uuid,):
        raise RecorderError("refusing to overwrite dataset with a conflicting player selection")
    files = manifest.get("files")
    samples = files.get("samples.jsonl") if isinstance(files, dict) else None
    samples_sha256 = samples.get("sha256") if isinstance(samples, dict) else None
    if not isinstance(samples_sha256, str) or _SHA256_RE.fullmatch(samples_sha256) is None:
        raise RecorderError("refusing to overwrite dataset with invalid samples provenance")
    return _VerifiedDataset(
        metadata=metadata,
        manifest_sha256=hashlib.sha256(encoded).hexdigest(),
        samples_sha256=samples_sha256,
    )


def _entry_fields(
    value: ImportedRenderResult | Mapping[str, Any],
) -> tuple[Path, str, Path | None, Path | None, str | None]:
    if isinstance(value, ImportedRenderResult):
        return value.directory, value.status, value.result, value.manifest, None
    if not isinstance(value, Mapping):
        raise RecorderError("imported render entry must be an object")
    raw_directory = value.get("directory")
    status = value.get("status")
    if not isinstance(raw_directory, (str, Path)) or not isinstance(status, str):
        raise RecorderError("imported render entry lacks directory or status")
    result = value.get("result")
    manifest = value.get("manifest")
    segment_id = value.get("segment_id")
    if result is not None and not isinstance(result, (str, Path)):
        raise RecorderError("imported render result path is invalid")
    if manifest is not None and not isinstance(manifest, (str, Path)):
        raise RecorderError("imported render manifest path is invalid")
    if segment_id is not None and not isinstance(segment_id, str):
        raise RecorderError("imported render segment ID is invalid")
    return (
        Path(raw_directory),
        status,
        Path(result) if result is not None else None,
        Path(manifest) if manifest is not None else None,
        segment_id,
    )


def _validate_import(
    config: RecorderConfig,
    identity: _JobIdentity,
    dataset: _VerifiedDataset,
    value: ImportedRenderResult | Mapping[str, Any],
) -> _CompleteImport | None:
    unresolved, status, supplied_result, supplied_manifest, supplied_segment = _entry_fields(value)
    render_jobs_root = config.paths.exports / "render-jobs"
    job_root = render_jobs_root / identity.job_id
    for path, label in ((render_jobs_root, "render import root"), (job_root, "render job root")):
        if path.is_symlink():
            raise RecorderError(f"{label} may not be a symlink: {path}")
    if unresolved.is_symlink() or not unresolved.is_dir():
        raise RecorderError(f"render import is missing or symlinked: {unresolved}")
    directory = unresolved.resolve()
    if directory.parent != job_root.resolve():
        raise RecorderError("render import is outside its fixed server-owned job directory")
    if _SEGMENT_ID_RE.fullmatch(directory.name) is None:
        raise RecorderError("render import directory has an invalid segment ID")
    result_path = directory / "result.json"
    manifest_path = directory / "import-manifest.json"
    if supplied_result is not None and supplied_result.resolve() != result_path:
        raise RecorderError("imported render supplied a non-canonical result path")
    if supplied_manifest is not None and supplied_manifest.resolve() != manifest_path:
        raise RecorderError("imported render supplied a non-canonical manifest path")
    result, result_bytes = _read_json(result_path, "canonical render result")
    manifest, _manifest_bytes = _read_json(manifest_path, "render import manifest")
    if status not in {"complete", "no_coverage"} or result.get("status") != status:
        raise RecorderError("render import status does not match its canonical result")
    if (
        result.get("schema_version") != 2
        or result.get("result_type") != CANONICAL_RENDER_RESULT_TYPE
        or result.get("artifact_root") != "."
    ):
        raise RecorderError("render import does not contain a canonical renderer result")
    if (
        result.get("session_id") != identity.session_id
        or result.get("player_uuid") != identity.player_uuid
        or result.get("connection_id") != identity.connection_id
        or result.get("requested_global_start_tick") != identity.start_tick
        or result.get("requested_global_end_tick") != identity.end_tick
    ):
        raise RecorderError("render import identity or requested range does not match its queue job")
    presentation = result.get("presentation_contract")
    if presentation is not None and presentation != FULL_CLIENT_PRESENTATION_CONTRACT:
        raise RecorderError("render import presentation contract is unsupported")
    if presentation == FULL_CLIENT_PRESENTATION_CONTRACT:
        if result.get("no_gui") is not False:
            raise RecorderError("render import full-client presentation requires GUI output")
        structured_hud = validate_hud_result_envelope(
            result.get("structured_hud"), "render import structured_hud"
        )
        if (
            structured_hud["start_server_tick"] != identity.start_tick
            or structured_hud["end_server_tick"] != identity.end_tick
        ):
            raise RecorderError(
                "render import structured_hud range does not match its queue job"
            )
        if (
            structured_hud["dataset_id"] != identity.dataset_id
            or structured_hud["dataset_manifest_sha256"]
            != dataset.manifest_sha256
            or structured_hud["samples_sha256"] != dataset.samples_sha256
            or structured_hud["session_id"] != identity.session_id
            or structured_hud["player_uuid"] != identity.player_uuid
            or structured_hud["connection_id"] != identity.connection_id
        ):
            raise RecorderError(
                "render import structured_hud does not match the currently verified dataset"
            )
    elif result.get("structured_hud") is not None:
        raise RecorderError(
            "render import structured_hud lacks the current presentation contract"
        )
    portable = result.get("portable_request")
    source_replay = result.get("source_replay")
    if not isinstance(portable, dict) or not isinstance(source_replay, dict):
        raise RecorderError("canonical render result lacks portable provenance")
    request_id = _canonical_uuid(portable.get("request_id"), "request ID")
    request_sha = portable.get("sha256")
    segment_id = source_replay.get("segment_id")
    segment_ordinal = source_replay.get("segment_ordinal")
    replay_sha = source_replay.get("sha256")
    replay_bytes = source_replay.get("size_bytes")
    if (
        portable.get("request_type") != PORTABLE_REQUEST_TYPE
        or not isinstance(request_sha, str)
        or _SHA256_RE.fullmatch(request_sha) is None
    ):
        raise RecorderError("canonical render result has an invalid request SHA-256")
    if not isinstance(segment_id, str) or _SEGMENT_ID_RE.fullmatch(segment_id) is None:
        raise RecorderError("canonical render result has an invalid segment ID")
    if (
        not isinstance(segment_ordinal, int)
        or isinstance(segment_ordinal, bool)
        or not 0 <= segment_ordinal <= 2**31 - 1
        or source_replay.get("format") != "flashback"
        or not isinstance(replay_sha, str)
        or _SHA256_RE.fullmatch(replay_sha) is None
        or not isinstance(replay_bytes, int)
        or isinstance(replay_bytes, bool)
        or not 0 < replay_bytes <= 2**63 - 1
    ):
        raise RecorderError("canonical render result has invalid replay segment provenance")
    if segment_id != directory.name or (
        supplied_segment is not None and supplied_segment != segment_id
    ):
        raise RecorderError("render import segment identity does not match its directory")
    files = manifest.get("files")
    result_file = files.get("result.json") if isinstance(files, dict) else None
    if (
        manifest.get("schema_version") != 1
        or manifest.get("owner") != "mc-recorder"
        or manifest.get("import_type") != RENDER_IMPORT_TYPE
        or manifest.get("status") != status
        or manifest.get("request_id") != request_id
        or manifest.get("request_sha256") != request_sha
        or manifest.get("source_replay") != source_replay
        or not isinstance(result_file, dict)
        or result_file.get("size_bytes") != len(result_bytes)
        or result_file.get("sha256") != hashlib.sha256(result_bytes).hexdigest()
    ):
        raise RecorderError("render import manifest does not bind its canonical result")
    if status == "no_coverage":
        if result.get("global_start_tick") is not None or result.get("global_end_tick") is not None:
            raise RecorderError("no-coverage render import declares an artifact range")
        if (directory / "frames" / "frames.jsonl").exists():
            raise RecorderError("no-coverage render import unexpectedly contains a frame index")
        return None

    first_tick = _required_tick(result.get("global_start_tick"), "coverage start tick")
    last_tick = _required_tick(result.get("global_end_tick"), "coverage end tick")
    if (
        first_tick > last_tick
        or first_tick < identity.start_tick
        or last_tick > identity.end_tick
    ):
        raise RecorderError("render import coverage is outside its queue job range")
    if result.get("output") != "frames" or result.get("frames_index") != "frames/frames.jsonl":
        raise RecorderError("canonical render result has non-canonical frame references")
    frames_index = directory / "frames" / "frames.jsonl"
    if frames_index.is_symlink() or not frames_index.is_file():
        raise RecorderError("complete render import has no regular frame index")
    return _CompleteImport(
        directory=directory,
        segment_id=segment_id,
        request_id=request_id,
        start_tick=first_tick,
        end_tick=last_tick,
    )


def _coverage(imports: list[_CompleteImport]) -> tuple[int, int | None, int | None]:
    ordered = sorted(imports, key=lambda item: (item.start_tick, item.end_tick))
    total = 0
    previous_end: int | None = None
    for item in ordered:
        if previous_end is not None and item.start_tick <= previous_end:
            raise RecorderError("complete render imports have overlapping global tick coverage")
        total += item.end_tick - item.start_tick + 1
        previous_end = item.end_tick
    return (
        total,
        ordered[0].start_tick if ordered else None,
        ordered[-1].end_tick if ordered else None,
    )


def _result(
    identity: _JobIdentity,
    metadata: DatasetMetadata,
    complete: list[_CompleteImport],
    no_coverage_count: int,
) -> RenderAttachmentResult:
    rendered, first, last = _coverage(complete)
    return RenderAttachmentResult(
        output=identity.output,
        dataset_id=identity.dataset_id,
        sample_count=metadata.sample_count,
        state_count=metadata.state_count,
        action_count=metadata.action_count,
        rgb_sample_count=metadata.rgb_samples,
        scene_sample_count=metadata.scene_samples,
        requested_start_tick=identity.start_tick,
        requested_end_tick=identity.end_tick,
        rendered_tick_count=rendered,
        coverage_start_tick=first,
        coverage_end_tick=last,
        complete_import_count=len(complete),
        no_coverage_import_count=no_coverage_count,
        partial=metadata.sample_count == 0 or metadata.rgb_samples < metadata.sample_count,
    )


def attach_imported_renders(
    config: RecorderConfig,
    job: Mapping[str, Any],
    imports: Iterable[ImportedRenderResult | Mapping[str, Any]],
) -> RenderAttachmentResult:
    """Attach verified server-side render imports to one deterministic dataset.

    The queue job supplies identity only. Paths are accepted solely when they are
    canonical direct children of the server-owned import directory for that job.
    The existing structured dataset is verified before a force replacement is
    permitted, so a missing, conflicting, or tampered output is never promoted.
    """

    identity = _job_identity(config, job)
    imported_values = list(imports)
    with operation_lock(config.paths.runtime, f"attach_render:{identity.job_id}"):
        existing = _verify_dataset(config, identity)
        complete: list[_CompleteImport] = []
        no_coverage_count = 0
        seen_segments: set[str] = set()
        seen_requests: set[str] = set()
        for value in imported_values:
            item = _validate_import(config, identity, existing, value)
            if item is None:
                no_coverage_count += 1
                continue
            if item.segment_id in seen_segments or item.request_id in seen_requests:
                raise RecorderError("render import list contains duplicate segment or request identities")
            seen_segments.add(item.segment_id)
            seen_requests.add(item.request_id)
            complete.append(item)
        _coverage(complete)
        if not complete:
            return _result(identity, existing.metadata, complete, no_coverage_count)

        current = _verify_dataset(config, identity)
        if (
            current.manifest_sha256 != existing.manifest_sha256
            or current.samples_sha256 != existing.samples_sha256
        ):
            raise RecorderError(
                "refusing to attach RGB because the structured dataset changed during verification"
            )
        episode = resolve_episode(config.paths.captures, identity.session_id)
        scene_store = identity.output / "scene" / "scene-v1.sqlite3"
        export_episode(
            episode,
            identity.output,
            players=[identity.player_uuid],
            connections=[identity.connection_id],
            first_tick=identity.selection_start_tick,
            last_tick=identity.selection_end_tick,
            frames=[item.directory for item in complete],
            scenes=[scene_store] if scene_store.is_file() and not scene_store.is_symlink() else [],
            force=True,
        )
        verified = _verify_dataset(config, identity)
        return _result(identity, verified.metadata, complete, no_coverage_count)


__all__ = ["RenderAttachmentResult", "attach_imported_renders"]
