from __future__ import annotations

import base64
import binascii
import copy
import gzip
import hashlib
import hmac
import io
import json
import math
import os
import re
import sqlite3
import stat
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator

from .errors import RecorderError
from .render_contract import FULL_CLIENT_PRESENTATION_CONTRACT
from .render_hud import validate_hud_result_envelope


DATASET_SCHEMA_VERSION = 1
DATASET_OWNER = "mc-recorder"
DATASET_FORMAT = "mc-recorder-jsonl-v1"
DATASET_SUFFIX = ".dataset"
DATASET_FILES = (
    "samples.jsonl",
    "states.jsonl",
    "actions.jsonl",
    "modalities.jsonl",
)
_DATASET_ENTRIES = frozenset(("manifest.json", *DATASET_FILES))
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_OPAQUE_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_INDEX_SCHEMA_VERSION = 1
_MAX_SQLITE_INTEGER = 2**63 - 1

__all__ = (
    "ArtifactUnavailableError",
    "CatalogIssue",
    "DatasetCatalogResult",
    "DatasetMetadata",
    "DatasetNotFoundError",
    "DatasetSummary",
    "DatasetValidationError",
    "DatasetViewer",
    "DatasetViewerError",
    "PlayerConnectionSummary",
    "SampleDetail",
    "SampleNotFoundError",
    "SamplePage",
    "SampleSummary",
    "VerifiedArtifact",
    "TrajectoryBounds",
    "TrajectoryOverview",
    "TrajectoryPoint",
    "TrajectoryTrack",
    "VoxelCell",
    "VoxelSlice",
    "opaque_dataset_id",
)


class DatasetViewerError(RecorderError):
    """Base error raised by the exported-dataset reader."""


class DatasetValidationError(DatasetViewerError):
    """An exported dataset or indexed sample violates the v1 contract."""


class DatasetNotFoundError(DatasetViewerError):
    """The supplied opaque dataset identifier is unknown."""


class SampleNotFoundError(DatasetViewerError):
    """The supplied opaque sample identifier is unknown for the dataset."""


class ArtifactUnavailableError(DatasetViewerError):
    """A sample does not have the requested validated modality."""


def opaque_dataset_id(exports_root: Path, directory_name: str) -> str:
    """Derive the public non-path identifier for one direct dataset export."""

    if (
        not directory_name.endswith(DATASET_SUFFIX)
        or directory_name == DATASET_SUFFIX
        or Path(directory_name).name != directory_name
    ):
        raise DatasetViewerError("dataset directory name is invalid")
    root = Path(exports_root).expanduser().resolve()
    value = f"mc-recorder-dataset-v1\0{root}\0{directory_name}".encode()
    return hashlib.blake2b(value, digest_size=16).hexdigest()


@dataclass(frozen=True)
class CatalogIssue:
    name: str
    message: str


@dataclass(frozen=True)
class DatasetSummary:
    dataset_id: str
    session_id: str
    created_at: str | None
    sample_count: int
    player_count: int
    connection_count: int
    first_tick: int | None
    last_tick: int | None
    size_bytes: int
    rgb_samples: int
    voxel_samples: int


@dataclass(frozen=True)
class DatasetCatalogResult:
    datasets: tuple[DatasetSummary, ...]
    rejected: tuple[CatalogIssue, ...]


@dataclass(frozen=True)
class DatasetMetadata:
    dataset_id: str
    session_id: str
    created_at: str | None
    schema_version: int
    owner: str
    format: str
    sample_count: int
    state_count: int | None
    action_count: int | None
    first_tick: int | None
    last_tick: int | None
    size_bytes: int
    source_sealed_epochs: int | None
    source_active_epochs_skipped: int | None
    selected_players: tuple[str, ...]
    selected_from_tick: int | None
    selected_to_tick: int | None
    rgb_samples: int
    rgb_presentation: str | None
    voxel_samples: int
    files: dict[str, dict[str, object]]


@dataclass(frozen=True)
class PlayerConnectionSummary:
    player_uuid: str
    player_name: str | None
    connection_id: str
    sample_count: int
    first_tick: int
    last_tick: int
    valid_transitions: int
    rgb_samples: int
    voxel_samples: int


@dataclass(frozen=True)
class SampleSummary:
    sample_id: str
    server_tick: int
    next_server_tick: int
    player_uuid: str
    player_name: str | None
    connection_id: str
    dimension: str | None
    position: tuple[float, float, float] | None
    transition_valid: bool
    transition_invalid_reasons: tuple[str, ...]
    ordered_packet_count: int
    peer_count: int
    rgb_available: bool
    voxel_available: bool


@dataclass(frozen=True)
class SamplePage:
    items: tuple[SampleSummary, ...]
    total: int
    next_cursor: str | None


@dataclass(frozen=True)
class SampleDetail:
    sample_id: str
    record: dict[str, Any]


@dataclass(frozen=True)
class VerifiedArtifact:
    path: Path
    size_bytes: int
    sha256: str
    media_type: str


@dataclass(frozen=True)
class TrajectoryBounds:
    min_x: float
    max_x: float
    min_y: float
    max_y: float
    min_z: float
    max_z: float


@dataclass(frozen=True)
class TrajectoryPoint:
    server_tick: int
    x: float
    y: float
    z: float
    continuous_from_previous: bool


@dataclass(frozen=True)
class TrajectoryTrack:
    player_uuid: str
    player_name: str | None
    connection_id: str
    dimension: str | None
    point_count: int
    first_tick: int
    last_tick: int
    distance_blocks: float
    horizontal_distance_blocks: float
    points: tuple[TrajectoryPoint, ...]


@dataclass(frozen=True)
class TrajectoryOverview:
    total_points: int
    returned_points: int
    downsampled: bool
    omitted_tracks: int
    bounds: TrajectoryBounds | None
    tracks: tuple[TrajectoryTrack, ...]


@dataclass(frozen=True)
class VoxelCell:
    x: int
    y: int
    z: int
    covered: bool
    block_state: str | None
    palette_index: int | None


@dataclass(frozen=True)
class VoxelSlice:
    axis: str
    index: int
    world_coordinate: int
    row_axis: str
    column_axis: str
    origin: tuple[int, int, int]
    shape: tuple[int, int, int]
    dimension: str
    covered_cells: int
    total_cells: int
    coverage_complete: bool
    cells: tuple[tuple[VoxelCell, ...], ...]


@dataclass(frozen=True)
class _VerifiedFile:
    path: Path
    size_bytes: int
    sha256: str
    device: int
    inode: int
    modified_ns: int
    changed_ns: int


@dataclass(frozen=True)
class _Dataset:
    dataset_id: str
    directory: Path
    manifest: dict[str, Any]
    manifest_sha256: str
    files: dict[str, _VerifiedFile]
    fingerprint: str
    quick_signature: tuple[tuple[str, int, int, int, int, int], ...]


class DatasetViewer:
    """Secure, indexed reader for immutable ``*.dataset`` export directories.

    Dataset and sample identifiers are hashes, never paths. The SQLite database
    stores only byte offsets and small summary fields; full sample JSON is read
    lazily from the hash-verified export.
    """

    def __init__(
        self,
        exports_root: Path,
        runtime_root: Path,
        *,
        max_manifest_bytes: int = 4 * 1024 * 1024,
        max_sample_line_bytes: int = 32 * 1024 * 1024,
        max_rgb_bytes: int = 128 * 1024 * 1024,
        max_voxel_compressed_bytes: int = 64 * 1024 * 1024,
        max_voxel_json_bytes: int = 64 * 1024 * 1024,
        max_voxel_cells: int = 2_000_000,
        max_voxel_axis: int = 129,
        max_page_size: int = 200,
    ) -> None:
        self.exports_root = Path(exports_root).expanduser().resolve()
        self.runtime_root = Path(runtime_root).expanduser().resolve()
        self.max_manifest_bytes = _positive_limit(
            max_manifest_bytes, "manifest byte limit"
        )
        self.max_sample_line_bytes = _positive_limit(
            max_sample_line_bytes, "sample line byte limit"
        )
        self.max_rgb_bytes = _positive_limit(max_rgb_bytes, "RGB byte limit")
        self.max_voxel_compressed_bytes = _positive_limit(
            max_voxel_compressed_bytes, "voxel compressed byte limit"
        )
        self.max_voxel_json_bytes = _positive_limit(
            max_voxel_json_bytes, "voxel JSON byte limit"
        )
        self.max_voxel_cells = _positive_limit(max_voxel_cells, "voxel cell limit")
        self.max_voxel_axis = _positive_limit(max_voxel_axis, "voxel axis limit")
        self.max_page_size = _positive_limit(max_page_size, "page size limit")
        self._lock = threading.RLock()
        self._cache: dict[str, _Dataset] = {}

        index_directory = self.runtime_root / "dataset-viewer"
        index_directory.mkdir(parents=True, exist_ok=True)
        if index_directory.is_symlink() or not index_directory.is_dir():
            raise DatasetViewerError(
                f"dataset index root is not a regular directory: {index_directory}"
            )
        self.index_path = index_directory / "samples-v1.sqlite3"
        if self.index_path.is_symlink():
            raise DatasetViewerError(
                f"dataset index may not be a symlink: {self.index_path}"
            )
        self._initialize_index()

    def catalog(self) -> DatasetCatalogResult:
        summaries: list[DatasetSummary] = []
        rejected: list[CatalogIssue] = []
        for candidate in self._dataset_candidates():
            try:
                dataset = self._load_dataset(candidate)
                self._ensure_index(dataset)
                summaries.append(self._dataset_summary(dataset))
            except DatasetViewerError as exc:
                rejected.append(CatalogIssue(candidate.name, str(exc)))
        summaries.sort(
            key=lambda item: ((item.created_at or ""), item.session_id), reverse=True
        )
        rejected.sort(key=lambda item: item.name)
        return DatasetCatalogResult(tuple(summaries), tuple(rejected))

    def list_datasets(self) -> tuple[DatasetSummary, ...]:
        return self.catalog().datasets

    def has_cached_verified_index(self, dataset_id: str) -> bool:
        """Return quickly only when this process already verified and indexed the dataset.

        This deliberately never hashes files or waits behind an in-progress background
        index build. Callers can keep status endpoints responsive while ``catalog()``
        performs first-discovery work on its worker thread.
        """

        _validate_opaque_id(dataset_id, "dataset id")
        try:
            candidate = next(
                (
                    path
                    for path in self._dataset_candidates()
                    if self._dataset_id(path.name) == dataset_id
                ),
                None,
            )
        except DatasetViewerError:
            return False
        if candidate is None or not self._lock.acquire(blocking=False):
            return False
        try:
            try:
                _entries, quick_signature = self._inspect_dataset_entries(candidate)
            except DatasetViewerError:
                return False
            cached = self._cache.get(candidate.name)
            if cached is None or cached.quick_signature != quick_signature:
                return False
            database: sqlite3.Connection | None = None
            try:
                database = sqlite3.connect(self.index_path, timeout=0.05)
                row = database.execute(
                    "SELECT fingerprint FROM indexed_datasets WHERE dataset_id = ?",
                    (dataset_id,),
                ).fetchone()
            except sqlite3.Error:
                return False
            finally:
                if database is not None:
                    database.close()
            return row is not None and row[0] == cached.fingerprint
        finally:
            self._lock.release()

    def get_dataset_metadata(self, dataset_id: str) -> DatasetMetadata:
        dataset = self._dataset_by_id(dataset_id)
        self._ensure_index(dataset)
        with self._connect() as database:
            row = database.execute(
                """
                SELECT sample_count, first_tick, last_tick, rgb_samples, voxel_samples
                FROM indexed_datasets WHERE dataset_id = ?
                """,
                (dataset.dataset_id,),
            ).fetchone()
        if row is None:
            raise DatasetViewerError("dataset index disappeared after creation")

        manifest = dataset.manifest
        source = (
            manifest.get("source") if isinstance(manifest.get("source"), dict) else {}
        )
        selection = (
            manifest.get("selection")
            if isinstance(manifest.get("selection"), dict)
            else {}
        )
        players = selection.get("players")
        selected_players = (
            tuple(sorted(item for item in players if isinstance(item, str)))
            if isinstance(players, list)
            else ()
        )
        files = {
            name: {"size_bytes": verified.size_bytes, "sha256": verified.sha256}
            for name, verified in sorted(dataset.files.items())
        }
        return DatasetMetadata(
            dataset_id=dataset.dataset_id,
            session_id=_required_string(manifest, "session_id", "dataset manifest"),
            created_at=manifest.get("created_at")
            if isinstance(manifest.get("created_at"), str)
            else None,
            schema_version=DATASET_SCHEMA_VERSION,
            owner=DATASET_OWNER,
            format=DATASET_FORMAT,
            sample_count=int(row[0]),
            state_count=_manifest_modality_records(manifest, "state"),
            action_count=_manifest_modality_records(manifest, "actions"),
            first_tick=_nullable_int(row[1]),
            last_tick=_nullable_int(row[2]),
            size_bytes=sum(item.size_bytes for item in dataset.files.values()),
            source_sealed_epochs=_mapping_int(source, "sealed_epochs"),
            source_active_epochs_skipped=_mapping_int(source, "active_epochs_skipped"),
            selected_players=selected_players,
            selected_from_tick=_mapping_int(selection, "from_tick"),
            selected_to_tick=_mapping_int(selection, "to_tick"),
            rgb_samples=int(row[3]),
            rgb_presentation=_rgb_presentation(
                selection,
                int(row[3]),
                dataset_id=dataset.dataset_id,
                session_id=_required_string(
                    manifest, "session_id", "dataset manifest"
                ),
            ),
            voxel_samples=int(row[4]),
            files=files,
        )

    def list_player_connections(
        self, dataset_id: str
    ) -> tuple[PlayerConnectionSummary, ...]:
        dataset = self._dataset_by_id(dataset_id)
        self._ensure_index(dataset)
        with self._connect() as database:
            rows = database.execute(
                """
                SELECT player_uuid, MAX(player_name), connection_id, COUNT(*),
                       MIN(server_tick), MAX(server_tick), SUM(transition_valid),
                       SUM(rgb_available), SUM(voxel_available)
                FROM samples
                WHERE dataset_id = ?
                GROUP BY player_uuid, connection_id
                ORDER BY player_uuid, MIN(server_tick), connection_id
                """,
                (dataset.dataset_id,),
            ).fetchall()
        return tuple(
            PlayerConnectionSummary(
                player_uuid=str(row[0]),
                player_name=str(row[1]) if row[1] is not None else None,
                connection_id=str(row[2]),
                sample_count=int(row[3]),
                first_tick=int(row[4]),
                last_tick=int(row[5]),
                valid_transitions=int(row[6]),
                rgb_samples=int(row[7]),
                voxel_samples=int(row[8]),
            )
            for row in rows
        )

    def list_sample_summaries(
        self,
        dataset_id: str,
        *,
        player_uuid: str | None = None,
        connection_id: str | None = None,
        from_tick: int | None = None,
        to_tick: int | None = None,
        transition_valid: bool | None = None,
        rgb_available: bool | None = None,
        voxel_available: bool | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> SamplePage:
        dataset = self._dataset_by_id(dataset_id)
        self._ensure_index(dataset)
        if (
            not isinstance(limit, int)
            or isinstance(limit, bool)
            or not 1 <= limit <= self.max_page_size
        ):
            raise DatasetViewerError(
                f"limit must be between 1 and {self.max_page_size}"
            )
        from_tick = _optional_tick(from_tick, "from_tick")
        to_tick = _optional_tick(to_tick, "to_tick")
        if from_tick is not None and to_tick is not None and from_tick > to_tick:
            raise DatasetViewerError("from_tick cannot be greater than to_tick")
        for name, value in (
            ("player_uuid", player_uuid),
            ("connection_id", connection_id),
        ):
            if value is not None and (not isinstance(value, str) or not value):
                raise DatasetViewerError(f"{name} must be a non-empty string")
        for name, value in (
            ("transition_valid", transition_valid),
            ("rgb_available", rgb_available),
            ("voxel_available", voxel_available),
        ):
            if value is not None and not isinstance(value, bool):
                raise DatasetViewerError(f"{name} must be a boolean")

        clauses = ["dataset_id = ?"]
        parameters: list[object] = [dataset.dataset_id]
        filters = (
            ("player_uuid", player_uuid),
            ("connection_id", connection_id),
            ("server_tick >=", from_tick),
            ("server_tick <=", to_tick),
            (
                "transition_valid",
                int(transition_valid) if transition_valid is not None else None,
            ),
            (
                "rgb_available",
                int(rgb_available) if rgb_available is not None else None,
            ),
            (
                "voxel_available",
                int(voxel_available) if voxel_available is not None else None,
            ),
        )
        for expression, value in filters:
            if value is not None:
                clauses.append(
                    f"{expression} ?"
                    if expression.endswith((">=", "<="))
                    else f"{expression} = ?"
                )
                parameters.append(value)
        filter_sql = " AND ".join(clauses)

        with self._connect() as database:
            total = int(
                database.execute(
                    f"SELECT COUNT(*) FROM samples WHERE {filter_sql}", parameters
                ).fetchone()[0]
            )
            page_clauses = list(clauses)
            page_parameters = list(parameters)
            if cursor is not None:
                _validate_opaque_id(cursor, "sample cursor")
                cursor_row = database.execute(
                    "SELECT ordinal FROM samples WHERE dataset_id = ? AND sample_id = ?",
                    (dataset.dataset_id, cursor),
                ).fetchone()
                if cursor_row is None:
                    raise SampleNotFoundError(
                        "sample cursor was not found in this dataset"
                    )
                page_clauses.append("ordinal > ?")
                page_parameters.append(int(cursor_row[0]))
            page_parameters.append(limit + 1)
            rows = database.execute(
                f"""
                SELECT sample_id, server_tick, next_server_tick, player_uuid, player_name,
                       connection_id, dimension, position_x, position_y, position_z,
                       transition_valid, invalid_reasons, ordered_packet_count, peer_count,
                       rgb_available, voxel_available
                FROM samples
                WHERE {" AND ".join(page_clauses)}
                ORDER BY ordinal
                LIMIT ?
                """,
                page_parameters,
            ).fetchall()

        has_more = len(rows) > limit
        rows = rows[:limit]
        items = tuple(_sample_summary(row) for row in rows)
        next_cursor = items[-1].sample_id if has_more and items else None
        return SamplePage(items=items, total=total, next_cursor=next_cursor)

    def get_sample_detail(self, dataset_id: str, sample_id: str) -> SampleDetail:
        dataset = self._dataset_by_id(dataset_id)
        record = self._load_sample_record(dataset, sample_id)
        public = copy.deepcopy(record)
        modalities = public.get("modalities")
        if isinstance(modalities, dict):
            rgb = modalities.get("rgb")
            if isinstance(rgb, dict):
                rgb.pop("reference", None)
                rgb["artifact_id"] = (
                    sample_id
                    if rgb.get("available") is True and rgb.get("valid") is True
                    else None
                )
            voxels = modalities.get("voxels")
            if isinstance(voxels, dict):
                for key in (
                    "reference",
                    "coverage_mask_reference",
                    "unvalidated_source_reference",
                    "unvalidated_source_coverage_mask_reference",
                ):
                    voxels.pop(key, None)
                voxels["artifact_id"] = (
                    sample_id
                    if voxels.get("available") is True and voxels.get("valid") is True
                    else None
                )
        return SampleDetail(sample_id=sample_id, record=public)

    def get_trajectory(
        self,
        dataset_id: str,
        *,
        player_uuid: str | None = None,
        connection_id: str | None = None,
        from_tick: int | None = None,
        to_tick: int | None = None,
        transition_valid: bool | None = None,
        rgb_available: bool | None = None,
        voxel_available: bool | None = None,
        max_points: int = 2_400,
    ) -> TrajectoryOverview:
        """Return a bounded top-down trajectory from the verified sample index.

        The source rows are streamed per player/connection/dimension track. The
        response preserves each returned track's endpoints and marks paths that
        cross an invalid or missing transition as discontinuous. Exact travel
        distance is accumulated from all matching indexed positions, not from
        only the returned display points.
        """

        dataset = self._dataset_by_id(dataset_id)
        self._ensure_index(dataset)
        if (
            not isinstance(max_points, int)
            or isinstance(max_points, bool)
            or not 1 <= max_points <= 10_000
        ):
            raise DatasetViewerError("max_points must be between 1 and 10000")
        from_tick = _optional_tick(from_tick, "from_tick")
        to_tick = _optional_tick(to_tick, "to_tick")
        if from_tick is not None and to_tick is not None and from_tick > to_tick:
            raise DatasetViewerError("from_tick cannot be greater than to_tick")
        for name, value in (
            ("player_uuid", player_uuid),
            ("connection_id", connection_id),
        ):
            if value is not None and (not isinstance(value, str) or not value):
                raise DatasetViewerError(f"{name} must be a non-empty string")
        for name, value in (
            ("transition_valid", transition_valid),
            ("rgb_available", rgb_available),
            ("voxel_available", voxel_available),
        ):
            if value is not None and not isinstance(value, bool):
                raise DatasetViewerError(f"{name} must be a boolean")

        clauses = [
            "dataset_id = ?",
            "position_x IS NOT NULL",
            "position_y IS NOT NULL",
            "position_z IS NOT NULL",
        ]
        parameters: list[object] = [dataset.dataset_id]
        filters = (
            ("player_uuid", player_uuid),
            ("connection_id", connection_id),
            ("server_tick >=", from_tick),
            ("server_tick <=", to_tick),
            (
                "transition_valid",
                int(transition_valid) if transition_valid is not None else None,
            ),
            (
                "rgb_available",
                int(rgb_available) if rgb_available is not None else None,
            ),
            (
                "voxel_available",
                int(voxel_available) if voxel_available is not None else None,
            ),
        )
        for expression, value in filters:
            if value is not None:
                clauses.append(
                    f"{expression} ?"
                    if expression.endswith((">=", "<="))
                    else f"{expression} = ?"
                )
                parameters.append(value)
        filter_sql = " AND ".join(clauses)

        with self._connect() as database:
            bounds_row = database.execute(
                f"""
                SELECT COUNT(*), MIN(position_x), MAX(position_x),
                       MIN(position_y), MAX(position_y),
                       MIN(position_z), MAX(position_z)
                FROM samples WHERE {filter_sql}
                """,
                parameters,
            ).fetchone()
            total_points = int(bounds_row[0]) if bounds_row is not None else 0
            if total_points == 0:
                return TrajectoryOverview(0, 0, False, 0, None, ())
            bounds = TrajectoryBounds(
                min_x=float(bounds_row[1]),
                max_x=float(bounds_row[2]),
                min_y=float(bounds_row[3]),
                max_y=float(bounds_row[4]),
                min_z=float(bounds_row[5]),
                max_z=float(bounds_row[6]),
            )
            track_count = int(
                database.execute(
                    f"""
                    SELECT COUNT(*) FROM (
                        SELECT 1 FROM samples
                        WHERE {filter_sql}
                        GROUP BY player_uuid, connection_id, dimension
                    )
                    """,
                    parameters,
                ).fetchone()[0]
            )
            group_rows = database.execute(
                f"""
                SELECT player_uuid, MAX(player_name), connection_id, dimension,
                       COUNT(*), MIN(server_tick), MAX(server_tick), MIN(ordinal)
                FROM samples
                WHERE {filter_sql}
                GROUP BY player_uuid, connection_id, dimension
                ORDER BY MIN(ordinal), player_uuid, connection_id, dimension
                LIMIT ?
                """,
                [*parameters, max_points],
            ).fetchall()
            budgets = _allocate_trajectory_budgets(
                [int(row[4]) for row in group_rows], max_points
            )
            tracks: list[TrajectoryTrack] = []
            for group, budget in zip(group_rows, budgets, strict=True):
                if budget <= 0:
                    continue
                group_count = int(group[4])
                group_clauses = [*clauses, "player_uuid = ?", "connection_id = ?"]
                group_parameters = [*parameters, group[0], group[2]]
                if group[3] is None:
                    group_clauses.append("dimension IS NULL")
                else:
                    group_clauses.append("dimension = ?")
                    group_parameters.append(group[3])
                targets = _trajectory_target_indexes(group_count, budget)
                cursor = database.execute(
                    f"""
                    SELECT server_tick, next_server_tick, position_x, position_y,
                           position_z, transition_valid
                    FROM samples
                    WHERE {" AND ".join(group_clauses)}
                    ORDER BY ordinal
                    """,
                    group_parameters,
                )
                points: list[TrajectoryPoint] = []
                previous: tuple[int, int, float, float, float, bool] | None = None
                continuous_since_selected = True
                distance_blocks = 0.0
                horizontal_distance_blocks = 0.0
                seen = 0
                for index, row in enumerate(cursor):
                    current = (
                        int(row[0]),
                        int(row[1]),
                        float(row[2]),
                        float(row[3]),
                        float(row[4]),
                        bool(row[5]),
                    )
                    if previous is not None:
                        continuous = previous[5] and current[0] == previous[1]
                        continuous_since_selected = (
                            continuous_since_selected and continuous
                        )
                        if continuous:
                            dx = current[2] - previous[2]
                            dy = current[3] - previous[3]
                            dz = current[4] - previous[4]
                            horizontal_distance_blocks += math.hypot(dx, dz)
                            distance_blocks += math.sqrt(dx * dx + dy * dy + dz * dz)
                    if index in targets:
                        points.append(
                            TrajectoryPoint(
                                server_tick=current[0],
                                x=current[2],
                                y=current[3],
                                z=current[4],
                                continuous_from_previous=(
                                    bool(previous) and continuous_since_selected
                                ),
                            )
                        )
                        continuous_since_selected = True
                    previous = current
                    seen += 1
                if seen != group_count:
                    raise DatasetViewerError(
                        "dataset trajectory index changed while it was being read"
                    )
                tracks.append(
                    TrajectoryTrack(
                        player_uuid=str(group[0]),
                        player_name=str(group[1]) if group[1] is not None else None,
                        connection_id=str(group[2]),
                        dimension=str(group[3]) if group[3] is not None else None,
                        point_count=group_count,
                        first_tick=int(group[5]),
                        last_tick=int(group[6]),
                        distance_blocks=distance_blocks,
                        horizontal_distance_blocks=horizontal_distance_blocks,
                        points=tuple(points),
                    )
                )

        returned_points = sum(len(track.points) for track in tracks)
        omitted_tracks = track_count - len(group_rows) + sum(
            1 for budget in budgets if budget <= 0
        )
        return TrajectoryOverview(
            total_points=total_points,
            returned_points=returned_points,
            downsampled=returned_points < total_points,
            omitted_tracks=omitted_tracks,
            bounds=bounds,
            tracks=tuple(tracks),
        )

    def resolve_rgb_artifact(self, dataset_id: str, sample_id: str) -> VerifiedArtifact:
        dataset = self._dataset_by_id(dataset_id)
        record = self._load_sample_record(dataset, sample_id)
        rgb = _sample_modality(record, "rgb")
        if rgb.get("available") is not True or rgb.get("valid") is not True:
            raise ArtifactUnavailableError("sample has no validated RGB artifact")
        artifact = self._verified_artifact(
            dataset,
            rgb,
            suffix=".png",
            maximum_bytes=self.max_rgb_bytes,
            label="RGB",
            media_type="image/png",
        )
        with artifact.path.open("rb") as handle:
            if handle.read(8) != b"\x89PNG\r\n\x1a\n":
                raise DatasetValidationError(
                    "RGB artifact does not have a PNG signature"
                )
        return artifact

    def get_voxel_slice(
        self,
        dataset_id: str,
        sample_id: str,
        *,
        axis: str,
        index: int,
    ) -> VoxelSlice:
        dataset = self._dataset_by_id(dataset_id)
        sample = self._load_sample_record(dataset, sample_id)
        modality = _sample_modality(sample, "voxels")
        if modality.get("available") is not True or modality.get("valid") is not True:
            raise ArtifactUnavailableError("sample has no validated voxel artifact")
        artifact = self._verified_artifact(
            dataset,
            modality,
            suffix=".json.gz",
            maximum_bytes=self.max_voxel_compressed_bytes,
            label="voxel",
            media_type="application/gzip",
        )
        snapshot = self._read_voxel_snapshot(artifact)
        decoded = self._validate_voxel_snapshot(snapshot, sample, modality)
        return _voxel_slice(decoded, axis, index)

    def _dataset_candidates(self) -> list[Path]:
        if not self.exports_root.exists():
            return []
        if self.exports_root.is_symlink() or not self.exports_root.is_dir():
            raise DatasetViewerError(
                f"exports root is not a regular directory: {self.exports_root}"
            )
        candidates: list[Path] = []
        for child in self.exports_root.iterdir():
            if not child.name.endswith(DATASET_SUFFIX) or child.name == DATASET_SUFFIX:
                continue
            if child.is_symlink() or not child.is_dir():
                continue
            if child.resolve().parent != self.exports_root:
                continue
            candidates.append(child)
        return sorted(candidates, key=lambda path: path.name)

    def _dataset_by_id(self, dataset_id: str) -> _Dataset:
        _validate_opaque_id(dataset_id, "dataset id")
        for candidate in self._dataset_candidates():
            if self._dataset_id(candidate.name) == dataset_id:
                return self._load_dataset(candidate)
        raise DatasetNotFoundError("dataset was not found")

    def _dataset_id(self, directory_name: str) -> str:
        return opaque_dataset_id(self.exports_root, directory_name)

    def _load_dataset(self, candidate: Path) -> _Dataset:
        with self._lock:
            if (
                candidate.is_symlink()
                or not candidate.is_dir()
                or candidate.parent.resolve() != self.exports_root
            ):
                raise DatasetValidationError(
                    f"dataset is not a contained directory: {candidate.name}"
                )
            entries, quick_signature = self._inspect_dataset_entries(candidate)
            cached = self._cache.get(candidate.name)
            if cached is not None and cached.quick_signature == quick_signature:
                return cached

            manifest_path = entries["manifest.json"]
            try:
                with manifest_path.open("rb") as handle:
                    manifest_bytes = handle.read(self.max_manifest_bytes + 1)
            except OSError as exc:
                raise DatasetValidationError(
                    f"cannot read dataset manifest: {candidate.name}"
                ) from exc
            if len(manifest_bytes) > self.max_manifest_bytes:
                raise DatasetValidationError(
                    f"dataset manifest exceeds {self.max_manifest_bytes} bytes"
                )
            try:
                manifest = _strict_json_loads(manifest_bytes)
            except (ValueError, RecursionError) as exc:
                raise DatasetValidationError(
                    f"invalid dataset manifest: {candidate.name}"
                ) from exc
            if not isinstance(manifest, dict):
                raise DatasetValidationError("dataset manifest must be an object")
            if manifest.get("schema_version") != DATASET_SCHEMA_VERSION or isinstance(
                manifest.get("schema_version"), bool
            ):
                raise DatasetValidationError("unsupported dataset schema_version")
            if manifest.get("owner") != DATASET_OWNER:
                raise DatasetValidationError(f"dataset owner must be {DATASET_OWNER!r}")
            if manifest.get("format") != DATASET_FORMAT:
                raise DatasetValidationError(
                    f"dataset format must be {DATASET_FORMAT!r}"
                )
            _required_string(manifest, "session_id", "dataset manifest")
            file_envelopes = manifest.get("files")
            if not isinstance(file_envelopes, dict) or set(file_envelopes) != set(
                DATASET_FILES
            ):
                raise DatasetValidationError(
                    "dataset manifest files must exactly match the v1 streams"
                )

            verified: dict[str, _VerifiedFile] = {}
            for name in DATASET_FILES:
                envelope = file_envelopes.get(name)
                if not isinstance(envelope, dict):
                    raise DatasetValidationError(
                        f"manifest file envelope is invalid: {name}"
                    )
                if set(envelope) != {"size_bytes", "sha256"}:
                    raise DatasetValidationError(
                        f"manifest file envelope has unexpected fields: {name}"
                    )
                expected_size = envelope.get("size_bytes")
                expected_sha = envelope.get("sha256")
                if (
                    not isinstance(expected_size, int)
                    or isinstance(expected_size, bool)
                    or expected_size < 0
                ):
                    raise DatasetValidationError(
                        f"manifest size_bytes is invalid: {name}"
                    )
                if (
                    not isinstance(expected_sha, str)
                    or _SHA256_RE.fullmatch(expected_sha) is None
                ):
                    raise DatasetValidationError(f"manifest SHA-256 is invalid: {name}")
                verified[name] = _verify_file(
                    entries[name], expected_size, expected_sha, name
                )

            manifest_sha = hashlib.sha256(manifest_bytes).hexdigest()
            fingerprint_hash = hashlib.sha256()
            fingerprint_hash.update(f"viewer-index-v{_INDEX_SCHEMA_VERSION}\0".encode())
            fingerprint_hash.update(manifest_sha.encode())
            for name in DATASET_FILES:
                item = verified[name]
                fingerprint_hash.update(
                    f"\0{name}\0{item.size_bytes}\0{item.sha256}".encode()
                )
            dataset = _Dataset(
                dataset_id=self._dataset_id(candidate.name),
                directory=candidate,
                manifest=manifest,
                manifest_sha256=manifest_sha,
                files=verified,
                fingerprint=fingerprint_hash.hexdigest(),
                quick_signature=quick_signature,
            )
            self._cache[candidate.name] = dataset
            return dataset

    def _inspect_dataset_entries(
        self, candidate: Path
    ) -> tuple[dict[str, Path], tuple[tuple[str, int, int, int, int, int], ...]]:
        try:
            scanned = list(os.scandir(candidate))
        except OSError as exc:
            raise DatasetValidationError(
                f"cannot inspect dataset directory: {candidate.name}"
            ) from exc
        names = {entry.name for entry in scanned}
        if names != _DATASET_ENTRIES:
            missing = sorted(_DATASET_ENTRIES - names)
            unexpected = sorted(names - _DATASET_ENTRIES)
            detail = []
            if missing:
                detail.append(f"missing {', '.join(missing)}")
            if unexpected:
                detail.append(f"unexpected {', '.join(unexpected)}")
            raise DatasetValidationError(
                "dataset directory entries are not exact: " + "; ".join(detail)
            )
        entries: dict[str, Path] = {}
        signature: list[tuple[str, int, int, int, int, int]] = []
        for entry in scanned:
            try:
                metadata = entry.stat(follow_symlinks=False)
            except OSError as exc:
                raise DatasetValidationError(
                    f"cannot stat dataset entry: {entry.name}"
                ) from exc
            if entry.is_symlink() or not stat.S_ISREG(metadata.st_mode):
                raise DatasetValidationError(
                    f"dataset entry must be a regular non-symlink file: {entry.name}"
                )
            path = candidate / entry.name
            if path.resolve().parent != candidate.resolve():
                raise DatasetValidationError(
                    f"dataset entry escapes its directory: {entry.name}"
                )
            entries[entry.name] = path
            signature.append(
                (
                    entry.name,
                    metadata.st_dev,
                    metadata.st_ino,
                    metadata.st_size,
                    metadata.st_mtime_ns,
                    metadata.st_ctime_ns,
                )
            )
        return entries, tuple(sorted(signature))

    def _initialize_index(self) -> None:
        with self._connect() as database:
            database.executescript(
                """
                CREATE TABLE IF NOT EXISTS index_metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS indexed_datasets (
                    dataset_id TEXT PRIMARY KEY,
                    fingerprint TEXT NOT NULL,
                    sample_count INTEGER NOT NULL,
                    first_tick INTEGER,
                    last_tick INTEGER,
                    rgb_samples INTEGER NOT NULL,
                    voxel_samples INTEGER NOT NULL,
                    indexed_at_ns INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS samples (
                    dataset_id TEXT NOT NULL,
                    ordinal INTEGER NOT NULL,
                    sample_id TEXT NOT NULL,
                    byte_offset INTEGER NOT NULL,
                    byte_length INTEGER NOT NULL,
                    session_id TEXT NOT NULL,
                    server_tick INTEGER NOT NULL,
                    next_server_tick INTEGER NOT NULL,
                    player_uuid TEXT NOT NULL,
                    player_name TEXT,
                    connection_id TEXT NOT NULL,
                    dimension TEXT,
                    position_x REAL,
                    position_y REAL,
                    position_z REAL,
                    transition_valid INTEGER NOT NULL,
                    invalid_reasons TEXT NOT NULL,
                    ordered_packet_count INTEGER NOT NULL,
                    peer_count INTEGER NOT NULL,
                    rgb_available INTEGER NOT NULL,
                    voxel_available INTEGER NOT NULL,
                    PRIMARY KEY (dataset_id, ordinal),
                    UNIQUE (dataset_id, sample_id),
                    UNIQUE (dataset_id, server_tick, player_uuid, connection_id)
                );
                CREATE INDEX IF NOT EXISTS samples_subject_tick
                    ON samples(dataset_id, player_uuid, connection_id, server_tick);
                CREATE INDEX IF NOT EXISTS samples_filters
                    ON samples(dataset_id, transition_valid, rgb_available, voxel_available, ordinal);
                """
            )
            existing = database.execute(
                "SELECT value FROM index_metadata WHERE key = 'schema_version'"
            ).fetchone()
            if existing is not None and existing[0] != str(_INDEX_SCHEMA_VERSION):
                raise DatasetViewerError("dataset viewer index schema is unsupported")
            database.execute(
                "INSERT OR REPLACE INTO index_metadata(key, value) VALUES('schema_version', ?)",
                (str(_INDEX_SCHEMA_VERSION),),
            )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        database = sqlite3.connect(self.index_path, timeout=30.0)
        try:
            database.execute("PRAGMA busy_timeout = 30000")
            database.execute("PRAGMA journal_mode = WAL")
            database.execute("PRAGMA synchronous = NORMAL")
            with database:
                yield database
        finally:
            database.close()

    def _ensure_index(self, dataset: _Dataset) -> None:
        with self._lock, self._connect() as database:
            current = database.execute(
                "SELECT fingerprint FROM indexed_datasets WHERE dataset_id = ?",
                (dataset.dataset_id,),
            ).fetchone()
            if current is not None and current[0] == dataset.fingerprint:
                return
            try:
                database.execute("BEGIN IMMEDIATE")
                current = database.execute(
                    "SELECT fingerprint FROM indexed_datasets WHERE dataset_id = ?",
                    (dataset.dataset_id,),
                ).fetchone()
                if current is not None and current[0] == dataset.fingerprint:
                    database.commit()
                    return
                database.execute(
                    "DELETE FROM samples WHERE dataset_id = ?", (dataset.dataset_id,)
                )
                sample_count, first_tick, last_tick, rgb_count, voxel_count = (
                    self._index_samples(database, dataset)
                )
                database.execute(
                    """
                    INSERT OR REPLACE INTO indexed_datasets(
                        dataset_id, fingerprint, sample_count, first_tick, last_tick,
                        rgb_samples, voxel_samples, indexed_at_ns
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        dataset.dataset_id,
                        dataset.fingerprint,
                        sample_count,
                        first_tick,
                        last_tick,
                        rgb_count,
                        voxel_count,
                        time.time_ns(),
                    ),
                )
                database.commit()
            except (sqlite3.Error, DatasetViewerError) as exc:
                database.rollback()
                if isinstance(exc, DatasetViewerError):
                    raise
                raise DatasetValidationError(
                    f"could not build dataset sample index: {exc}"
                ) from exc

    def _index_samples(
        self, database: sqlite3.Connection, dataset: _Dataset
    ) -> tuple[int, int | None, int | None, int, int]:
        samples = dataset.files["samples.jsonl"]
        digest = hashlib.sha256()
        byte_count = 0
        sample_count = 0
        first_tick: int | None = None
        last_tick: int | None = None
        rgb_count = 0
        voxel_count = 0
        try:
            handle = samples.path.open("rb")
        except OSError as exc:
            raise DatasetValidationError(
                "cannot open samples.jsonl while indexing"
            ) from exc
        with handle:
            while True:
                offset = handle.tell()
                line = handle.readline(self.max_sample_line_bytes + 1)
                if not line:
                    break
                digest.update(line)
                byte_count += len(line)
                if len(line) > self.max_sample_line_bytes:
                    raise DatasetValidationError(
                        f"samples.jsonl contains a row larger than {self.max_sample_line_bytes} bytes"
                    )
                if not line.strip():
                    continue
                context = f"samples.jsonl byte {offset}"
                try:
                    record = _strict_json_loads(line)
                except (ValueError, RecursionError) as exc:
                    raise DatasetValidationError(f"{context}: invalid JSON") from exc
                values = _sample_index_values(record, dataset, offset, len(line))
                try:
                    database.execute(
                        """
                        INSERT INTO samples(
                            dataset_id, ordinal, sample_id, byte_offset, byte_length,
                            session_id, server_tick, next_server_tick, player_uuid,
                            player_name, connection_id, dimension, position_x, position_y,
                            position_z, transition_valid, invalid_reasons,
                            ordered_packet_count, peer_count, rgb_available, voxel_available
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (dataset.dataset_id, sample_count, *values),
                    )
                except sqlite3.IntegrityError as exc:
                    raise DatasetValidationError(
                        f"{context}: duplicate sample identity"
                    ) from exc
                tick = int(values[4])
                first_tick = tick if first_tick is None else min(first_tick, tick)
                last_tick = tick if last_tick is None else max(last_tick, tick)
                rgb_count += int(values[-2])
                voxel_count += int(values[-1])
                sample_count += 1

        if byte_count != samples.size_bytes or digest.hexdigest() != samples.sha256:
            raise DatasetValidationError(
                "samples.jsonl changed while its index was being built"
            )
        declared = _manifest_modality_records(dataset.manifest, "samples")
        if declared is not None and declared != sample_count:
            raise DatasetValidationError(
                f"dataset manifest declares {declared} samples but samples.jsonl contains {sample_count}"
            )
        return sample_count, first_tick, last_tick, rgb_count, voxel_count

    def _dataset_summary(self, dataset: _Dataset) -> DatasetSummary:
        with self._connect() as database:
            row = database.execute(
                """
                SELECT d.sample_count, d.first_tick, d.last_tick, d.rgb_samples,
                       d.voxel_samples, COUNT(DISTINCT s.player_uuid),
                       COUNT(DISTINCT s.player_uuid || char(0) || s.connection_id)
                FROM indexed_datasets d
                LEFT JOIN samples s ON s.dataset_id = d.dataset_id
                WHERE d.dataset_id = ?
                GROUP BY d.dataset_id
                """,
                (dataset.dataset_id,),
            ).fetchone()
        if row is None:
            raise DatasetViewerError("dataset index is missing")
        return DatasetSummary(
            dataset_id=dataset.dataset_id,
            session_id=_required_string(
                dataset.manifest, "session_id", "dataset manifest"
            ),
            created_at=(
                dataset.manifest.get("created_at")
                if isinstance(dataset.manifest.get("created_at"), str)
                else None
            ),
            sample_count=int(row[0]),
            first_tick=_nullable_int(row[1]),
            last_tick=_nullable_int(row[2]),
            rgb_samples=int(row[3]),
            voxel_samples=int(row[4]),
            player_count=int(row[5]),
            connection_count=int(row[6]),
            size_bytes=sum(item.size_bytes for item in dataset.files.values()),
        )

    def _load_sample_record(self, dataset: _Dataset, sample_id: str) -> dict[str, Any]:
        _validate_opaque_id(sample_id, "sample id")
        self._ensure_index(dataset)
        with self._connect() as database:
            row = database.execute(
                """
                SELECT byte_offset, byte_length FROM samples
                WHERE dataset_id = ? AND sample_id = ?
                """,
                (dataset.dataset_id, sample_id),
            ).fetchone()
        if row is None:
            raise SampleNotFoundError("sample was not found in this dataset")
        offset, length = int(row[0]), int(row[1])
        source = dataset.files["samples.jsonl"]
        if offset < 0 or length <= 0 or offset + length > source.size_bytes:
            raise DatasetValidationError("sample index contains an invalid byte range")
        try:
            with source.path.open("rb") as handle:
                handle.seek(offset)
                line = handle.read(length)
        except OSError as exc:
            raise DatasetValidationError("could not read indexed sample bytes") from exc
        if len(line) != length:
            raise DatasetValidationError("indexed sample bytes are truncated")
        try:
            record = _strict_json_loads(line)
        except (ValueError, RecursionError) as exc:
            raise DatasetValidationError(
                "indexed sample is no longer valid JSON"
            ) from exc
        values = _sample_index_values(record, dataset, offset, length)
        if values[0] != sample_id:
            raise DatasetValidationError(
                "sample index fingerprint no longer matches its source row"
            )
        return record

    def _verified_artifact(
        self,
        dataset: _Dataset,
        modality: dict[str, Any],
        *,
        suffix: str,
        maximum_bytes: int,
        label: str,
        media_type: str,
    ) -> VerifiedArtifact:
        reference = modality.get("reference")
        expected_size = modality.get("artifact_bytes")
        expected_sha = modality.get("artifact_sha256")
        if not isinstance(reference, str) or not reference:
            raise DatasetValidationError(f"{label} artifact reference is missing")
        if (
            not isinstance(expected_size, int)
            or isinstance(expected_size, bool)
            or expected_size < 0
        ):
            raise DatasetValidationError(f"{label} artifact byte size is invalid")
        if expected_size > maximum_bytes:
            raise DatasetValidationError(
                f"{label} artifact exceeds the {maximum_bytes}-byte limit"
            )
        if (
            not isinstance(expected_sha, str)
            or _SHA256_RE.fullmatch(expected_sha) is None
        ):
            raise DatasetValidationError(f"{label} artifact SHA-256 is invalid")
        supplied = Path(reference).expanduser()
        candidate = supplied if supplied.is_absolute() else dataset.directory / supplied
        lexical = Path(os.path.abspath(candidate))
        try:
            resolved = lexical.resolve(strict=True)
            resolved.relative_to(self.exports_root)
        except (OSError, ValueError) as exc:
            raise DatasetValidationError(
                f"{label} artifact escapes the exports root"
            ) from exc

        # Check the supplied lexical route as well as its resolved target. This
        # rejects a symlink under exports without rejecting platform aliases
        # such as macOS /var -> /private/var above the configured root.
        cursor = lexical
        while cursor.resolve(strict=False) != self.exports_root:
            try:
                metadata = cursor.lstat()
            except OSError as exc:
                raise DatasetValidationError(
                    f"{label} artifact does not exist"
                ) from exc
            if stat.S_ISLNK(metadata.st_mode):
                raise DatasetValidationError(
                    f"{label} artifact path may not contain symlinks"
                )
            parent = cursor.parent
            if parent == cursor:
                raise DatasetValidationError(
                    f"{label} artifact is outside the exports root"
                )
            cursor = parent
        if resolved.suffix.lower() != suffix.lower() and not str(
            resolved
        ).lower().endswith(suffix.lower()):
            raise DatasetValidationError(
                f"{label} artifact has an unexpected file type"
            )
        verified = _verify_file(
            resolved, expected_size, expected_sha, f"{label} artifact"
        )
        return VerifiedArtifact(
            path=verified.path,
            size_bytes=verified.size_bytes,
            sha256=verified.sha256,
            media_type=media_type,
        )

    def _read_voxel_snapshot(self, artifact: VerifiedArtifact) -> dict[str, Any]:
        try:
            with artifact.path.open("rb") as raw:
                compressed_bytes = raw.read(self.max_voxel_compressed_bytes + 1)
        except OSError as exc:
            raise DatasetValidationError("voxel artifact became unavailable after validation") from exc
        if len(compressed_bytes) > self.max_voxel_compressed_bytes:
            raise DatasetValidationError(
                f"voxel artifact exceeds the {self.max_voxel_compressed_bytes}-byte limit"
            )
        if len(compressed_bytes) != artifact.size_bytes or not hmac.compare_digest(
            hashlib.sha256(compressed_bytes).hexdigest(), artifact.sha256
        ):
            raise DatasetValidationError("voxel artifact changed after validation")
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(compressed_bytes)) as compressed:
                payload = compressed.read(self.max_voxel_json_bytes + 1)
        except (OSError, EOFError, gzip.BadGzipFile) as exc:
            raise DatasetValidationError(
                "voxel artifact is not a valid gzip stream"
            ) from exc
        if len(payload) > self.max_voxel_json_bytes:
            raise DatasetValidationError(
                f"voxel artifact expands beyond the {self.max_voxel_json_bytes}-byte limit"
            )
        try:
            value = _strict_json_loads(payload)
        except (ValueError, RecursionError) as exc:
            raise DatasetValidationError(
                "voxel artifact contains invalid JSON"
            ) from exc
        if not isinstance(value, dict):
            raise DatasetValidationError("voxel artifact JSON must be an object")
        return value

    def _validate_voxel_snapshot(
        self,
        snapshot: dict[str, Any],
        sample: dict[str, Any],
        modality: dict[str, Any],
    ) -> dict[str, Any]:
        context = "voxel artifact"
        if snapshot.get("schema_version") != 1 or isinstance(
            snapshot.get("schema_version"), bool
        ):
            raise DatasetValidationError(f"{context} schema_version is unsupported")
        if snapshot.get("format") != "mc-recorder-voxel-palette-v1":
            raise DatasetValidationError(f"{context} format is unsupported")
        for key in ("session_id", "player_uuid", "connection_id", "server_tick"):
            if snapshot.get(key) != sample.get(key):
                raise DatasetValidationError(
                    f"{context} {key} does not match the sample"
                )
        if snapshot.get("dimension") != modality.get("dimension"):
            raise DatasetValidationError(
                f"{context} dimension does not match its modality envelope"
            )
        dimension = snapshot.get("dimension")
        if not isinstance(dimension, str) or not dimension:
            raise DatasetValidationError(f"{context} dimension is invalid")
        origin = _voxel_vector(snapshot, "origin")
        shape = _voxel_vector(snapshot, "shape")
        if modality.get("origin") != snapshot.get("origin") or modality.get(
            "shape"
        ) != snapshot.get("shape"):
            raise DatasetValidationError(
                f"{context} shape or origin does not match its modality envelope"
            )
        if any(value <= 0 or value > self.max_voxel_axis for value in shape):
            raise DatasetValidationError(
                f"{context} shape is outside the configured axis bounds"
            )
        total = shape[0] * shape[1] * shape[2]
        if total > self.max_voxel_cells:
            raise DatasetValidationError(f"{context} exceeds the configured cell limit")
        if snapshot.get("total_cells") != total or modality.get("total_cells") != total:
            raise DatasetValidationError(f"{context} total_cells does not match shape")
        if snapshot.get("linear_order") != "x_fastest_then_z_then_y":
            raise DatasetValidationError(f"{context} linear order is unsupported")
        dtype = snapshot.get("index_dtype")
        if (
            dtype not in {"uint16", "uint32"}
            or snapshot.get("index_byte_order") != "little_endian"
        ):
            raise DatasetValidationError(f"{context} index encoding is unsupported")
        width = 2 if dtype == "uint16" else 4
        indices = _decode_base64(
            snapshot.get("indices_base64"), "voxel palette indices"
        )
        if len(indices) != total * width:
            raise DatasetValidationError(
                f"{context} index byte length does not match shape"
            )
        coverage = _decode_base64(
            snapshot.get("coverage_bitset_base64"), "voxel coverage bitset"
        )
        maximum_coverage_bytes = (total + 7) // 8
        if len(coverage) > maximum_coverage_bytes:
            raise DatasetValidationError(f"{context} coverage bitset is too long")
        if coverage and total % 8 and coverage[-1] >> (total % 8):
            raise DatasetValidationError(
                f"{context} coverage bitset contains out-of-range bits"
            )
        if snapshot.get("coverage_bit_order") != "lsb0":
            raise DatasetValidationError(f"{context} coverage bit order is unsupported")
        covered = sum((byte >> bit) & 1 for byte in coverage for bit in range(8))
        if (
            snapshot.get("covered_cells") != covered
            or modality.get("covered_cells") != covered
        ):
            raise DatasetValidationError(
                f"{context} covered_cells does not match its bitset"
            )
        complete = covered == total
        if (
            snapshot.get("coverage_complete") is not complete
            or modality.get("coverage_complete") is not complete
        ):
            raise DatasetValidationError(f"{context} coverage_complete is inconsistent")
        palette = snapshot.get("palette")
        if (
            not isinstance(palette, list)
            or not palette
            or not all(isinstance(item, str) and item for item in palette)
        ):
            raise DatasetValidationError(f"{context} palette is invalid")
        palette_indexes: list[int] = []
        for offset in range(0, len(indices), width):
            palette_index = int.from_bytes(
                indices[offset : offset + width], "little", signed=False
            )
            if palette_index >= len(palette):
                raise DatasetValidationError(
                    f"{context} contains an out-of-range palette index"
                )
            palette_indexes.append(palette_index)
        return {
            "origin": origin,
            "shape": shape,
            "dimension": dimension,
            "covered_cells": covered,
            "total_cells": total,
            "coverage_complete": complete,
            "coverage": coverage,
            "palette": tuple(palette),
            "indices": tuple(palette_indexes),
        }


def _positive_limit(value: int, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise DatasetViewerError(f"{label} must be a positive integer")
    return value


def _allocate_trajectory_budgets(
    point_counts: list[int], maximum: int
) -> list[int]:
    """Allocate a bounded display-point budget while retaining small tracks."""

    if not point_counts:
        return []
    budgets = [0] * len(point_counts)
    minimums = [1 if count == 1 else 2 for count in point_counts]
    minimum_total = sum(minimums)
    if minimum_total > maximum:
        for index in range(min(maximum, len(point_counts))):
            budgets[index] = 1
        return budgets

    budgets = minimums
    remaining = maximum - minimum_total
    capacities = [count - budget for count, budget in zip(point_counts, budgets)]
    capacity_total = sum(capacities)
    if remaining <= 0 or capacity_total <= 0:
        return budgets
    remaining = min(remaining, capacity_total)
    exact = [remaining * capacity / capacity_total for capacity in capacities]
    additions = [
        min(capacity, math.floor(value))
        for capacity, value in zip(capacities, exact)
    ]
    for index, addition in enumerate(additions):
        budgets[index] += addition
    leftover = remaining - sum(additions)
    order = sorted(
        range(len(point_counts)),
        key=lambda index: (
            exact[index] - additions[index],
            capacities[index],
            -index,
        ),
        reverse=True,
    )
    for index in order:
        if leftover <= 0:
            break
        if budgets[index] < point_counts[index]:
            budgets[index] += 1
            leftover -= 1
    return budgets


def _trajectory_target_indexes(point_count: int, budget: int) -> frozenset[int]:
    if point_count <= 0 or budget <= 0:
        return frozenset()
    if point_count <= budget:
        return frozenset(range(point_count))
    if budget == 1:
        return frozenset((0,))
    return frozenset(
        round(index * (point_count - 1) / (budget - 1))
        for index in range(budget)
    )


def _strict_json_loads(value: str | bytes | bytearray) -> object:
    def finite_float(raw: str) -> float:
        parsed = float(raw)
        if not math.isfinite(parsed):
            raise ValueError("non-finite JSON number")
        return parsed

    def reject_constant(raw: str) -> object:
        raise ValueError(f"non-standard JSON constant: {raw}")

    return json.loads(value, parse_float=finite_float, parse_constant=reject_constant)


def _validate_opaque_id(value: str, label: str) -> None:
    if not isinstance(value, str) or _OPAQUE_ID_RE.fullmatch(value) is None:
        raise DatasetViewerError(f"invalid {label}")


def _required_string(mapping: dict[str, Any], key: str, context: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value:
        raise DatasetValidationError(f"{context} {key} must be a non-empty string")
    return value


def _mapping_int(mapping: object, key: str) -> int | None:
    if not isinstance(mapping, dict):
        return None
    value = mapping.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _nullable_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _optional_tick(value: int | None, label: str) -> int | None:
    if value is None:
        return None
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not 0 <= value <= _MAX_SQLITE_INTEGER
    ):
        raise DatasetViewerError(
            f"{label} must be a non-negative signed 64-bit integer"
        )
    return value


def _manifest_modality_records(manifest: dict[str, Any], name: str) -> int | None:
    modalities = manifest.get("modalities")
    if not isinstance(modalities, dict):
        return None
    entry = modalities.get(name)
    return _mapping_int(entry, "records")


def _rgb_presentation(
    selection: object,
    rgb_samples: int,
    *,
    dataset_id: str,
    session_id: str,
) -> str | None:
    if rgb_samples <= 0:
        return None
    attachments = (
        selection.get("frame_attachments") if isinstance(selection, dict) else None
    )
    if not isinstance(attachments, list) or not attachments:
        # V1 results predating presentation provenance were always HUD-free.
        return "hud_free"
    presentations: set[str] = set()
    for attachment in attachments:
        if not isinstance(attachment, dict):
            presentations.add("hud_free")
            continue
        no_gui = attachment.get("no_gui", True)
        if no_gui is False:
            structured_hud_valid = False
            if (
                attachment.get("presentation_contract")
                == FULL_CLIENT_PRESENTATION_CONTRACT
            ):
                try:
                    structured_hud = validate_hud_result_envelope(
                        attachment.get("structured_hud"),
                        "dataset frame attachment structured_hud",
                    )
                    first_tick = attachment.get("global_start_tick")
                    last_tick = attachment.get("global_end_tick")
                    structured_hud_valid = (
                        structured_hud["dataset_id"] == dataset_id
                        and structured_hud["session_id"] == session_id
                        and structured_hud["session_id"]
                        == attachment.get("session_id")
                        and structured_hud["player_uuid"]
                        == attachment.get("player_uuid")
                        and structured_hud["connection_id"]
                        == attachment.get("connection_id")
                        and isinstance(first_tick, int)
                        and not isinstance(first_tick, bool)
                        and isinstance(last_tick, int)
                        and not isinstance(last_tick, bool)
                        and structured_hud["start_server_tick"] <= first_tick
                        and structured_hud["end_server_tick"] >= last_tick
                    )
                except RecorderError:
                    pass
            presentations.add(
                "full_client"
                if structured_hud_valid
                else "legacy_gui_unsynchronized"
            )
        else:
            presentations.add("hud_free")
    if "legacy_gui_unsynchronized" in presentations and len(presentations) > 1:
        return "mixed_legacy_gui_unsynchronized"
    if len(presentations) > 1:
        return "mixed"
    return next(iter(presentations))


def _verify_file(
    path: Path, expected_size: int, expected_sha: str, label: str
) -> _VerifiedFile:
    if path.is_symlink():
        raise DatasetValidationError(f"{label} may not be a symlink")
    try:
        before = path.stat()
    except OSError as exc:
        raise DatasetValidationError(f"cannot stat {label}") from exc
    if not stat.S_ISREG(before.st_mode) or before.st_size != expected_size:
        raise DatasetValidationError(f"{label} size does not match its manifest")
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        after = path.stat()
    except OSError as exc:
        raise DatasetValidationError(f"cannot read {label}") from exc
    if digest.hexdigest().lower() != expected_sha.lower():
        raise DatasetValidationError(f"{label} SHA-256 does not match its manifest")
    if (
        before.st_dev != after.st_dev
        or before.st_ino != after.st_ino
        or before.st_size != after.st_size
        or before.st_mtime_ns != after.st_mtime_ns
        or before.st_ctime_ns != after.st_ctime_ns
    ):
        raise DatasetValidationError(f"{label} changed while it was being verified")
    return _VerifiedFile(
        path=path,
        size_bytes=before.st_size,
        sha256=digest.hexdigest(),
        device=before.st_dev,
        inode=before.st_ino,
        modified_ns=before.st_mtime_ns,
        changed_ns=before.st_ctime_ns,
    )


def _sample_index_values(
    record: object, dataset: _Dataset, offset: int, length: int
) -> tuple[object, ...]:
    context = f"samples.jsonl byte {offset}"
    if not isinstance(record, dict):
        raise DatasetValidationError(f"{context}: sample must be an object")
    if record.get("schema_version") != DATASET_SCHEMA_VERSION or isinstance(
        record.get("schema_version"), bool
    ):
        raise DatasetValidationError(f"{context}: unsupported sample schema_version")
    session_id = _required_string(record, "session_id", context)
    expected_session = _required_string(
        dataset.manifest, "session_id", "dataset manifest"
    )
    if session_id != expected_session:
        raise DatasetValidationError(
            f"{context}: session_id does not match the dataset"
        )
    player_uuid = _required_string(record, "player_uuid", context)
    connection_id = _required_string(record, "connection_id", context)
    server_tick = _required_nonnegative_int(record, "server_tick", context)
    next_server_tick = _required_nonnegative_int(record, "next_server_tick", context)
    sample_key = record.get("sample_key")
    expected_key = {
        "session_id": session_id,
        "server_tick": server_tick,
        "player_uuid": player_uuid,
        "connection_id": connection_id,
    }
    if sample_key != expected_key:
        raise DatasetValidationError(
            f"{context}: sample_key does not match the row identity"
        )
    transition_valid = record.get("transition_valid")
    if not isinstance(transition_valid, bool):
        raise DatasetValidationError(f"{context}: transition_valid must be a boolean")
    reasons = record.get("transition_invalid_reasons")
    if not isinstance(reasons, list) or not all(
        isinstance(item, str) for item in reasons
    ):
        raise DatasetValidationError(
            f"{context}: transition_invalid_reasons must be strings"
        )
    state = record.get("state")
    if not isinstance(state, dict):
        raise DatasetValidationError(f"{context}: state must be an object")
    player_name = (
        state.get("player_name") if isinstance(state.get("player_name"), str) else None
    )
    dimension = (
        state.get("dimension") if isinstance(state.get("dimension"), str) else None
    )
    position = state.get("position")
    coordinates: list[float | None] = [None, None, None]
    if isinstance(position, dict):
        for index, axis in enumerate(("x", "y", "z")):
            value = position.get(axis)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                try:
                    coordinate = float(value)
                except OverflowError as exc:
                    raise DatasetValidationError(
                        f"{context}: position.{axis} is outside finite numeric bounds"
                    ) from exc
                if not math.isfinite(coordinate):
                    raise DatasetValidationError(
                        f"{context}: position.{axis} must be finite"
                    )
                coordinates[index] = coordinate
    action = record.get("action")
    if not isinstance(action, dict):
        raise DatasetValidationError(f"{context}: action must be an object")
    packets = action.get("ordered_packets")
    if not isinstance(packets, list):
        raise DatasetValidationError(
            f"{context}: action.ordered_packets must be an array"
        )
    peers = record.get("peers")
    peer_states = peers.get("state") if isinstance(peers, dict) else None
    peer_count = len(peer_states) if isinstance(peer_states, list) else 0
    rgb = _sample_modality(record, "rgb")
    voxels = _sample_modality(record, "voxels")
    rgb_available = int(rgb.get("available") is True and rgb.get("valid") is True)
    voxel_available = int(
        voxels.get("available") is True and voxels.get("valid") is True
    )
    sample_id_input = (
        f"sample-v1\0{dataset.fingerprint}\0{offset}\0{length}\0{session_id}\0"
        f"{server_tick}\0{player_uuid}\0{connection_id}"
    ).encode()
    sample_id = hashlib.blake2b(sample_id_input, digest_size=16).hexdigest()
    return (
        sample_id,
        offset,
        length,
        session_id,
        server_tick,
        next_server_tick,
        player_uuid,
        player_name,
        connection_id,
        dimension,
        coordinates[0],
        coordinates[1],
        coordinates[2],
        int(transition_valid),
        json.dumps(reasons, separators=(",", ":")),
        len(packets),
        peer_count,
        rgb_available,
        voxel_available,
    )


def _required_nonnegative_int(mapping: dict[str, Any], key: str, context: str) -> int:
    value = mapping.get(key)
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not 0 <= value <= _MAX_SQLITE_INTEGER
    ):
        raise DatasetValidationError(
            f"{context}: {key} must be a non-negative signed 64-bit integer"
        )
    return value


def _sample_modality(record: dict[str, Any], name: str) -> dict[str, Any]:
    modalities = record.get("modalities")
    value = modalities.get(name) if isinstance(modalities, dict) else None
    if not isinstance(value, dict):
        raise DatasetValidationError(f"sample modalities.{name} must be an object")
    available = value.get("available")
    valid = value.get("valid")
    if not isinstance(available, bool) or not isinstance(valid, bool):
        raise DatasetValidationError(
            f"sample modalities.{name} availability flags are invalid"
        )
    return value


def _sample_summary(row: tuple[object, ...]) -> SampleSummary:
    coordinates = row[7:10]
    position = (
        (float(coordinates[0]), float(coordinates[1]), float(coordinates[2]))
        if all(
            isinstance(value, (int, float)) and not isinstance(value, bool)
            for value in coordinates
        )
        else None
    )
    try:
        raw_reasons = json.loads(str(row[11]))
    except ValueError:
        raw_reasons = []
    reasons = (
        tuple(item for item in raw_reasons if isinstance(item, str))
        if isinstance(raw_reasons, list)
        else ()
    )
    return SampleSummary(
        sample_id=str(row[0]),
        server_tick=int(row[1]),
        next_server_tick=int(row[2]),
        player_uuid=str(row[3]),
        player_name=str(row[4]) if row[4] is not None else None,
        connection_id=str(row[5]),
        dimension=str(row[6]) if row[6] is not None else None,
        position=position,
        transition_valid=bool(row[10]),
        transition_invalid_reasons=reasons,
        ordered_packet_count=int(row[12]),
        peer_count=int(row[13]),
        rgb_available=bool(row[14]),
        voxel_available=bool(row[15]),
    )


def _decode_base64(value: object, label: str) -> bytes:
    if not isinstance(value, str):
        raise DatasetValidationError(f"{label} must be a base64 string")
    try:
        return base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise DatasetValidationError(f"{label} is not valid base64") from exc


def _voxel_vector(mapping: dict[str, Any], key: str) -> tuple[int, int, int]:
    value = mapping.get(key)
    if not isinstance(value, dict):
        raise DatasetValidationError(f"voxel artifact {key} must be an object")
    coordinates: list[int] = []
    for axis in ("x", "y", "z"):
        coordinate = value.get(axis)
        if not isinstance(coordinate, int) or isinstance(coordinate, bool):
            raise DatasetValidationError(
                f"voxel artifact {key}.{axis} must be an integer"
            )
        coordinates.append(coordinate)
    return coordinates[0], coordinates[1], coordinates[2]


def _voxel_slice(decoded: dict[str, Any], axis: str, index: int) -> VoxelSlice:
    if axis not in {"x", "y", "z"}:
        raise DatasetViewerError("voxel slice axis must be x, y, or z")
    if not isinstance(index, int) or isinstance(index, bool):
        raise DatasetViewerError("voxel slice index must be an integer")
    origin = decoded["origin"]
    shape = decoded["shape"]
    axis_index = {"x": 0, "y": 1, "z": 2}[axis]
    if not 0 <= index < shape[axis_index]:
        raise DatasetViewerError(
            f"voxel slice index is outside axis size {shape[axis_index]}"
        )
    if axis == "x":
        row_axis, column_axis = "y", "z"
        coordinates: Iterable[Iterable[tuple[int, int, int]]] = (
            ((index, y, z) for z in range(shape[2])) for y in range(shape[1])
        )
    elif axis == "y":
        row_axis, column_axis = "z", "x"
        coordinates = (
            ((x, index, z) for x in range(shape[0])) for z in range(shape[2])
        )
    else:
        row_axis, column_axis = "y", "x"
        coordinates = (
            ((x, y, index) for x in range(shape[0])) for y in range(shape[1])
        )

    coverage: bytes = decoded["coverage"]
    palette: tuple[str, ...] = decoded["palette"]
    indices: tuple[int, ...] = decoded["indices"]
    rows: list[tuple[VoxelCell, ...]] = []
    for coordinate_row in coordinates:
        cells: list[VoxelCell] = []
        for x, y, z in coordinate_row:
            linear = x + shape[0] * (z + shape[2] * y)
            covered = linear // 8 < len(coverage) and bool(
                coverage[linear // 8] & (1 << (linear % 8))
            )
            palette_index = indices[linear] if covered else None
            cells.append(
                VoxelCell(
                    x=origin[0] + x,
                    y=origin[1] + y,
                    z=origin[2] + z,
                    covered=covered,
                    block_state=palette[palette_index]
                    if palette_index is not None
                    else None,
                    palette_index=palette_index,
                )
            )
        rows.append(tuple(cells))
    slice_covered = sum(cell.covered for row in rows for cell in row)
    slice_total = sum(len(row) for row in rows)
    return VoxelSlice(
        axis=axis,
        index=index,
        world_coordinate=origin[axis_index] + index,
        row_axis=row_axis,
        column_axis=column_axis,
        origin=origin,
        shape=shape,
        dimension=decoded["dimension"],
        covered_cells=slice_covered,
        total_cells=slice_total,
        coverage_complete=slice_covered == slice_total,
        cells=tuple(rows),
    )
