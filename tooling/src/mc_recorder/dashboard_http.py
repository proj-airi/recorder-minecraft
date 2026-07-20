from __future__ import annotations

import base64
import hashlib
import hmac
import importlib.resources
import json
import os
import re
import sys
import threading
from dataclasses import asdict
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit

from .config import RecorderConfig
from .dashboard_service import DashboardService
from .dataset_viewer import (
    ArtifactUnavailableError,
    DatasetNotFoundError,
    DatasetValidationError,
    DatasetViewerError,
    SampleNotFoundError,
    VerifiedArtifact,
)
from .errors import RecorderError


MAX_REQUEST_BYTES = 64 * 1024
JOB_PATH = re.compile(r"^/api/v1/jobs/([0-9a-f-]{36})$")
GENERATE_PATH = re.compile(r"^/api/v1/recordings/([0-9a-f]{24})/generate$")
OPAQUE_DATASET_ID = re.compile(r"^[0-9a-f]{32}$")
OPAQUE_SAMPLE_ID = re.compile(r"^[0-9a-f]{32}$")


class DashboardApplication:
    def __init__(self, config: RecorderConfig, username: str, password: str):
        self.config = config
        self.username = username
        self.password = password
        self.static_root = importlib.resources.files("mc_recorder").joinpath("static")
        self.service = DashboardService(config)
        self.dataset_viewer = self.service.dataset_viewer
        self.dataset_index = self.service.dataset_index

    def close(self) -> None:
        self.service.close()


class DashboardHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 64

    def __init__(self, address: tuple[str, int], application: DashboardApplication):
        self.application = application
        self._request_slots = threading.BoundedSemaphore(32)
        super().__init__(address, DashboardHandler)

    def get_request(self):
        request, address = super().get_request()
        request.settimeout(30)
        return request, address

    def process_request(self, request, client_address) -> None:
        if not self._request_slots.acquire(blocking=False):
            request.close()
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self._request_slots.release()
            raise

    def process_request_thread(self, request, client_address) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._request_slots.release()


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "mc-recorder-dashboard/1"
    sys_version = ""

    @property
    def application(self) -> DashboardApplication:
        return self.server.application  # type: ignore[attr-defined,no-any-return]

    def __getattr__(self, name: str):
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
                self._static("dashboard.html", "text/html; charset=utf-8")
                return
            if path == "/app.js":
                self._static("dashboard.js", "text/javascript; charset=utf-8")
                return
            if path == "/styles.css":
                self._static("dashboard.css", "text/css; charset=utf-8")
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
        except (ValueError, KeyError) as exc:
            self._error(HTTPStatus.BAD_REQUEST, str(exc))

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        if not self._authenticate():
            return
        if not self._authorize_mutation():
            return
        path = urlsplit(self.path).path
        try:
            self._read_json_body()
            if path == "/api/v1/server/start":
                job = self.application.service.start_server_job()
            elif path == "/api/v1/server/stop":
                job = self.application.service.stop_server_job()
            elif match := GENERATE_PATH.fullmatch(path):
                job = self.application.service.generate_job(match.group(1))
            else:
                self._error(HTTPStatus.NOT_FOUND, "not found")
                return
            self._json(HTTPStatus.ACCEPTED, job)
        except RecorderError as exc:
            self._error(HTTPStatus.CONFLICT, str(exc))
        except (ValueError, json.JSONDecodeError) as exc:
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
            except (ValueError, UnicodeDecodeError):
                pass
        allowed = hmac.compare_digest(supplied_user, self.application.username) & hmac.compare_digest(
            supplied_password, self.application.password
        )
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
        if not hmac.compare_digest(
            self.headers.get("X-MC-Recorder-CSRF", ""), self.application.service.csrf_token
        ):
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
            metadata["connections"] = [
                asdict(connection) for connection in viewer.list_player_connections(dataset_id)
            ]
            self._json(HTTPStatus.OK, metadata)
            return
        if len(parts) == 5 and parts[4] == "samples":
            validity = self._query_one(query, "validity")
            modality = self._query_one(query, "modality")
            transition_valid = self._query_bool(query, "transition_valid")
            rgb_available = self._query_bool(query, "rgb_available")
            voxel_available = self._query_bool(query, "voxel_available")
            if validity is not None:
                if validity not in {"valid", "invalid"}:
                    raise ValueError("validity must be valid or invalid")
                transition_valid = validity == "valid"
            if modality is not None:
                if modality not in {"rgb", "voxels"}:
                    raise ValueError("modality must be rgb or voxels")
                if modality == "rgb":
                    rgb_available = True
                else:
                    voxel_available = True
            page = viewer.list_sample_summaries(
                dataset_id,
                player_uuid=self._query_one(query, "player_uuid"),
                connection_id=self._query_one(query, "connection_id"),
                from_tick=self._query_optional_int(query, "from_tick"),
                to_tick=self._query_optional_int(query, "to_tick"),
                transition_valid=transition_valid,
                rgb_available=rgb_available,
                voxel_available=voxel_available,
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
            if len(parts) == 7 and parts[6] == "voxel-slice":
                axis = self._query_one(query, "axis") or "y"
                index = self._query_int(query, "index", -1)
                if index < 0:
                    detail = viewer.get_sample_detail(dataset_id, sample_id)
                    index = self._default_voxel_index(detail.record, axis)
                voxel_slice = viewer.get_voxel_slice(
                    dataset_id,
                    sample_id,
                    axis=axis,
                    index=index,
                )
                payload = asdict(voxel_slice)
                rows = payload["cells"]
                payload["height"] = len(rows)
                payload["width"] = len(rows[0]) if rows else 0
                payload["cells"] = [
                    {
                        **cell,
                        "color": self._block_color(cell["block_state"]) if cell["covered"] else None,
                    }
                    for row in rows
                    for cell in row
                ]
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
        if len(data) != artifact.size_bytes or not hmac.compare_digest(
            hashlib.sha256(data).hexdigest(), artifact.sha256
        ):
            raise DatasetValidationError("RGB artifact changed after validation")
        return data

    @staticmethod
    def _default_voxel_index(record: dict[str, Any], axis: str) -> int:
        if axis not in {"x", "y", "z"}:
            raise ValueError("voxel axis must be x, y, or z")
        modalities = record.get("modalities")
        voxels = modalities.get("voxels") if isinstance(modalities, dict) else None
        shape = voxels.get("shape") if isinstance(voxels, dict) else None
        if not isinstance(shape, dict) or set(shape) != {"x", "y", "z"}:
            raise ValueError("voxel modality does not declare a three-axis shape")
        value = shape[axis]
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise ValueError("voxel modality shape is invalid")
        return value // 2

    @staticmethod
    def _block_color(block_state: object) -> list[int]:
        digest = hashlib.blake2s(str(block_state).encode("utf-8"), digest_size=3).digest()
        return [48 + component * 159 // 255 for component in digest]

    def _static(self, name: str, content_type: str) -> None:
        resource = self.application.static_root.joinpath(name)
        try:
            data = resource.read_bytes()
        except (FileNotFoundError, OSError):
            self._error(HTTPStatus.NOT_FOUND, "static asset not found")
            return
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


def serve_dashboard(config: RecorderConfig) -> None:
    username = os.environ.get("MC_RECORDER_DASHBOARD_USERNAME", "")
    password = os.environ.get("MC_RECORDER_DASHBOARD_PASSWORD", "")
    if not username or not password:
        raise RecorderError(
            "MC_RECORDER_DASHBOARD_USERNAME and MC_RECORDER_DASHBOARD_PASSWORD are required"
        )
    if ":" in username:
        raise RecorderError(
            "MC_RECORDER_DASHBOARD_USERNAME may not contain ':' because Basic auth uses it as the user-id delimiter"
        )
    application = DashboardApplication(config, username, password)
    try:
        server = DashboardHTTPServer((config.dashboard.bind, config.dashboard.port), application)
    except OSError as exc:
        application.close()
        raise RecorderError(
            f"cannot bind dashboard to {config.dashboard.bind}:{config.dashboard.port}: {exc}"
        ) from exc
    address, port = server.server_address[:2]
    print(f"mc-recorder dashboard listening on http://{address}:{port}", file=sys.stderr)
    try:
        server.serve_forever(poll_interval=0.25)
    finally:
        server.server_close()
        application.close()
