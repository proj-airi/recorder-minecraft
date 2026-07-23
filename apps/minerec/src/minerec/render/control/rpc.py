from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import tempfile
import threading
import uuid
from datetime import UTC, datetime
from os import stat_result
from pathlib import Path
from typing import Any, Mapping

from minerec.config import RecorderConfig
from minerec.errors import RecorderError
from minerec.processing.dataset.viewer import DatasetViewer
from minerec.processing.render.hud import (
    create_structured_hud_sidecar,
    validate_hud_sidecar_envelope,
)
from minerec.render.control.contract import (
    FULL_CLIENT_PRESENTATION_CAPABILITY_KEY,
    FULL_CLIENT_PRESENTATION_CONTRACT,
)
from minerec.render.control.queue import (
    DEFAULT_DEFER_COOLDOWN_SECONDS,
    MAX_LEASE_SECONDS,
    MIN_LEASE_SECONDS,
    RenderQueueStore,
)
from minerec.render.control.sources import (
    ReplayNotReadyError,
    ReplaySegmentSource,
    resolve_replay_segments,
)
from minerec.render.control.transfer import (
    ImportedRenderResult,
    create_portable_render_request,
    import_render_bundle,
    write_portable_render_request,
)

PLAN_OWNER = "mc-recorder"
PLAN_TYPE = "mc-recorder-remote-render-plan-v1"
FINALIZE_TYPE = "mc-recorder-remote-render-finalize-v1"
SKIP_TYPE = "mc-recorder-remote-render-skip-v1"
MAX_RPC_BODY_BYTES = 64 * 1024
MAX_PLAN_BYTES = 4 * 1024 * 1024
MAX_FINALIZE_BYTES = 4 * 1024 * 1024
MAX_SEGMENTS = 1024
MAX_ERROR_CHARS = 2048
PORTABLE_NO_GUI_CAPABILITY = "portable_request_no_gui"
STRUCTURED_CLAIM_FAILURE_CAPABILITY = "structured_claim_failure"
PLAN_PREPARATION_HEARTBEAT_SECONDS = 20.0
_HEX_24_RE = re.compile(r"^[0-9a-f]{24}$")
_HEX_32_RE = re.compile(r"^[0-9a-f]{32}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SEGMENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,159}$")
_ACTION_RE = re.compile(r"^[a-z][a-z-]{0,31}$")


def _json_bytes(value: Mapping[str, Any]) -> bytes:
    try:
        return (
            json.dumps(
                value,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError, RecursionError) as exc:
        raise RecorderError("render RPC body must be finite JSON") from exc


def _strict_object(
    value: object,
    *,
    required: set[str],
    optional: set[str] = frozenset(),  # ty:ignore[invalid-parameter-default]
    label: str,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RecorderError(f"{label} must be a JSON object")
    missing = required - set(value)
    extras = set(value) - required - optional
    if missing:
        raise RecorderError(f"{label} is missing fields: {', '.join(sorted(missing))}")
    if extras:
        raise RecorderError(f"{label} contains unsupported fields: {', '.join(sorted(extras))}")
    return value  # ty:ignore[invalid-return-type]


def _canonical_uuid(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise RecorderError(f"invalid {label}")
    try:
        canonical = str(uuid.UUID(value))
    except (ValueError, AttributeError) as exc:
        raise RecorderError(f"invalid {label}") from exc
    if value != canonical:
        raise RecorderError(f"{label} must use canonical UUID spelling")
    return canonical


def _bounded_integer(value: object, label: str, minimum: int, maximum: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum or value > maximum:
        raise RecorderError(f"{label} must be an integer in {minimum}..{maximum}")
    return value


def _opaque(value: object, label: str, maximum: int = 160) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum or value.startswith(".") or "/" in value or "\\" in value or any(ord(character) < 32 for character in value):
        raise RecorderError(f"invalid {label}")
    return value


def _sha256_file(path: Path, label: str) -> tuple[str, int]:
    if path.is_symlink() or not path.is_file():
        raise RecorderError(f"{label} is missing or symlinked: {path}")
    digest = hashlib.sha256()
    try:
        before = path.stat()
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
        after = path.stat()
    except OSError as exc:
        raise RecorderError(f"cannot verify {label}: {path}") from exc

    def identity(stat: stat_result) -> tuple[int, int, int, int]:
        return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns

    if identity(before) != identity(after):
        raise RecorderError(f"{label} changed during verification: {path}")
    return digest.hexdigest(), after.st_size


def _ensure_directory(path: Path, label: str, *, create: bool = False) -> Path:
    requested = path.expanduser()
    if requested.is_symlink():
        raise RecorderError(f"{label} may not be a symlink: {requested}")
    if create:
        requested.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not requested.is_dir():
        raise RecorderError(f"{label} is not a directory: {requested}")
    return requested.resolve()


def _assert_safe_descendant(root: Path, path: Path, label: str) -> Path:
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise RecorderError(f"{label} escapes the render RPC root") from exc
    cursor = root
    if cursor.is_symlink() or not cursor.is_dir():
        raise RecorderError(f"render RPC root is unsafe: {root}")
    for component in relative.parts:
        cursor /= component
        if cursor.is_symlink():
            raise RecorderError(f"{label} traverses a symlink: {cursor}")
    try:
        path.resolve().relative_to(root)
    except (OSError, ValueError) as exc:
        raise RecorderError(f"{label} escapes the render RPC root") from exc
    return path


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _publish_json(path: Path, value: Mapping[str, Any], maximum: int, label: str) -> str:
    encoded = _json_bytes(value)
    if len(encoded) > maximum:
        raise RecorderError(f"{label} exceeds its size limit")
    if path.is_symlink():
        raise RecorderError(f"{label} path may not be a symlink: {path}")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.tmp-", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path, follow_symlinks=False)
        except FileExistsError:
            if path.is_symlink() or not path.is_file() or path.read_bytes() != encoded:
                raise RecorderError(f"{label} already exists with conflicting bytes: {path}")
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)
    return hashlib.sha256(encoded).hexdigest()


def _read_json(path: Path, maximum: int, label: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise RecorderError(f"{label} is missing or symlinked: {path}")
    try:
        before = path.stat()
        if before.st_size > maximum:
            raise RecorderError(f"{label} exceeds its size limit")
        raw = path.read_bytes()
        after = path.stat()
    except OSError as exc:
        raise RecorderError(f"cannot read {label}: {path}") from exc

    def identity(stat: stat_result) -> tuple[int, int, int, int]:
        return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns

    if identity(before) != identity(after):
        raise RecorderError(f"{label} changed while it was read")
    try:
        value = json.loads(raw)
    except (ValueError, RecursionError) as exc:
        raise RecorderError(f"{label} is invalid JSON") from exc
    if not isinstance(value, dict):
        raise RecorderError(f"{label} must be a JSON object")
    return value


class _PlanLeaseKeeper:
    """Renew a worker lease while the server prepares immutable render inputs."""

    def __init__(
        self,
        queue: RenderQueueStore,
        *,
        worker_id: str,
        attempt_id: str,
        lease_token: str,
        lease_seconds: int,
        interval_seconds: float = PLAN_PREPARATION_HEARTBEAT_SECONDS,
    ) -> None:
        self.queue = queue
        self.worker_id = worker_id
        self.attempt_id = attempt_id
        self.lease_token = lease_token
        self.lease_seconds = lease_seconds
        self.interval_seconds = interval_seconds
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name="mc-recorder-render-plan-lease",
            daemon=True,
        )
        self._error: BaseException | None = None
        self._started = False

    def start(self) -> None:
        self._renew()
        self._started = True
        self._thread.start()

    def finish(self) -> None:
        self._join()
        if self._error is not None:
            raise RecorderError(f"render lease renewal failed while preparing the server plan: {self._error}") from self._error
        self._renew()

    def abort(self) -> None:
        self._join()

    def _join(self) -> None:
        self._stop.set()
        if self._started:
            self._thread.join(timeout=30)
            if self._thread.is_alive() and self._error is None:
                self._error = RecorderError("render plan lease keeper did not stop within 30 seconds")

    def _renew(self) -> None:
        self.queue.heartbeat(
            self.worker_id,
            self.attempt_id,
            self.lease_token,
            phase="downloading",
            message="Preparing verified server render inputs",
            lease_seconds=self.lease_seconds,
        )

    def _run(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            try:
                self._renew()
            except BaseException as exc:
                self._error = exc
                return


def _validate_job_payload(value: object) -> dict[str, Any]:
    payload = _strict_object(
        value,
        required={
            "recording_id",
            "session_id",
            "player_uuid",
            "connection_id",
            "dataset_id",
            "start_tick",
            "end_tick",
            "render",
        },
        optional={"selection_start_tick", "selection_end_tick"},
        label="render job payload",
    )
    if not isinstance(payload["recording_id"], str) or not _HEX_24_RE.fullmatch(payload["recording_id"]):
        raise RecorderError("render job has an invalid recording ID")
    _opaque(payload["session_id"], "session ID", 128)
    _canonical_uuid(payload["player_uuid"], "player UUID")
    _canonical_uuid(payload["connection_id"], "connection UUID")
    if not isinstance(payload["dataset_id"], str) or not _HEX_32_RE.fullmatch(payload["dataset_id"]):
        raise RecorderError("render job has an invalid dataset ID")
    first_tick = _bounded_integer(payload["start_tick"], "start tick", 0, 2**63 - 1)
    last_tick = _bounded_integer(payload["end_tick"], "end tick", 0, 2**63 - 1)
    if first_tick > last_tick:
        raise RecorderError("render job tick range is reversed")
    if ("selection_start_tick" in payload) != ("selection_end_tick" in payload):
        raise RecorderError("render job selection tick bounds must be supplied together")
    selection_first = _bounded_integer(
        payload.get("selection_start_tick", first_tick),
        "selection start tick",
        0,
        2**63 - 1,
    )
    selection_last = _bounded_integer(
        payload.get("selection_end_tick", last_tick),
        "selection end tick",
        0,
        2**63 - 1,
    )
    if not selection_first <= first_tick <= last_tick <= selection_last:
        raise RecorderError("render job tick range is outside its dataset selection")
    render = _strict_object(
        payload["render"],
        required={"width", "height", "fps"},
        optional={"no_gui", "presentation_contract"},
        label="render settings",
    )
    width = _bounded_integer(render["width"], "render width", 160, 3840)
    height = _bounded_integer(render["height"], "render height", 90, 2160)
    if width * height > 3840 * 2160:
        raise RecorderError("render resolution exceeds the maximum pixel count")
    if render["fps"] != 20 or isinstance(render["fps"], bool):
        raise RecorderError("remote rendering supports exactly 20 FPS")
    if "no_gui" in render and not isinstance(render["no_gui"], bool):
        raise RecorderError("render no_gui must be a boolean")
    presentation_contract = render.get("presentation_contract")
    if presentation_contract is not None:
        if presentation_contract != FULL_CLIENT_PRESENTATION_CONTRACT:
            raise RecorderError("render presentation_contract is unsupported")
        if render.get("no_gui", True):
            raise RecorderError("render presentation_contract requires no_gui=false")
    return payload


def _validate_plan(value: object) -> dict[str, Any]:
    plan = _strict_object(
        value,
        required={
            "schema_version",
            "plan_type",
            "owner",
            "created_at",
            "job_id",
            "attempt_id",
            "generation",
            "worker_id",
            "lease_token_sha256",
            "payload",
            "sources",
        },
        optional={"structured_hud"},
        label="remote render plan",
    )
    if plan["schema_version"] != 1 or isinstance(plan["schema_version"], bool):
        raise RecorderError("unsupported remote render plan schema")
    if plan["plan_type"] != PLAN_TYPE or plan["owner"] != PLAN_OWNER:
        raise RecorderError("remote render plan is not owned by mc-recorder")
    if not isinstance(plan["created_at"], str) or len(plan["created_at"]) > 64:
        raise RecorderError("remote render plan has an invalid creation time")
    _canonical_uuid(plan["job_id"], "render job ID")
    _canonical_uuid(plan["attempt_id"], "render attempt ID")
    _canonical_uuid(plan["worker_id"], "render worker ID")
    _bounded_integer(plan["generation"], "render attempt generation", 1, 2**31 - 1)
    if not isinstance(plan["lease_token_sha256"], str) or not _SHA256_RE.fullmatch(plan["lease_token_sha256"]):
        raise RecorderError("remote render plan has an invalid lease token digest")
    payload = _validate_job_payload(plan["payload"])
    structured_hud = plan.get("structured_hud")
    if structured_hud is not None:
        if not isinstance(structured_hud, dict) or set(structured_hud) != {
            "file",
            "schema_version",
            "sidecar_type",
            "format",
            "sha256",
            "size_bytes",
            "record_count",
            "first_tick",
            "last_tick",
            "dataset_id",
            "dataset_manifest_sha256",
            "samples_sha256",
            "session_id",
            "player_uuid",
            "connection_id",
        }:
            raise RecorderError("remote render plan structured_hud has invalid fields")
        if structured_hud.get("file") != "hud/structured-hud.jsonl":
            raise RecorderError("remote render plan has a non-canonical structured_hud file")
        envelope = validate_hud_sidecar_envelope(
            {key: value for key, value in structured_hud.items() if key != "file"},
            "remote render plan structured_hud",
        )
        if (
            envelope["dataset_id"] != payload["dataset_id"]
            or envelope["session_id"] != payload["session_id"]
            or envelope["player_uuid"] != payload["player_uuid"]
            or envelope["connection_id"] != payload["connection_id"]
            or envelope["first_tick"] != payload["start_tick"]
            or envelope["last_tick"] != payload["end_tick"]
        ):
            raise RecorderError("remote render plan structured_hud identity is inconsistent")
        if payload["render"].get("presentation_contract") != FULL_CLIENT_PRESENTATION_CONTRACT:
            raise RecorderError("remote render plan structured_hud requires the current presentation")
    elif payload["render"].get("presentation_contract") == FULL_CLIENT_PRESENTATION_CONTRACT:
        raise RecorderError("current full-client render plan requires structured_hud")
    sources = plan["sources"]
    if not isinstance(sources, list) or not 1 <= len(sources) <= MAX_SEGMENTS:
        raise RecorderError(f"remote render plan must contain 1..{MAX_SEGMENTS} sources")
    previous_ordinal: int | None = None
    seen_ids: set[str] = set()
    seen_ordinals: set[int] = set()
    for source in sources:
        source = _strict_object(
            source,
            required={
                "segment_id",
                "segment_ordinal",
                "player_uuid",
                "connection_id",
                "format",
                "sha256",
                "size_bytes",
                "replay_file",
                "request_file",
                "upload_directory",
                "import_directory",
            },
            label="remote render plan source",
        )
        segment_id = source["segment_id"]
        if not isinstance(segment_id, str) or not _SEGMENT_RE.fullmatch(segment_id):
            raise RecorderError("remote render plan has an invalid segment ID")
        ordinal = _bounded_integer(source["segment_ordinal"], "replay segment ordinal", 0, 2**31 - 1)
        if segment_id in seen_ids or ordinal in seen_ordinals:
            raise RecorderError("remote render plan has duplicate replay segments")
        if previous_ordinal is not None and ordinal >= previous_ordinal:
            raise RecorderError("remote render plan sources are not newest-first")
        previous_ordinal = ordinal
        seen_ids.add(segment_id)
        seen_ordinals.add(ordinal)
        if source["player_uuid"] != payload["player_uuid"]:
            raise RecorderError("remote render source player does not match its job")
        if source["connection_id"] != payload["connection_id"]:
            raise RecorderError("remote render source connection does not match its job")
        if source["format"] != "flashback":
            raise RecorderError("remote rendering supports Flashback sources only")
        if not isinstance(source["sha256"], str) or not _SHA256_RE.fullmatch(source["sha256"]):
            raise RecorderError("remote render source has an invalid SHA-256")
        _bounded_integer(source["size_bytes"], "replay source size", 1, 2**63 - 1)
        stem = f"{ordinal:010d}-{segment_id}"
        expected = {
            "replay_file": f"sources/{stem}.zip",
            "request_file": f"requests/{stem}.json",
            "upload_directory": f"uploads/{segment_id}",
            "import_directory": f"render-jobs/{plan['job_id']}/{segment_id}",
        }
        for key, expected_value in expected.items():
            if source[key] != expected_value:
                raise RecorderError(f"remote render plan has a non-canonical {key}")
    return plan


class RenderRpcService:
    """Path-free SSH RPC boundary for persistent and one-shot GUI workers."""

    def __init__(self, config: RecorderConfig) -> None:
        self.config = config
        self.runtime = _ensure_directory(config.paths.runtime, "runtime root", create=True)
        self.rpc_root = _ensure_directory(self.runtime / "render-rpc", "render RPC root", create=True)
        self.attempts_root = _ensure_directory(self.rpc_root / "attempts", "render RPC attempts root", create=True)
        self.exports_root = _ensure_directory(config.paths.exports, "export root", create=True)
        self.imports_root = _ensure_directory(self.exports_root / "render-jobs", "durable render import root", create=True)
        self.queue = RenderQueueStore(self.runtime / "render-queue.sqlite3")
        self.dataset_viewer = DatasetViewer(config.paths.exports, config.paths.runtime)

    def dispatch(self, action: str, body: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(action, str) or not _ACTION_RE.fullmatch(action):
            raise RecorderError("invalid render RPC action")
        if not isinstance(body, dict):
            raise RecorderError("render RPC body must be a JSON object")
        if len(_json_bytes(body)) > MAX_RPC_BODY_BYTES:
            raise RecorderError("render RPC body exceeds the size limit")
        handlers = {
            "register": self._register,
            "worker-heartbeat": self._worker_heartbeat,
            "claim": self._claim,
            "request": self._request,
            "heartbeat": self._heartbeat,
            "fail": self._fail,
            "finalize": self._finalize,
        }
        handler = handlers.get(action)
        if handler is None:
            raise RecorderError(f"unsupported render RPC action: {action}")
        return handler(dict(body))

    def _register(self, body: dict[str, Any]) -> dict[str, Any]:
        request = _strict_object(
            body,
            required={"worker_id", "name", "capabilities"},
            label="register request",
        )
        worker_id = _canonical_uuid(request["worker_id"], "render worker ID")
        name = request["name"]
        if not isinstance(name, str) or not name.strip() or len(name) > 80 or any(ord(character) < 32 for character in name):
            raise RecorderError("render worker name must be between 1 and 80 characters")
        if not isinstance(request["capabilities"], dict):
            raise RecorderError("render worker capabilities must be a JSON object")
        worker = self.queue.register_worker(name, worker_id=worker_id, capabilities=request["capabilities"])
        return {
            "worker": worker,
            "server_capabilities": {
                STRUCTURED_CLAIM_FAILURE_CAPABILITY: True,
                FULL_CLIENT_PRESENTATION_CAPABILITY_KEY: (FULL_CLIENT_PRESENTATION_CONTRACT),
                "worker_presence_heartbeat": True,
                "deferred_job_cooldown": True,
            },
        }

    def _worker_heartbeat(self, body: dict[str, Any]) -> dict[str, Any]:
        request = _strict_object(
            body,
            required={"worker_id"},
            label="worker heartbeat request",
        )
        worker_id = _canonical_uuid(request["worker_id"], "render worker ID")
        return {"worker": self.queue.worker_heartbeat(worker_id)}

    def _claim(self, body: dict[str, Any]) -> dict[str, Any]:
        request = _strict_object(
            body,
            required={"worker_id"},
            optional={"job_id", "lease_seconds"},
            label="claim request",
        )
        worker_id = _canonical_uuid(request["worker_id"], "render worker ID")
        worker = next(
            (value for value in self.queue.workers() if value["id"] == worker_id),
            None,
        )
        if worker is None:
            raise RecorderError("render worker must register before claiming a job")
        capabilities = worker.get("capabilities")
        if not isinstance(capabilities, dict) or capabilities.get(PORTABLE_NO_GUI_CAPABILITY) is not True:
            raise RecorderError("render worker is too old for GUI-mode-bound requests; update its mc-recorder tooling")
        if capabilities.get(FULL_CLIENT_PRESENTATION_CAPABILITY_KEY) != FULL_CLIENT_PRESENTATION_CONTRACT:
            raise RecorderError("render worker does not support the required full-client presentation contract; update its mc-recorder tooling")
        job_id = _canonical_uuid(request["job_id"], "render job ID") if request.get("job_id") is not None else None
        lease_seconds = _bounded_integer(
            request.get("lease_seconds", 120),
            "lease seconds",
            MIN_LEASE_SECONDS,
            MAX_LEASE_SECONDS,
        )
        claim = self.queue.claim(worker_id, job_id=job_id, lease_seconds=lease_seconds)
        if claim is None:
            return {"claim": None, "sources": [], "upload_directory": None}
        attempt = claim["attempt"]
        lease_keeper = _PlanLeaseKeeper(
            self.queue,
            worker_id=worker_id,
            attempt_id=attempt["id"],
            lease_token=attempt["lease_token"],
            lease_seconds=lease_seconds,
        )
        plan_root: Path | None = None
        try:
            lease_keeper.start()
            plan, plan_root = self._create_plan(claim, worker_id)
            lease_keeper.finish()
        except ReplayNotReadyError as exc:
            lease_keeper.abort()
            queued = self.queue.defer_attempt(
                worker_id,
                attempt["id"],
                attempt["lease_token"],
                str(exc),
            )
            return {
                "claim": None,
                "sources": [],
                "upload_directory": None,
                "pending_job": queued,
                "reason": "replay_pending",
                "deferred_job_cooldown_seconds": DEFAULT_DEFER_COOLDOWN_SECONDS,
            }
        except Exception as exc:
            lease_keeper.abort()
            try:
                failed = self.queue.fail_attempt(
                    worker_id,
                    attempt["id"],
                    attempt["lease_token"],
                    str(exc),
                )
            except Exception:
                if plan_root is not None:
                    try:
                        self._remove_hud_sidecar(plan_root)
                    except Exception as cleanup_exc:
                        exc.add_note(f"structured HUD cleanup also failed: {str(cleanup_exc) or type(cleanup_exc).__name__}")
                raise exc
            if plan_root is not None:
                try:
                    self._remove_hud_sidecar(plan_root)
                except Exception as cleanup_exc:
                    exc.add_note(f"structured HUD cleanup also failed: {str(cleanup_exc) or type(cleanup_exc).__name__}")
            detail = (str(exc) or type(exc).__name__)[:MAX_ERROR_CHARS]
            if capabilities.get(STRUCTURED_CLAIM_FAILURE_CAPABILITY) is not True:
                raise RecorderError(f"server failed claimed render job {failed['id']} before transfer: {detail}") from exc
            return {
                "claim": None,
                "sources": [],
                "upload_directory": None,
                "failed_job": failed,
                "reason": "claim_failed",
                "error": detail,
            }
        return {
            "claim": claim,
            "sources": [self._source_response(plan_root, source) for source in plan["sources"]],
            "structured_hud": self._hud_response(plan_root, plan.get("structured_hud")),
            "upload_directory": str(plan_root / "uploads"),
        }

    def _create_plan(self, claim: dict[str, Any], worker_id: str) -> tuple[dict[str, Any], Path]:
        job = claim["job"]
        attempt = claim["attempt"]
        payload = _validate_job_payload(job.get("payload"))
        job_id = _canonical_uuid(job.get("id"), "render job ID")
        attempt_id = _canonical_uuid(attempt.get("id"), "render attempt ID")
        generation = _bounded_integer(attempt.get("generation"), "render attempt generation", 1, 2**31 - 1)
        lease_token = attempt.get("lease_token")
        if not isinstance(lease_token, str) or not 16 <= len(lease_token) <= 256:
            raise RecorderError("render queue returned an invalid lease token")
        sources = resolve_replay_segments(
            replays_root=self.config.paths.replays,
            session_id=payload["session_id"],
            player_uuid=payload["player_uuid"],
            connection_id=payload["connection_id"],
        )
        if not 1 <= len(sources) <= MAX_SEGMENTS:
            raise RecorderError(f"exact connection has too many replay segments (max {MAX_SEGMENTS})")
        plan_root = self.attempts_root / attempt_id
        if plan_root.exists() or plan_root.is_symlink():
            raise RecorderError(f"render attempt plan already exists: {attempt_id}")
        plan_root.mkdir(mode=0o700)
        for name in ("sources", "requests", "uploads", "hud"):
            (plan_root / name).mkdir(mode=0o700)
        planned_sources: list[dict[str, Any]] = []
        try:
            for source in sorted(sources, key=lambda value: value.segment_ordinal, reverse=True):
                planned = self._pin_source(plan_root, source, payload, job_id)
                planned_sources.append(planned)
            plan: dict[str, Any] = {
                "schema_version": 1,
                "plan_type": PLAN_TYPE,
                "owner": PLAN_OWNER,
                "created_at": datetime.now(UTC).isoformat(),
                "job_id": job_id,
                "attempt_id": attempt_id,
                "generation": generation,
                "worker_id": worker_id,
                "lease_token_sha256": hashlib.sha256(lease_token.encode("utf-8")).hexdigest(),
                "payload": payload,
                "sources": planned_sources,
            }
            if payload["render"].get("presentation_contract") == FULL_CLIENT_PRESENTATION_CONTRACT:
                sidecar = create_structured_hud_sidecar(
                    self.dataset_viewer,
                    payload["dataset_id"],
                    plan_root / "hud" / "structured-hud.jsonl",
                    session_id=payload["session_id"],
                    player_uuid=payload["player_uuid"],
                    connection_id=payload["connection_id"],
                    first_tick=payload["start_tick"],
                    last_tick=payload["end_tick"],
                    selection_first_tick=payload.get("selection_start_tick", payload["start_tick"]),
                    selection_last_tick=payload.get("selection_end_tick", payload["end_tick"]),
                )
                plan["structured_hud"] = {
                    **sidecar.envelope(),
                    "file": "hud/structured-hud.jsonl",
                }
            _validate_plan(plan)
            _publish_json(plan_root / "plan.json", plan, MAX_PLAN_BYTES, "remote render plan")
        except Exception as exc:
            # The hardlinks deliberately remain only when a durable plan exists.
            for child in (plan_root / "sources").glob("*"):
                if not child.is_symlink() and child.is_file():
                    child.unlink(missing_ok=True)
            try:
                self._remove_hud_sidecar(plan_root)
            except Exception as cleanup_exc:
                exc.add_note(f"structured HUD cleanup also failed: {str(cleanup_exc) or type(cleanup_exc).__name__}")
            raise
        return plan, plan_root

    def _pin_source(
        self,
        plan_root: Path,
        source: ReplaySegmentSource,
        payload: Mapping[str, Any],
        job_id: str,
    ) -> dict[str, Any]:
        segment_id = source.segment_id
        if not isinstance(segment_id, str) or not _SEGMENT_RE.fullmatch(segment_id):
            raise RecorderError("resolved replay segment has an invalid segment ID")
        ordinal = _bounded_integer(source.segment_ordinal, "replay segment ordinal", 0, 2**31 - 1)
        if source.player_uuid != payload["player_uuid"] or source.connection_id != payload["connection_id"]:
            raise RecorderError("resolved replay segment identity does not match the render job")
        if source.replay_format != "flashback":
            raise RecorderError("remote rendering supports Flashback replay archives only")
        if not _SHA256_RE.fullmatch(source.sha256) or not 1 <= source.size_bytes <= 2**63 - 1:
            raise RecorderError("resolved replay segment has an invalid integrity envelope")
        stem = f"{ordinal:010d}-{segment_id}"
        replay_file = f"sources/{stem}.zip"
        pinned = plan_root / replay_file
        try:
            os.link(source.path, pinned, follow_symlinks=False)
        except OSError as exc:
            raise RecorderError("cannot pin replay for rendering; replay and runtime paths must share a filesystem") from exc
        digest, size = _sha256_file(pinned, "pinned replay source")
        if digest != source.sha256 or size != source.size_bytes:
            pinned.unlink(missing_ok=True)
            raise RecorderError("pinned replay changed after exact source resolution")
        return {
            "segment_id": segment_id,
            "segment_ordinal": ordinal,
            "player_uuid": source.player_uuid,
            "connection_id": source.connection_id,
            "format": "flashback",
            "sha256": source.sha256,
            "size_bytes": source.size_bytes,
            "replay_file": replay_file,
            "request_file": f"requests/{stem}.json",
            "upload_directory": f"uploads/{segment_id}",
            "import_directory": f"render-jobs/{job_id}/{segment_id}",
        }

    @staticmethod
    def _source_response(plan_root: Path, source: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "segment_id": source["segment_id"],
            "segment_ordinal": source["segment_ordinal"],
            "player_uuid": source["player_uuid"],
            "connection_id": source["connection_id"],
            "path": str(plan_root / source["replay_file"]),
            "replay_format": source["format"],
            "sha256": source["sha256"],
            "size_bytes": source["size_bytes"],
        }

    @staticmethod
    def _hud_response(plan_root: Path, structured_hud: object) -> dict[str, Any] | None:
        if structured_hud is None:
            return None
        assert isinstance(structured_hud, dict)
        return {
            **{key: value for key, value in structured_hud.items() if key != "file"},
            "path": str(plan_root / structured_hud["file"]),  # ty:ignore[invalid-argument-type, unsupported-operator]
        }  # ty:ignore[invalid-return-type]

    def _remove_hud_sidecar(self, plan_root: Path) -> None:
        root = _assert_safe_descendant(self.attempts_root, plan_root, "render attempt HUD root")
        if root.parent != self.attempts_root:
            raise RecorderError("render attempt HUD root is not a direct owned attempt")
        hud_root = root / "hud"
        sidecar = hud_root / "structured-hud.jsonl"
        _assert_safe_descendant(self.rpc_root, hud_root, "render attempt HUD directory")
        _assert_safe_descendant(self.rpc_root, sidecar, "render attempt HUD sidecar")
        if sidecar.is_symlink():
            raise RecorderError("render attempt HUD sidecar may not be a symlink")
        if not sidecar.exists():
            return
        if not sidecar.is_file():
            raise RecorderError("render attempt HUD sidecar is not a regular file")
        sidecar.unlink()
        _fsync_directory(hud_root)

    def _plan(self, attempt_id: str) -> tuple[dict[str, Any], Path]:
        canonical = _canonical_uuid(attempt_id, "render attempt ID")
        root = self.attempts_root / canonical
        _assert_safe_descendant(self.rpc_root, root, "render attempt plan")
        if root.is_symlink() or not root.is_dir():
            raise RecorderError("render attempt has no owned server plan")
        for name in ("sources", "requests", "uploads", "hud"):
            child = root / name
            _assert_safe_descendant(self.rpc_root, child, f"render attempt {name}")
            if child.is_symlink() or not child.is_dir():
                raise RecorderError(f"render attempt {name} directory is unsafe")
        plan = _validate_plan(_read_json(root / "plan.json", MAX_PLAN_BYTES, "remote render plan"))
        if plan["attempt_id"] != canonical:
            raise RecorderError("remote render plan does not match its attempt directory")
        return plan, root

    @staticmethod
    def _authorize_plan(plan: Mapping[str, Any], worker_id: str, lease_token: object) -> str:
        canonical_worker = _canonical_uuid(worker_id, "render worker ID")
        if plan["worker_id"] != canonical_worker:
            raise RecorderError("render attempt is not owned by this worker")
        if not isinstance(lease_token, str) or not 16 <= len(lease_token) <= 256:
            raise RecorderError("invalid render lease token")
        actual = hashlib.sha256(lease_token.encode("utf-8")).hexdigest()
        if not hmac.compare_digest(actual, str(plan["lease_token_sha256"])):
            raise RecorderError("render lease is not owned by this worker")
        return canonical_worker

    def _fence(self, plan: Mapping[str, Any], worker_id: str, attempt_id: str, lease_token: str) -> dict[str, Any]:
        self._authorize_plan(plan, worker_id, lease_token)
        job = self.queue.get(plan["job_id"])
        active = job.get("active_attempt")
        if not isinstance(active, dict) or active.get("id") != attempt_id:
            raise RecorderError("render lease is no longer active")
        phase = job.get("state")
        if phase not in {"downloading", "rendering", "uploading"}:
            raise RecorderError("render lease is no longer active")
        return self.queue.heartbeat(
            worker_id,
            attempt_id,
            lease_token,
            phase=phase,
            lease_seconds=120,
        )

    def _request(self, body: dict[str, Any]) -> dict[str, Any]:
        request = _strict_object(
            body,
            required={"worker_id", "attempt_id", "lease_token", "segment_id"},
            optional={"newer_cutoff"},
            label="portable request request",
        )
        attempt_id = _canonical_uuid(request["attempt_id"], "render attempt ID")
        worker_id = _canonical_uuid(request["worker_id"], "render worker ID")
        segment_id = request["segment_id"]
        if not isinstance(segment_id, str) or not _SEGMENT_RE.fullmatch(segment_id):
            raise RecorderError("invalid replay segment ID")
        cutoff_value = request.get("newer_cutoff")
        cutoff = _bounded_integer(cutoff_value, "newer cutoff", 0, 2**63 - 1) if cutoff_value is not None else None
        plan, root = self._plan(attempt_id)
        self._fence(plan, worker_id, attempt_id, request["lease_token"])
        sources: list[dict[str, Any]] = plan["sources"]
        try:
            index = next(i for i, value in enumerate(sources) if value["segment_id"] == segment_id)
        except StopIteration as exc:
            raise RecorderError("replay segment is not part of this render attempt") from exc
        if index == 0 and cutoff is not None:
            raise RecorderError("the newest replay segment cannot have a newer cutoff")
        for previous in sources[:index]:
            request_path = root / previous["request_file"]
            skip_path = request_path.with_suffix(".skip.json")
            if skip_path.exists():
                raise RecorderError("render plan was already completed before this segment")
            if not request_path.is_file() or request_path.is_symlink():
                raise RecorderError("replay segments must be requested newest-to-oldest")
        payload = plan["payload"]
        start_tick = payload["start_tick"]
        end_tick = payload["end_tick"]
        if cutoff is not None and cutoff > end_tick + 1:
            raise RecorderError("newer cutoff is outside the connection tick range")
        source = sources[index]
        request_path = root / source["request_file"]
        skip_path = request_path.with_suffix(".skip.json")
        if cutoff is not None and cutoff <= start_tick:
            skip = {
                "schema_version": 1,
                "skip_type": SKIP_TYPE,
                "attempt_id": attempt_id,
                "segment_id": segment_id,
                "newer_cutoff": cutoff,
                "reason": "newer_segments_cover_connection_start",
            }
            _publish_json(skip_path, skip, MAX_RPC_BODY_BYTES, "render segment skip marker")
            if request_path.exists() or request_path.is_symlink():
                raise RecorderError("render segment already has a conflicting portable request")
            return {
                "request": None,
                "request_sha256": None,
                "segment_id": segment_id,
                "done": True,
            }
        if skip_path.exists() or skip_path.is_symlink():
            raise RecorderError("render segment already has a conflicting skip marker")
        replay = root / source["replay_file"]
        digest, size = _sha256_file(replay, "pinned replay source")
        if digest != source["sha256"] or size != source["size_bytes"]:
            raise RecorderError("pinned replay source failed its plan integrity envelope")
        episode = self._episode(payload["session_id"])
        request_id = str(
            uuid.uuid5(
                uuid.UUID(attempt_id),
                f"{segment_id}:{'none' if cutoff is None else cutoff}",
            )
        )
        portable_value = create_portable_render_request(
            episode,
            replay,
            segment_id=segment_id,
            segment_ordinal=source["segment_ordinal"],
            player_uuid=payload["player_uuid"],
            connection_id=payload["connection_id"],
            width=payload["render"]["width"],
            height=payload["render"]["height"],
            fps=payload["render"]["fps"],
            first_tick=start_tick,
            last_tick=end_tick if cutoff is None else cutoff - 1,
            request_id=request_id,
            range_policy="intersection",
            newer_cutoff=cutoff,
            no_gui=payload["render"].get("no_gui", True),
            presentation_contract=payload["render"].get("presentation_contract"),
            structured_hud=({key: value for key, value in plan["structured_hud"].items() if key != "file"} if isinstance(plan.get("structured_hud"), dict) else None),
        )
        portable = write_portable_render_request(request_path, portable_value)
        return {
            "request": portable.data,
            "request_sha256": portable.sha256,
            "segment_id": segment_id,
            "done": index == len(sources) - 1,
        }

    def _episode(self, session_id: str) -> Path:
        captures = self.config.paths.captures.resolve()
        episode = captures / _opaque(session_id, "session ID", 128)
        if episode.is_symlink() or not episode.is_dir():
            raise RecorderError("render source episode is unavailable or symlinked")
        try:
            episode.resolve(strict=True).relative_to(captures)
        except (OSError, ValueError) as exc:
            raise RecorderError("render source episode escapes the capture root") from exc
        return episode

    def _heartbeat(self, body: dict[str, Any]) -> dict[str, Any]:
        request = _strict_object(
            body,
            required={"worker_id", "attempt_id", "lease_token", "phase"},
            optional={"current", "total", "message", "lease_seconds"},
            label="heartbeat request",
        )
        worker_id = _canonical_uuid(request["worker_id"], "render worker ID")
        attempt_id = _canonical_uuid(request["attempt_id"], "render attempt ID")
        plan, _root = self._plan(attempt_id)
        self._authorize_plan(plan, worker_id, request["lease_token"])
        lease_seconds = _bounded_integer(
            request.get("lease_seconds", 120),
            "lease seconds",
            MIN_LEASE_SECONDS,
            MAX_LEASE_SECONDS,
        )
        job = self.queue.heartbeat(
            worker_id,
            attempt_id,
            request["lease_token"],
            phase=request["phase"],
            current=request.get("current"),
            total=request.get("total"),
            message=request.get("message"),
            lease_seconds=lease_seconds,
        )
        return {"job": job}

    def _fail(self, body: dict[str, Any]) -> dict[str, Any]:
        request = _strict_object(
            body,
            required={"worker_id", "attempt_id", "lease_token", "error"},
            label="failure request",
        )
        worker_id = _canonical_uuid(request["worker_id"], "render worker ID")
        attempt_id = _canonical_uuid(request["attempt_id"], "render attempt ID")
        error = request["error"]
        if not isinstance(error, str) or not error or len(error) > MAX_ERROR_CHARS:
            raise RecorderError(f"render worker error must be 1..{MAX_ERROR_CHARS} characters")
        plan, root = self._plan(attempt_id)
        self._authorize_plan(plan, worker_id, request["lease_token"])
        job = self.queue.fail_attempt(worker_id, attempt_id, request["lease_token"], error)
        self._remove_hud_sidecar(root)
        return {"job": job}

    def _finalize(self, body: dict[str, Any]) -> dict[str, Any]:
        request = _strict_object(
            body,
            required={"worker_id", "attempt_id", "lease_token"},
            label="finalize request",
        )
        worker_id = _canonical_uuid(request["worker_id"], "render worker ID")
        attempt_id = _canonical_uuid(request["attempt_id"], "render attempt ID")
        plan, root = self._plan(attempt_id)
        self._authorize_plan(plan, worker_id, request["lease_token"])
        job = self.queue.get(plan["job_id"])
        if job["state"] in {"failed", "canceled"}:
            self._remove_hud_sidecar(root)
            raise RecorderError(f"render job cannot be finalized while {job['state']}")
        if job["state"] in {"attaching", "complete", "partial"}:
            response = self._finalize_response(plan, root, job)
            self._remove_hud_sidecar(root)
            return response
        requested = self._requested_sources(plan, root)
        if not requested:
            raise RecorderError("render attempt has no portable requests to finalize")
        for source in requested:
            upload = root / source["upload_directory"]
            _assert_safe_descendant(self.rpc_root, upload, "render bundle upload")
            if upload.is_symlink() or not upload.is_dir():
                raise RecorderError(f"render bundle upload is missing for {source['segment_id']}")
        if job["state"] in {"downloading", "rendering", "uploading"}:
            self._fence(plan, worker_id, attempt_id, request["lease_token"])
            self.queue.heartbeat(
                worker_id,
                attempt_id,
                request["lease_token"],
                phase="uploading",
                lease_seconds=120,
            )
            job = self.queue.mark_uploaded(
                worker_id,
                attempt_id,
                request["lease_token"],
                {
                    "attempt_id": attempt_id,
                    "plan_sha256": _sha256_file(root / "plan.json", "remote render plan")[0],
                    "segments": [source["segment_id"] for source in requested],
                },
            )
        elif job["state"] != "verifying":
            raise RecorderError(f"render job cannot be finalized while {job['state']}")
        try:
            imports = [self._import_source(plan, root, source) for source in requested]
            finalization = self._finalization_value(plan, imports)
            _publish_json(
                root / "finalize.json",
                finalization,
                MAX_FINALIZE_BYTES,
                "remote render finalization",
            )
        except Exception as exc:
            try:
                self.queue.fail(plan["job_id"], str(exc))
            except Exception:
                pass
            try:
                self._remove_hud_sidecar(root)
            except Exception as cleanup_exc:
                exc.add_note(f"structured HUD cleanup also failed: {str(cleanup_exc) or type(cleanup_exc).__name__}")
            raise
        response = self._response_from_imports(plan, self.queue.get(plan["job_id"]), imports)
        self._remove_hud_sidecar(root)
        return response

    def _requested_sources(self, plan: Mapping[str, Any], root: Path) -> list[dict[str, Any]]:
        requested: list[dict[str, Any]] = []
        stopped = False
        missing = False
        for source in plan["sources"]:
            request_path = root / source["request_file"]
            skip_path = request_path.with_suffix(".skip.json")
            if skip_path.exists() or skip_path.is_symlink():
                if skip_path.is_symlink():
                    raise RecorderError("render segment skip marker may not be a symlink")
                marker = _strict_object(
                    _read_json(skip_path, MAX_RPC_BODY_BYTES, "render segment skip marker"),
                    required={
                        "schema_version",
                        "skip_type",
                        "attempt_id",
                        "segment_id",
                        "newer_cutoff",
                        "reason",
                    },
                    label="render segment skip marker",
                )
                if (
                    not requested
                    or missing
                    or stopped
                    or marker["schema_version"] != 1
                    or marker["skip_type"] != SKIP_TYPE
                    or marker["attempt_id"] != plan["attempt_id"]
                    or marker["segment_id"] != source["segment_id"]
                    or marker["reason"] != "newer_segments_cover_connection_start"
                    or not isinstance(marker["newer_cutoff"], int)
                    or isinstance(marker["newer_cutoff"], bool)
                    or marker["newer_cutoff"] > plan["payload"]["start_tick"]
                ):
                    raise RecorderError("render segment skip marker does not match its plan")
                stopped = True
                continue
            if request_path.exists() or request_path.is_symlink():
                if stopped or missing or request_path.is_symlink() or not request_path.is_file():
                    raise RecorderError("render plan request sequence is unsafe")
                requested.append(source)
            else:
                missing = True
        return requested

    def _import_source(self, plan: Mapping[str, Any], root: Path, source: Mapping[str, Any]) -> ImportedRenderResult:
        request_path = root / source["request_file"]
        replay = root / source["replay_file"]
        upload = root / source["upload_directory"]
        destination = self.exports_root / source["import_directory"]
        for path, label in (
            (request_path, "portable request"),
            (replay, "pinned replay"),
            (upload, "render bundle upload"),
        ):
            _assert_safe_descendant(self.rpc_root, path, label)
        _assert_safe_descendant(self.exports_root, destination, "durable render import")
        job_import_root = destination.parent
        if job_import_root.is_symlink():
            raise RecorderError("durable render job import directory may not be a symlink")
        job_import_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        _assert_safe_descendant(self.exports_root, job_import_root, "durable render job import directory")
        digest, size = _sha256_file(replay, "pinned replay source")
        if digest != source["sha256"] or size != source["size_bytes"]:
            raise RecorderError("pinned replay source failed its plan integrity envelope")
        return import_render_bundle(request_path, upload, replay, destination)

    @staticmethod
    def _finalization_value(plan: Mapping[str, Any], imports: list[ImportedRenderResult]) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "finalize_type": FINALIZE_TYPE,
            "owner": PLAN_OWNER,
            "job_id": plan["job_id"],
            "attempt_id": plan["attempt_id"],
            "worker_id": plan["worker_id"],
            "imports": [
                {
                    "segment_id": result.directory.name,
                    "request_id": result.request_id,
                    "status": result.status,
                }
                for result in imports
            ],
        }

    def _finalize_response(self, plan: Mapping[str, Any], root: Path, job: Mapping[str, Any]) -> dict[str, Any]:
        value = _read_json(root / "finalize.json", MAX_FINALIZE_BYTES, "remote render finalization")
        _strict_object(
            value,
            required={
                "schema_version",
                "finalize_type",
                "owner",
                "job_id",
                "attempt_id",
                "worker_id",
                "imports",
            },
            label="remote render finalization",
        )
        if value["schema_version"] != 1 or value["finalize_type"] != FINALIZE_TYPE or value["owner"] != PLAN_OWNER or value["job_id"] != plan["job_id"] or value["attempt_id"] != plan["attempt_id"] or value["worker_id"] != plan["worker_id"]:
            raise RecorderError("remote render finalization does not match its plan")
        imports: list[dict[str, Any]] = []
        if not isinstance(value["imports"], list) or not value["imports"]:
            raise RecorderError("remote render finalization has no imports")
        source_ids = {source["segment_id"] for source in plan["sources"]}
        expected_ids = [source["segment_id"] for source in self._requested_sources(plan, root)]
        actual_ids = [item.get("segment_id") if isinstance(item, dict) else None for item in value["imports"]]
        if actual_ids != expected_ids:
            raise RecorderError("remote render finalization import order does not match its plan")
        for item in value["imports"]:
            item = _strict_object(
                item,
                required={"segment_id", "request_id", "status"},
                label="remote render finalized import",
            )
            if item["segment_id"] not in source_ids:
                raise RecorderError("remote render finalization has an unknown segment")
            _canonical_uuid(item["request_id"], "portable request ID")
            if item["status"] not in {"complete", "no_coverage"}:
                raise RecorderError("remote render finalization has an invalid status")
            source = next(source for source in plan["sources"] if source["segment_id"] == item["segment_id"])
            directory = self.exports_root / source["import_directory"]
            _assert_safe_descendant(self.exports_root, directory, "durable render finalized import")
            if directory.is_symlink() or not directory.is_dir():
                raise RecorderError("remote render finalized import is missing or symlinked")
            result_path = directory / "result.json"
            manifest_path = directory / "import-manifest.json"
            if result_path.is_symlink() or not result_path.is_file() or manifest_path.is_symlink() or not manifest_path.is_file():
                raise RecorderError("remote render finalized import lacks canonical manifests")
            imports.append(
                {
                    **item,
                    "reused": True,
                    "directory": str(directory),
                    "result": str(result_path),
                    "manifest": str(manifest_path),
                }
            )
        return self._response_from_values(plan, job, imports)

    def _response_from_imports(
        self,
        plan: Mapping[str, Any],
        job: Mapping[str, Any],
        imports: list[ImportedRenderResult],
    ) -> dict[str, Any]:
        by_name = {source["import_directory"].split("/")[-1]: source for source in plan["sources"]}
        values = [
            {
                "segment_id": by_name[result.directory.name]["segment_id"],
                "request_id": result.request_id,
                "status": result.status,
                "reused": result.reused,
                "directory": str(result.directory),
                "result": str(result.result),
                "manifest": str(result.manifest),
            }
            for result in imports
        ]
        return self._response_from_values(plan, job, values)

    @staticmethod
    def _response_from_values(plan: Mapping[str, Any], job: Mapping[str, Any], imports: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "job": dict(job),
            "identity": {
                "job_id": plan["job_id"],
                "attempt_id": plan["attempt_id"],
                "recording_id": plan["payload"]["recording_id"],
                "dataset_id": plan["payload"]["dataset_id"],
                "session_id": plan["payload"]["session_id"],
                "player_uuid": plan["payload"]["player_uuid"],
                "connection_id": plan["payload"]["connection_id"],
                "start_tick": plan["payload"]["start_tick"],
                "end_tick": plan["payload"]["end_tick"],
            },
            "imports": imports,
            "attachment_directories": [item["directory"] for item in imports if item["status"] == "complete"],
        }


def dispatch_render_rpc(config: RecorderConfig, action: str, body: Mapping[str, Any]) -> dict[str, Any]:
    """Dispatch one bounded JSON request from the SSH-only CLI boundary."""

    return RenderRpcService(config).dispatch(action, body)
