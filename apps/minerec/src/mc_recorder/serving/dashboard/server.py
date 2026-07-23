from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import mimetypes
import re
import socket
import sys
import threading
from collections.abc import Mapping
from dataclasses import asdict, fields, is_dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, cast
from urllib.parse import parse_qs, urlsplit

from mc_recorder.config import ENV_DASHBOARD_PASSWORD, ENV_DASHBOARD_USERNAME, RecorderConfig, load_runtime_env
from mc_recorder.errors import RecorderError
from mc_recorder.processing.dataset.viewer import (
    ArtifactUnavailableError,
    DatasetNotFoundError,
    DatasetValidationError,
    DatasetViewerError,
    SampleNotFoundError,
    VerifiedArtifact,
)
from mc_recorder.serving.dashboard.service import DashboardService

MAX_REQUEST_BYTES = 64 * 1024
MAX_JSON_RESPONSE_BYTES = 64 * 1024 * 1024
JOB_PATH = re.compile(r"^/api/v1/jobs/([0-9a-f-]{36})$")
GENERATE_PATH = re.compile(r"^/api/v1/recordings/([0-9a-f]{24})/generate$")
RENDER_PATH = re.compile(r"^/api/v1/recordings/([0-9a-f]{24})/render$")
ROUTE_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
RENDER_JOB_PATH = re.compile(rf"^/api/v1/render-jobs/({ROUTE_UUID})$")
RENDER_JOB_ACTION_PATH = re.compile(rf"^/api/v1/render-jobs/({ROUTE_UUID})/(cancel|retry)$")
OPAQUE_DATASET_ID = re.compile(r"^[0-9a-f]{32}$")
OPAQUE_SAMPLE_ID = re.compile(r"^[0-9a-f]{32}$")


def _dashboard_static_root() -> Path:
    override = load_runtime_env().dashboard.static_root
    if override is not None:
        return override

    repository_root = Path(__file__).resolve().parents[6]
    return repository_root / "apps" / "dashboard" / "dist"


class DashboardApplication:
    def __init__(self, config: RecorderConfig, username: str, password: str) -> None:
        self.config = config
        self.username = username
        self.password = password
        self.static_root = _dashboard_static_root()
        self.service = DashboardService(config)
        self.dataset_viewer = self.service.dataset_viewer
        self.dataset_index = self.service.dataset_index

    def close(self) -> None:
        self.service.close()


class DashboardHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 64

    def __init__(self, address: tuple[str, int], application: DashboardApplication) -> None:
        self.application = application
        self._request_slots = threading.BoundedSemaphore(32)
        super().__init__(address, DashboardHandler)

    def get_request(self) -> tuple[socket.socket, tuple[str, int]]:
        request, address = super().get_request()
        request.settimeout(30)
        return request, address

    def process_request(self, request: Any, client_address: Any) -> None:  # noqa: ANN401
        if not self._request_slots.acquire(blocking=False):
            request.close()
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self._request_slots.release()
            raise

    def process_request_thread(self, request: Any, client_address: Any) -> None:  # noqa: ANN401
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._request_slots.release()


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "mc-recorder-dashboard/1"
    sys_version = ""

    @property
    def application(self) -> DashboardApplication:
        return cast(DashboardHTTPServer, self.server).application

    def __getattr__(self, name: str) -> object:
        # BaseHTTPRequestHandler otherwise emits its own unauthenticated 501
        # response for an unknown method. Route every syntactically valid HTTP
        # method through the same authentication and hardened response path.
        if name.startswith("do_"):
            return self._unsupported_method
        raise AttributeError(name)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        if not self._authenticate():
            return
        route = urlsplit(self.path)
        path = route.path
        try:
            if path == "/api/v1/status":
                self._json(HTTPStatus.OK, self.application.service.status())
                return
            if path == "/api/v1/recordings":
                self._json(HTTPStatus.OK, {"recordings": self.application.service.recordings()})
                return
            if path == "/api/v1/render-jobs":
                self._json(
                    HTTPStatus.OK,
                    {"jobs": self.application.service.render_jobs()},
                )
                return
            if match := RENDER_JOB_PATH.fullmatch(path):
                try:
                    job = self.application.service.render_queue.get(match.group(1))
                except KeyError:
                    self._error(HTTPStatus.NOT_FOUND, "render job not found")
                else:
                    self._json(HTTPStatus.OK, job)
                return
            if path == "/api/v1/render-workers":
                self._json(
                    HTTPStatus.OK,
                    {"workers": self.application.service.render_workers()},
                )
                return
            if match := JOB_PATH.fullmatch(path):
                try:
                    job = self.application.service.jobs.store.get(match.group(1))
                except KeyError:
                    self._error(HTTPStatus.NOT_FOUND, "job not found")
                else:
                    self._json(HTTPStatus.OK, job)
                return
            if path == "/api/v1/datasets":
                self._datasets()
                return
            if path.startswith("/api/v1/datasets/"):
                self._dataset_route(path, parse_qs(route.query, max_num_fields=32))
                return
            if path in {"/", "/index.html"}:
                self._static("index.html")
                return
            if not path.startswith("/api/"):
                self._static(path.removeprefix("/"))
                return
            self._error(HTTPStatus.NOT_FOUND, "not found")
        except (DatasetNotFoundError, SampleNotFoundError, ArtifactUnavailableError) as exc:
            self._error(HTTPStatus.NOT_FOUND, str(exc))
        except DatasetValidationError as exc:
            self._error(HTTPStatus.CONFLICT, str(exc))
        except DatasetViewerError as exc:
            self._error(HTTPStatus.BAD_REQUEST, str(exc))
        except RecorderError as exc:
            self._error(HTTPStatus.CONFLICT, str(exc))
        except (ValueError, KeyError, OverflowError) as exc:
            self._error(HTTPStatus.BAD_REQUEST, str(exc))

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        if not self._authenticate():
            return
        if not self._authorize_mutation():
            return
        path = urlsplit(self.path).path
        try:
            body = self._read_json_body()
            if path == "/api/v1/server/start":
                job = self.application.service.start_server_job()
            elif path == "/api/v1/server/stop":
                job = self.application.service.stop_server_job()
            elif match := GENERATE_PATH.fullmatch(path):
                job = self.application.service.generate_job(match.group(1))
            elif match := RENDER_PATH.fullmatch(path):
                unexpected = set(body) - {
                    "width",
                    "height",
                    "fps",
                    "no_gui",
                    "replace_legacy_rgb",
                }
                if unexpected:
                    raise ValueError(f"unsupported render setting: {sorted(unexpected)[0]}")
                for setting in ("width", "height", "fps"):
                    if setting in body and (not isinstance(body[setting], int) or isinstance(body[setting], bool)):
                        raise ValueError(f"render {setting} must be an integer")
                if "no_gui" in body and not isinstance(body["no_gui"], bool):
                    raise ValueError("render no_gui must be a boolean")
                if "replace_legacy_rgb" in body and not isinstance(body["replace_legacy_rgb"], bool):
                    raise ValueError("replace_legacy_rgb must be a boolean")
                job = self.application.service.create_render_job(
                    match.group(1),
                    width=body.get("width", 640),
                    height=body.get("height", 360),
                    fps=body.get("fps", 20),
                    no_gui=body.get("no_gui", False),
                    replace_legacy_rgb=body.get("replace_legacy_rgb", False),
                )
            elif match := RENDER_JOB_ACTION_PATH.fullmatch(path):
                if body:
                    raise ValueError("render job actions do not accept request fields")
                job_id, action = match.groups()
                try:
                    job = self.application.service.cancel_render_job(job_id) if action == "cancel" else self.application.service.retry_render_job(job_id)
                except KeyError:
                    self._error(HTTPStatus.NOT_FOUND, "render job not found")
                    return
            else:
                self._error(HTTPStatus.NOT_FOUND, "not found")
                return
            self._json(HTTPStatus.ACCEPTED, job)
        except RecorderError as exc:
            self._error(HTTPStatus.CONFLICT, str(exc))
        except (ValueError, json.JSONDecodeError, RecursionError) as exc:
            self._error(HTTPStatus.BAD_REQUEST, str(exc))

    def do_OPTIONS(self) -> None:  # noqa: N802 - explicitly no cross-origin API
        if not self._authenticate():
            return
        self._error(HTTPStatus.METHOD_NOT_ALLOWED, "CORS is not enabled")

    def do_HEAD(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        if not self._authenticate():
            return
        self.send_response(HTTPStatus.METHOD_NOT_ALLOWED)
        self._common_headers("application/json; charset=utf-8", 0)
        self.end_headers()

    def do_PUT(self) -> None:  # noqa: N802 - reject authenticated unsupported mutations
        self._unsupported_method()

    def do_PATCH(self) -> None:  # noqa: N802 - reject authenticated unsupported mutations
        self._unsupported_method()

    def do_DELETE(self) -> None:  # noqa: N802 - reject authenticated unsupported mutations
        self._unsupported_method()

    def do_TRACE(self) -> None:  # noqa: N802 - reject authenticated unsupported methods
        self._unsupported_method()

    def do_CONNECT(self) -> None:  # noqa: N802 - reject authenticated unsupported methods
        self._unsupported_method()

    def _unsupported_method(self) -> None:
        if not self._authenticate():
            return
        self._error(HTTPStatus.METHOD_NOT_ALLOWED, "method not allowed")

    def _authenticate(self) -> bool:
        authorization = self.headers.get("Authorization", "")
        supplied_user = supplied_password = ""
        if authorization.startswith("Basic "):
            try:
                decoded = base64.b64decode(authorization[6:], validate=True).decode("utf-8")
                supplied_user, supplied_password = decoded.split(":", 1)
            except ValueError, UnicodeDecodeError:
                pass
        allowed = hmac.compare_digest(supplied_user, self.application.username) & hmac.compare_digest(supplied_password, self.application.password)
        if allowed:
            return True
        body = b'{"error":"authentication required"}\n'
        self.send_response(HTTPStatus.UNAUTHORIZED)
        self.send_header("WWW-Authenticate", 'Basic realm="mc-recorder", charset="UTF-8"')
        self._common_headers("application/json; charset=utf-8", len(body))
        self.end_headers()
        self.wfile.write(body)
        return False

    def _authorize_mutation(self) -> bool:
        if not hmac.compare_digest(self.headers.get("X-MC-Recorder-CSRF", ""), self.application.service.csrf_token):
            self._error(HTTPStatus.FORBIDDEN, "missing or invalid CSRF token")
            return False
        origin = self.headers.get("Origin")
        host = self.headers.get("Host")
        allowed_origins = {f"http://{host}", f"https://{host}"} if host else set()
        if not origin or origin.rstrip("/") not in allowed_origins:
            self._error(HTTPStatus.FORBIDDEN, "request origin does not match Host")
            return False
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            self._error(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "mutations require application/json")
            return False
        return True

    def _read_json_body(self) -> dict[str, Any]:
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            raise ValueError("Content-Length is required")
        try:
            length = int(raw_length)
        except ValueError as exc:
            raise ValueError("invalid Content-Length") from exc
        if not 0 <= length <= MAX_REQUEST_BYTES:
            raise ValueError("request body is too large")
        value = json.loads(self.rfile.read(length) or b"{}")
        if not isinstance(value, dict):
            raise ValueError("request body must be a JSON object")
        return value

    def _datasets(self) -> None:
        catalog, indexing, error, refreshed_at = self.application.dataset_index.snapshot()
        self.application.dataset_index.request_refresh()
        datasets = []
        for summary in catalog.datasets:
            item = asdict(summary)
            item["id"] = item["dataset_id"]
            item["status"] = "verified"
            datasets.append(item)
        self._json(
            HTTPStatus.OK,
            {
                "datasets": datasets,
                "rejected": [asdict(issue) for issue in catalog.rejected],
                "indexing": indexing,
                "error": error,
                "refreshed_at_unix": refreshed_at,
            },
        )

    def _dataset_route(self, path: str, query: dict[str, list[str]]) -> None:
        viewer = self.application.dataset_viewer
        parts = path.strip("/").split("/")
        if len(parts) < 4:
            self._error(HTTPStatus.NOT_FOUND, "not found")
            return
        dataset_id = parts[3]
        if OPAQUE_DATASET_ID.fullmatch(dataset_id) is None:
            self._error(HTTPStatus.NOT_FOUND, "dataset not found")
            return
        if len(parts) == 4:
            metadata = asdict(viewer.get_dataset_metadata(dataset_id))
            metadata["id"] = metadata["dataset_id"]
            metadata["connections"] = [asdict(connection) for connection in viewer.list_player_connections(dataset_id)]
            self._json(HTTPStatus.OK, metadata)
            return
        if len(parts) == 5 and parts[4] == "trajectory":
            validity = self._query_one(query, "validity")
            modality = self._query_one(query, "modality")
            transition_valid = self._query_bool(query, "transition_valid")
            rgb_available = self._query_bool(query, "rgb_available")
            scene_available = self._query_bool(query, "scene_available")
            if validity is not None:
                if validity not in {"valid", "invalid"}:
                    raise ValueError("validity must be valid or invalid")
                transition_valid = validity == "valid"
            if modality is not None:
                if modality not in {"rgb", "scene"}:
                    raise ValueError("modality must be rgb or scene")
                if modality == "rgb":
                    rgb_available = True
                else:
                    scene_available = True
            trajectory = viewer.get_trajectory(
                dataset_id,
                player_uuid=self._query_one(query, "player_uuid"),
                connection_id=self._query_one(query, "connection_id"),
                from_tick=self._query_optional_int(query, "from_tick"),
                to_tick=self._query_optional_int(query, "to_tick"),
                transition_valid=transition_valid,
                rgb_available=rgb_available,
                scene_available=scene_available,
                max_points=self._query_int(query, "max_points", 2_400),
            )
            self._json(HTTPStatus.OK, asdict(trajectory))
            return
        if len(parts) == 5 and parts[4] == "samples":
            validity = self._query_one(query, "validity")
            modality = self._query_one(query, "modality")
            transition_valid = self._query_bool(query, "transition_valid")
            rgb_available = self._query_bool(query, "rgb_available")
            scene_available = self._query_bool(query, "scene_available")
            if validity is not None:
                if validity not in {"valid", "invalid"}:
                    raise ValueError("validity must be valid or invalid")
                transition_valid = validity == "valid"
            if modality is not None:
                if modality not in {"rgb", "scene"}:
                    raise ValueError("modality must be rgb or scene")
                if modality == "rgb":
                    rgb_available = True
                else:
                    scene_available = True
            page = viewer.list_sample_summaries(
                dataset_id,
                player_uuid=self._query_one(query, "player_uuid"),
                connection_id=self._query_one(query, "connection_id"),
                from_tick=self._query_optional_int(query, "from_tick"),
                to_tick=self._query_optional_int(query, "to_tick"),
                transition_valid=transition_valid,
                rgb_available=rgb_available,
                scene_available=scene_available,
                cursor=self._query_one(query, "cursor"),
                limit=self._query_int(query, "limit", 100),
            )
            self._json(
                HTTPStatus.OK,
                {
                    "samples": [asdict(item) for item in page.items],
                    "total": page.total,
                    "next_cursor": page.next_cursor,
                },
            )
            return
        if len(parts) >= 6 and parts[4] == "samples":
            sample_id = parts[5]
            if OPAQUE_SAMPLE_ID.fullmatch(sample_id) is None:
                self._error(HTTPStatus.NOT_FOUND, "sample not found")
                return
            if len(parts) == 6:
                detail = viewer.get_sample_detail(dataset_id, sample_id)
                self._json(HTTPStatus.OK, asdict(detail))
                return
            if len(parts) == 7 and parts[6] == "frame":
                artifact = viewer.resolve_rgb_artifact(dataset_id, sample_id)
                data = self._read_verified_artifact(artifact)
                self._bytes(HTTPStatus.OK, data, artifact.media_type, etag=artifact.sha256)
                return
            if len(parts) == 7 and parts[6] == "scene-slice":
                axis = self._query_one(query, "axis") or "y"
                scene_slice = viewer.get_scene_slice(
                    dataset_id,
                    sample_id,
                    axis=axis,
                    coordinate=self._query_optional_int(query, "coordinate"),
                    radius=self._query_int(query, "radius", 32),
                )
                from mc_recorder.processing.scene.store import scene_slice_to_json

                payload = self._scene_slice_payload(scene_slice_to_json(scene_slice))
                payload.update(viewer.get_scene_policy(dataset_id))
                self._json(HTTPStatus.OK, payload)
                return
        self._error(HTTPStatus.NOT_FOUND, "not found")

    @staticmethod
    def _read_verified_artifact(artifact: VerifiedArtifact) -> bytes:
        try:
            with artifact.path.open("rb") as handle:
                data = handle.read(artifact.size_bytes + 1)
        except OSError as exc:
            raise DatasetValidationError("RGB artifact became unavailable after validation") from exc
        if len(data) != artifact.size_bytes or not hmac.compare_digest(hashlib.sha256(data).hexdigest(), artifact.sha256):
            raise DatasetValidationError("RGB artifact changed after validation")
        return data

    @staticmethod
    def _block_color(block_state: object) -> list[int]:
        canonical = json.dumps(
            block_state,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        digest = hashlib.blake2s(canonical.encode("utf-8"), digest_size=3).digest()
        return [48 + component * 159 // 255 for component in digest]

    @classmethod
    def _scene_slice_payload(cls, scene_slice: object) -> dict[str, Any]:
        payload = _json_value(scene_slice)
        if not isinstance(payload, dict):
            raise DatasetValidationError("scene slice result must be an object")
        width = payload.get("width")
        height = payload.get("height")
        palette = payload.get("palette")
        cells = payload.get("cells")
        if (
            not isinstance(width, int)
            or isinstance(width, bool)
            or width <= 0
            or not isinstance(height, int)
            or isinstance(height, bool)
            or height <= 0
            or not isinstance(palette, list)
            or not isinstance(cells, list)
            or len(cells) != width * height
        ):
            raise DatasetValidationError("scene slice dimensions do not match its cells")
        payload = cast(dict[str, Any], payload)
        palette_colors = [None if block_state is None else cls._block_color(block_state) for block_state in palette]
        colored_cells: list[dict[str, Any]] = []
        for cell in cells:
            if not isinstance(cell, dict) or not isinstance(cell.get("covered"), bool):
                raise DatasetValidationError("scene slice contains an invalid cell")
            cell = cast(dict[str, Any], cell)
            world_position = cell.get("world_position")
            if not isinstance(world_position, list) or len(world_position) != 3 or any(isinstance(coordinate, bool) or not isinstance(coordinate, int) for coordinate in world_position):
                raise DatasetValidationError("scene slice cell world_position must contain three integers")
            palette_index = cell.get("palette_index")
            if cell["covered"]:
                if isinstance(palette_index, bool) or not isinstance(palette_index, int) or not 0 <= palette_index < len(palette):
                    raise DatasetValidationError("covered scene slice cell references an invalid palette entry")
            elif palette_index is not None:
                raise DatasetValidationError("uncovered scene slice cell must not reference the palette")
            colored_cells.append(
                {
                    "world_position": world_position,
                    "covered": cell["covered"],
                    "palette_index": palette_index,
                    "color": None if palette_index is None else palette_colors[palette_index],
                }
            )
        payload["cells"] = colored_cells
        row_axis = payload.get("row_axis")
        column_axis = payload.get("column_axis")
        if row_axis not in {"x", "y", "z"} or column_axis not in {"x", "y", "z"}:
            raise DatasetValidationError("scene slice projection axes are invalid")
        row_axis = cast(str, row_axis)
        column_axis = cast(str, column_axis)
        for key in ("entities", "block_entities"):
            values = payload.get(key)
            if not isinstance(values, list):
                raise DatasetValidationError(f"scene slice {key} must be an array")
            payload[key] = [
                cls._project_scene_summary(
                    value,
                    row_axis,
                    column_axis,
                    block_entity=key == "block_entities",
                )
                for value in values
            ]
        return payload

    @staticmethod
    def _project_scene_summary(
        value: object,
        row_axis: str,
        column_axis: str,
        *,
        block_entity: bool,
    ) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise DatasetValidationError("scene slice object summary must be an object")
        value = cast(dict[str, Any], value)
        position = _spatial_position(value.get("position"))
        if position is None:
            position = _spatial_position(value.get("world_position"))
        if position is None:
            position = _spatial_position(value)
        projection: dict[str, float] | None = None
        if position is not None:
            projection = {
                "row": position[{"x": 0, "y": 1, "z": 2}[row_axis]],
                "column": position[{"x": 0, "y": 1, "z": 2}[column_axis]],
            }
            bounds = _spatial_bounds(value.get("aabb", value.get("bounds", value.get("bounding_box"))))
            if bounds is not None:
                projection.update(
                    {
                        "min_row": bounds[f"min_{row_axis}"],
                        "max_row": bounds[f"max_{row_axis}"],
                        "min_column": bounds[f"min_{column_axis}"],
                        "max_column": bounds[f"max_{column_axis}"],
                    }
                )
        fields_to_keep = (
            ("dimension", "type_id", "position")
            if block_entity
            else (
                "instance_id",
                "dimension",
                "type_id",
                "network_id",
                "uuid",
                "position",
                "aabb",
            )
        )
        summary = {key: value[key] for key in fields_to_keep if key in value}
        custom_name = _scene_custom_name(value.get("payload"))
        if custom_name is not None:
            summary["custom_name"] = custom_name
        summary["projection"] = projection
        return summary

    def _static(self, name: str) -> None:
        root = self.application.static_root.resolve()
        resource = (root / name).resolve()
        if root not in {resource, *resource.parents} or resource.is_dir():
            self._error(HTTPStatus.NOT_FOUND, "static asset not found")
            return
        try:
            data = resource.read_bytes()
        except FileNotFoundError, OSError:
            self._error(
                HTTPStatus.NOT_FOUND,
                "dashboard static asset not found; run `pnpm build:dashboard` first",
            )
            return
        content_type = mimetypes.guess_type(resource.name)[0] or "application/octet-stream"
        if content_type == "text/html":
            content_type = "text/html; charset=utf-8"
        elif content_type in {"text/css", "text/javascript"}:
            content_type = f"{content_type}; charset=utf-8"
        self._bytes(HTTPStatus.OK, data, content_type)

    @staticmethod
    def _query_one(query: dict[str, list[str]], key: str) -> str | None:
        values = query.get(key)
        return values[0] if values else None

    @staticmethod
    def _query_int(query: dict[str, list[str]], key: str, default: int) -> int:
        value = DashboardHandler._query_one(query, key)
        return default if value is None else int(value)

    @staticmethod
    def _query_optional_int(query: dict[str, list[str]], key: str) -> int | None:
        value = DashboardHandler._query_one(query, key)
        return None if value is None else int(value)

    @staticmethod
    def _query_bool(query: dict[str, list[str]], key: str) -> bool | None:
        value = DashboardHandler._query_one(query, key)
        if value is None:
            return None
        if value not in {"true", "false"}:
            raise ValueError(f"{key} must be true or false")
        return value == "true"

    def _json(self, status: HTTPStatus, value: object) -> None:
        data = (json.dumps(value, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")
        if len(data) > MAX_JSON_RESPONSE_BYTES:
            raise DatasetValidationError(f"serialized JSON response exceeds {MAX_JSON_RESPONSE_BYTES} bytes")
        self._bytes(status, data, "application/json; charset=utf-8")

    def _error(self, status: HTTPStatus, message: str) -> None:
        self._json(status, {"error": message[:2048]})

    def _bytes(
        self,
        status: HTTPStatus,
        data: bytes,
        content_type: str,
        *,
        etag: str | None = None,
    ) -> None:
        self.send_response(status)
        if etag:
            self.send_header("ETag", f'"{etag}"')
        self._common_headers(content_type, len(data))
        self.end_headers()
        self.wfile.write(data)

    def _common_headers(self, content_type: str, length: int) -> None:
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; object-src 'none'; frame-ancestors 'none'",
        )


def _json_value(value: object) -> Any:  # noqa: ANN401
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _json_value(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    raise DatasetValidationError(f"scene slice contains a non-JSON value: {type(value).__name__}")


def _spatial_position(value: object) -> tuple[float, float, float] | None:
    coordinates: list[Any]
    if isinstance(value, Mapping):
        coordinates = [value.get(axis) for axis in ("x", "y", "z")]
    elif isinstance(value, (tuple, list)) and len(value) == 3:
        coordinates = list(value)
    else:
        return None
    if not all(isinstance(item, (int, float)) and not isinstance(item, bool) and math.isfinite(float(item)) for item in coordinates):
        return None
    return float(coordinates[0]), float(coordinates[1]), float(coordinates[2])


def _spatial_bounds(value: object) -> dict[str, float] | None:
    raw: dict[str, object]
    if isinstance(value, Mapping):
        if all(f"{edge}_{axis}" in value for edge in ("min", "max") for axis in ("x", "y", "z")):
            mapping = cast(Mapping[str, Any], value)
            raw = {f"{edge}_{axis}": mapping[f"{edge}_{axis}"] for edge in ("min", "max") for axis in ("x", "y", "z")}
        else:
            minimum = _spatial_position(value.get("min"))
            maximum = _spatial_position(value.get("max"))
            if minimum is None or maximum is None:
                return None
            raw = {
                **{f"min_{axis}": minimum[index] for index, axis in enumerate(("x", "y", "z"))},
                **{f"max_{axis}": maximum[index] for index, axis in enumerate(("x", "y", "z"))},
            }
    elif isinstance(value, (tuple, list)) and len(value) == 6:
        raw = dict(
            zip(
                ("min_x", "min_y", "min_z", "max_x", "max_y", "max_z"),
                value,
                strict=True,
            )
        )
    else:
        return None
    if not all(isinstance(item, (int, float)) and not isinstance(item, bool) and math.isfinite(float(item)) for item in raw.values()):
        return None
    return {key: float(item) for key, item in raw.items()}  # ty:ignore[invalid-argument-type]


def _scene_custom_name(value: object) -> str | None:
    if not isinstance(value, Mapping):
        return None
    for key in ("custom_name", "display_name", "name"):
        candidate = value.get(key)
        if isinstance(candidate, str) and candidate:
            return candidate[:256]
    metadata = value.get("metadata")
    if isinstance(metadata, Mapping):
        for key in ("custom_name", "display_name", "name"):
            candidate = metadata.get(key)
            if isinstance(candidate, str) and candidate:
                return candidate[:256]
    return None


def serve_dashboard(config: RecorderConfig) -> None:
    env = load_runtime_env().dashboard
    if not env.username or not env.password:
        raise RecorderError(f"{ENV_DASHBOARD_USERNAME} and {ENV_DASHBOARD_PASSWORD} are required")
    if ":" in env.username:
        raise RecorderError(f"{ENV_DASHBOARD_USERNAME} may not contain ':' because Basic auth uses it as the user-id delimiter")
    application = DashboardApplication(config, env.username, env.password)
    try:
        server = DashboardHTTPServer((config.dashboard.bind, config.dashboard.port), application)
    except OSError as exc:
        application.close()
        raise RecorderError(f"cannot bind dashboard to {config.dashboard.bind}:{config.dashboard.port}: {exc}") from exc
    address, port = server.server_address[:2]
    print(f"minerec dashboard listening on http://{address}:{port}", file=sys.stderr)
    try:
        server.serve_forever(poll_interval=0.25)
    finally:
        server.server_close()
        application.close()
