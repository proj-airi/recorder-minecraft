from __future__ import annotations

import hashlib
import hmac
import json
import mimetypes
import os
import re
import secrets
import socket
import sys
import tempfile
import threading
import webbrowser
from collections.abc import Iterable
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, cast
from urllib.parse import parse_qs, urlencode, urlsplit

from minerec.errors import RecorderError

from .service import ViewerService

MAX_IMPORT_BYTES = 64 * 1024**3
MAX_JSON_RESPONSE_BYTES = 16 * 1024 * 1024
MAX_RANGE_PARTS = 8
TICK_PATH = re.compile(r"^/api/v1/viewer/ticks/(-?[0-9]+)$")


def _viewer_static_root() -> Path:
    packaged = Path(__file__).resolve().parents[2] / "viewer_dist"
    if packaged.is_dir():
        return packaged
    repository_root = Path(__file__).resolve().parents[6]
    return repository_root / "apps" / "viewer" / "dist"


def _single_int(
    query: dict[str, list[str]],
    name: str,
    *,
    required: bool = False,
) -> int | None:
    values = query.get(name)
    if not values:
        if required:
            raise RecorderError(f"query parameter {name} is required")
        return None
    if len(values) != 1:
        raise RecorderError(f"query parameter {name} must occur once")
    try:
        return int(values[0])
    except ValueError as exc:
        raise RecorderError(f"query parameter {name} must be an integer") from exc


def _single_text(query: dict[str, list[str]], name: str) -> str | None:
    values = query.get(name)
    if not values:
        return None
    if len(values) != 1 or not values[0]:
        raise RecorderError(f"query parameter {name} must be one non-empty value")
    return values[0]


def _byte_ranges(header: str, size: int) -> list[tuple[int, int]]:
    if not header.startswith("bytes="):
        raise RecorderError("only bytes ranges are supported")
    parts = header[6:].split(",")
    if not 1 <= len(parts) <= MAX_RANGE_PARTS:
        raise RecorderError(f"range request must contain 1 to {MAX_RANGE_PARTS} parts")
    ranges: list[tuple[int, int]] = []
    for raw in parts:
        value = raw.strip()
        if "-" not in value:
            raise RecorderError("invalid byte range")
        start_text, end_text = value.split("-", 1)
        if not start_text:
            try:
                suffix = int(end_text)
            except ValueError as exc:
                raise RecorderError("invalid suffix byte range") from exc
            if suffix <= 0:
                raise RecorderError("suffix byte range must be positive")
            start = max(0, size - suffix)
            end = size - 1
        else:
            try:
                start = int(start_text)
                end = size - 1 if not end_text else int(end_text)
            except ValueError as exc:
                raise RecorderError("invalid byte range") from exc
            if start < 0 or end < start:
                raise RecorderError("invalid byte range bounds")
            end = min(end, size - 1)
        if size == 0 or start >= size:
            raise RecorderError("byte range is outside media")
        ranges.append((start, end))
    return ranges


class ViewerApplication:
    def __init__(self, token: str, *, static_root: Path | None = None) -> None:
        self.token = token
        self.static_root = (static_root or _viewer_static_root()).resolve()
        index = self.static_root / "index.html"
        if self.static_root.is_symlink() or not self.static_root.is_dir() or index.is_symlink() or not index.is_file():
            raise RecorderError("viewer frontend is not built; run 'pixi run build-viewer' first")
        self.service = ViewerService()
        self.origin = ""
        self.host = ""
        self._uploads = tempfile.TemporaryDirectory(prefix="minerec-viewer-upload-")
        self._import_lock = threading.Lock()

    def configure_address(self, host: str, port: int) -> None:
        self.host = f"{host}:{port}"
        self.origin = f"http://{self.host}"

    def close(self) -> None:
        self.service.close()
        self._uploads.cleanup()

    def import_stream(self, source: Any, *, content_length: int) -> dict[str, Any]:  # noqa: ANN401
        if not self._import_lock.acquire(blocking=False):
            raise RecorderError("another bundle import is already running")
        destination = Path(self._uploads.name) / f"upload-{secrets.token_hex(16)}.mcplay.zip"
        try:
            remaining = content_length
            digest = hashlib.sha256()
            with destination.open("xb") as output:
                while remaining:
                    chunk = source.read(min(1024 * 1024, remaining))
                    if not chunk:
                        raise RecorderError("bundle upload ended before Content-Length bytes arrived")
                    output.write(chunk)
                    digest.update(chunk)
                    remaining -= len(chunk)
                output.flush()
                os.fsync(output.fileno())
            summary = self.service.import_bundle(destination)
            return {**summary, "archive_sha256": digest.hexdigest()}
        finally:
            destination.unlink(missing_ok=True)
            self._import_lock.release()


class ViewerHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 16

    def __init__(self, address: tuple[str, int], application: ViewerApplication) -> None:
        self.application = application
        self._request_slots = threading.BoundedSemaphore(8)
        super().__init__(address, ViewerHandler)
        host, port = cast(tuple[str, int], self.server_address)
        application.configure_address(host, port)

    def get_request(self) -> tuple[socket.socket, tuple[str, int]]:
        request, address = super().get_request()
        request.settimeout(60)
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


class ViewerHandler(BaseHTTPRequestHandler):
    server_version = "minerec-viewer/1"
    sys_version = ""

    @property
    def application(self) -> ViewerApplication:
        return cast(ViewerHTTPServer, self.server).application

    def __getattr__(self, name: str) -> object:
        if name.startswith("do_"):
            return self._unsupported_method
        raise AttributeError(name)

    def do_GET(self) -> None:  # noqa: N802
        self._get_or_head(send_body=True)

    def do_HEAD(self) -> None:  # noqa: N802
        self._get_or_head(send_body=False)

    def do_POST(self) -> None:  # noqa: N802
        if not self._request_allowed(api=True, mutation=True):
            return
        route = urlsplit(self.path)
        if route.path != "/api/v1/viewer/import":
            self._error(HTTPStatus.NOT_FOUND, "not found")
            return
        transfer_encoding = self.headers.get("Transfer-Encoding")
        if transfer_encoding:
            self._error(HTTPStatus.BAD_REQUEST, "chunked bundle uploads are not supported")
            return
        try:
            raw_length = self.headers.get("Content-Length")
            if raw_length is None:
                raise RecorderError("Content-Length is required")
            content_length = int(raw_length)
            if not 1 <= content_length <= MAX_IMPORT_BYTES:
                raise RecorderError(f"bundle upload must be between 1 and {MAX_IMPORT_BYTES} bytes")
            result = self.application.import_stream(self.rfile, content_length=content_length)
            self._json(HTTPStatus.CREATED, result)
        except (OSError, ValueError, RecorderError) as exc:
            self._error(HTTPStatus.BAD_REQUEST, str(exc))

    def do_OPTIONS(self) -> None:  # noqa: N802
        if not self._request_allowed(api=True, mutation=False):
            return
        self._error(HTTPStatus.METHOD_NOT_ALLOWED, "CORS is not enabled")

    def _unsupported_method(self) -> None:
        if not self._request_allowed(api=self.path.startswith("/api/"), mutation=True):
            return
        self._error(HTTPStatus.METHOD_NOT_ALLOWED, "method not allowed")

    def _get_or_head(self, *, send_body: bool) -> None:
        route = urlsplit(self.path)
        path = route.path
        is_api = path.startswith("/api/")
        query = parse_qs(route.query, keep_blank_values=True, max_num_fields=32)
        media = path == "/api/v1/viewer/render/fpv"
        if not self._request_allowed(api=is_api, mutation=False, query_token=media, query=query):
            return
        try:
            if path == "/api/v1/viewer/bundle":
                self._json(HTTPStatus.OK, self.application.service.summary(), send_body=send_body)
                return
            if match := TICK_PATH.fullmatch(path):
                self._json(
                    HTTPStatus.OK,
                    self.application.service.tick(int(match.group(1))),
                    send_body=send_body,
                )
                return
            if path == "/api/v1/viewer/actions":
                self._json(
                    HTTPStatus.OK,
                    self.application.service.actions(
                        from_tick=_single_int(query, "from_tick"),
                        to_tick=_single_int(query, "to_tick"),
                        limit=_single_int(query, "limit"),
                    ),
                    send_body=send_body,
                )
                return
            if path == "/api/v1/viewer/trajectory":
                self._json(
                    HTTPStatus.OK,
                    self.application.service.trajectory(max_points=_single_int(query, "max_points")),
                    send_body=send_body,
                )
                return
            if path == "/api/v1/viewer/scene/slice":
                self._json(
                    HTTPStatus.OK,
                    self.application.service.scene_slice(
                        tick=cast(int, _single_int(query, "tick", required=True)),
                        dimension=_single_text(query, "dimension"),
                        y=_single_int(query, "y"),
                        radius=_single_int(query, "radius"),
                    ),
                    send_body=send_body,
                )
                return
            if path == "/api/v1/viewer/replays":
                self._json(HTTPStatus.OK, self.application.service.replays(), send_body=send_body)
                return
            if path == "/api/v1/viewer/render/fpv/timeline":
                self._json(
                    HTTPStatus.OK,
                    self.application.service.timeline(
                        from_frame=_single_int(query, "from_frame"),
                        limit=_single_int(query, "limit"),
                    ),
                    send_body=send_body,
                )
                return
            if media:
                self._media(self.application.service.render_path(), send_body=send_body)
                return
            if is_api:
                self._error(HTTPStatus.NOT_FOUND, "not found")
                return
            self._static(path, send_body=send_body)
        except FileNotFoundError:
            self._error(HTTPStatus.NOT_FOUND, "not found")
        except (OSError, ValueError, KeyError, RecorderError) as exc:
            self._error(HTTPStatus.BAD_REQUEST, str(exc))

    def _request_allowed(
        self,
        *,
        api: bool,
        mutation: bool,
        query_token: bool = False,
        query: dict[str, list[str]] | None = None,
    ) -> bool:
        if not hmac.compare_digest(self.headers.get("Host", ""), self.application.host):
            self._error(HTTPStatus.MISDIRECTED_REQUEST, "invalid Host header")
            return False
        origin = self.headers.get("Origin")
        if origin is not None and not hmac.compare_digest(origin, self.application.origin):
            self._error(HTTPStatus.FORBIDDEN, "invalid Origin header")
            return False
        if mutation and not hmac.compare_digest(origin or "", self.application.origin):
            self._error(HTTPStatus.FORBIDDEN, "same-origin request required")
            return False
        if not api:
            return True
        if query_token:
            values = (query or {}).get("token", [])
            supplied = values[0] if len(values) == 1 else ""
        else:
            supplied = self.headers.get("X-Minerec-Viewer-Token", "")
        if not hmac.compare_digest(supplied, self.application.token):
            self._error(HTTPStatus.UNAUTHORIZED, "viewer token required")
            return False
        return True

    def _static(self, request_path: str, *, send_body: bool) -> None:
        relative = "index.html" if request_path in {"", "/", "/index.html"} else request_path.removeprefix("/")
        if "\\" in relative or "\x00" in relative:
            raise FileNotFoundError
        root = self.application.static_root
        target = (root / relative).resolve()
        if root not in target.parents or target.is_symlink() or not target.is_file():
            raise FileNotFoundError
        data = target.read_bytes()
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self._common_headers(content_type, len(data), cache="no-cache")
        self.end_headers()
        if send_body:
            self.wfile.write(data)

    def _media(self, path: Path, *, send_body: bool) -> None:
        size = path.stat().st_size
        range_header = self.headers.get("Range")
        if not range_header:
            self.send_response(HTTPStatus.OK)
            self._common_headers("video/mp4", size, cache="no-store")
            self.send_header("Accept-Ranges", "bytes")
            self.end_headers()
            if send_body:
                self._stream_ranges(path, [(0, size - 1)])
            return
        try:
            ranges = _byte_ranges(range_header, size)
        except RecorderError as exc:
            self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
            self._common_headers("application/json; charset=utf-8", 0, cache="no-store")
            self.send_header("Content-Range", f"bytes */{size}")
            self.send_header("X-Minerec-Error", str(exc)[:200])
            self.end_headers()
            return
        if len(ranges) == 1:
            start, end = ranges[0]
            self.send_response(HTTPStatus.PARTIAL_CONTENT)
            self._common_headers("video/mp4", end - start + 1, cache="no-store")
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()
            if send_body:
                self._stream_ranges(path, ranges)
            return

        boundary = secrets.token_hex(16)
        parts: list[tuple[bytes, int, int]] = []
        content_length = 0
        for start, end in ranges:
            header = (f"--{boundary}\r\nContent-Type: video/mp4\r\nContent-Range: bytes {start}-{end}/{size}\r\n\r\n").encode("ascii")
            parts.append((header, start, end))
            content_length += len(header) + end - start + 1 + 2
        closing = f"--{boundary}--\r\n".encode("ascii")
        content_length += len(closing)
        self.send_response(HTTPStatus.PARTIAL_CONTENT)
        self._common_headers(f"multipart/byteranges; boundary={boundary}", content_length, cache="no-store")
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        if not send_body:
            return
        with path.open("rb") as handle:
            for header, start, end in parts:
                self.wfile.write(header)
                self._stream_one(handle, start, end)
                self.wfile.write(b"\r\n")
        self.wfile.write(closing)

    def _stream_ranges(self, path: Path, ranges: Iterable[tuple[int, int]]) -> None:
        with path.open("rb") as handle:
            for start, end in ranges:
                self._stream_one(handle, start, end)

    def _stream_one(self, handle: Any, start: int, end: int) -> None:  # noqa: ANN401
        handle.seek(start)
        remaining = end - start + 1
        while remaining:
            chunk = handle.read(min(1024 * 1024, remaining))
            if not chunk:
                raise OSError("render file ended while serving a byte range")
            self.wfile.write(chunk)
            remaining -= len(chunk)

    def _json(self, status: HTTPStatus, value: Any, *, send_body: bool = True) -> None:  # noqa: ANN401
        body = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8") + b"\n"
        if len(body) > MAX_JSON_RESPONSE_BYTES:
            raise RecorderError("viewer response exceeds safe size limit")
        self.send_response(status)
        self._common_headers("application/json; charset=utf-8", len(body), cache="no-store")
        self.end_headers()
        if send_body:
            self.wfile.write(body)

    def _error(self, status: HTTPStatus, message: str) -> None:
        body = json.dumps({"error": message}, separators=(",", ":")).encode("utf-8") + b"\n"
        self.send_response(status)
        self._common_headers("application/json; charset=utf-8", len(body), cache="no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _common_headers(self, content_type: str, content_length: int, *, cache: str) -> None:
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(content_length))
        self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; media-src 'self'; font-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
        )

    def log_message(self, format: str, *args: object) -> None:
        print(f"viewer: {self.address_string()} - {format % args}", file=sys.stderr)


def serve_viewer(bundle: Path | None = None, *, open_browser: bool = True) -> None:
    token = secrets.token_urlsafe(32)
    application = ViewerApplication(token)
    server = ViewerHTTPServer(("127.0.0.1", 0), application)
    try:
        if bundle is not None:
            application.service.import_bundle(bundle.expanduser().resolve())
        url = f"{application.origin}/?{urlencode({'token': token})}"
        print(f"Play-bundle viewer listening at {application.origin}")
        if open_browser and not webbrowser.open(url):
            print(f"Open {url}", file=sys.stderr)
        server.serve_forever()
    finally:
        server.server_close()
        application.close()


__all__ = [
    "ViewerApplication",
    "ViewerHTTPServer",
    "ViewerHandler",
    "serve_viewer",
]
