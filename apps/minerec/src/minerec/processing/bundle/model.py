from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, Callable, Mapping, TypeAlias

from minerec.errors import RecorderError

BUNDLE_FORMAT = "minerec-play-bundle"
BUNDLE_FORMAT_VERSION = 1
BUNDLE_EXTENSION = ".mcplay.zip"
TICK_RATE_HZ = 20

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SAFE_SEGMENT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class BundleError(RecorderError):
    """A portable play bundle failed a safety or integrity check."""


@dataclass(frozen=True)
class ArtifactSource:
    """A source file whose bytes were verified by the producing pipeline."""

    path: Path
    sha256: str
    size_bytes: int


@dataclass(frozen=True)
class BundleIdentity:
    server_name: str
    server_instance_id: str
    player_name: str
    player_uuid: str
    session_id: str
    connection_id: str
    started_at: str
    ended_at: str
    start_tick: int
    end_tick: int
    sensitivity: str = "private"
    known_modality_gaps: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReplaySegment:
    segment_id: str
    source: ArtifactSource
    source_segment_ordinal: int
    start_server_tick: int | None
    end_server_tick: int | None
    start_replay_tick: int | None
    end_replay_tick: int | None


@dataclass(frozen=True)
class RenderAttachment:
    video: ArtifactSource
    timeline: ArtifactSource
    frame_count: int
    width: int
    height: int
    codec: str = "h264"
    pixel_format: str = "yuv420p"
    fps: int = TICK_RATE_HZ
    fast_start: bool = True
    audio: bool = False


@dataclass(frozen=True)
class BundleRequest:
    identity: BundleIdentity
    actions: ArtifactSource
    scene: ArtifactSource
    replay_segments: tuple[ReplaySegment, ...]
    render: RenderAttachment | None = None


@dataclass(frozen=True)
class BundleLimits:
    max_archive_bytes: int = 2 * 1024**4
    max_entries: int = 4096
    max_central_directory_bytes: int = 64 * 1024**2
    max_metadata_bytes: int = 4 * 1024**2
    max_actions_bytes: int = 16 * 1024**3
    max_scene_bytes: int = 1024**4
    max_replay_bytes: int = 1024**4
    max_video_bytes: int = 1024**4
    max_timeline_bytes: int = 16 * 1024**3
    max_total_uncompressed_bytes: int = 2 * 1024**4
    max_compression_ratio: float = 200.0
    max_json_line_bytes: int = 16 * 1024**2


DEFAULT_BUNDLE_LIMITS = BundleLimits()

PathValidator: TypeAlias = Callable[[Path], object]
RenderValidator: TypeAlias = Callable[[Path, Mapping[str, Any]], object]
BundleInput: TypeAlias = str | Path | BinaryIO


@dataclass(frozen=True)
class ReplayDescriptor:
    ordinal: int
    segment_id: str
    source_segment_ordinal: int
    archive_path: Path
    sha256: str
    size_bytes: int
    start_server_tick: int | None
    end_server_tick: int | None
    start_replay_tick: int | None
    end_replay_tick: int | None


@dataclass(frozen=True)
class PublishedBundle:
    path: Path
    bundle_id: str
    reused: bool


@dataclass(frozen=True)
class OpenedBundle:
    """A validated bundle extracted into an exclusively owned directory.

    Call ``close`` (or use this value as a context manager) when its paths are
    no longer needed. ``metadata`` returns a fresh object on every access so a
    caller cannot mutate the validated value retained by this handle.
    """

    source: Path | None
    bundle_id: str
    _metadata_json: bytes
    extraction_root: Path
    actions_path: Path
    scene_path: Path
    replay_descriptors: tuple[ReplayDescriptor, ...]
    fpv_path: Path | None
    timeline_path: Path | None
    _temporary_directory: Any

    @property
    def metadata(self) -> dict[str, Any]:
        import json

        value = json.loads(self._metadata_json)
        if not isinstance(value, dict):  # pragma: no cover - guarded on ingress
            raise BundleError("validated bundle metadata is not an object")
        return value

    def close(self) -> None:
        self._temporary_directory.cleanup()

    def __enter__(self) -> OpenedBundle:
        return self

    def __exit__(self, _exc_type: object, _exc: object, _traceback: object) -> None:
        self.close()
