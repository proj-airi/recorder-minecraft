from __future__ import annotations

import json
import secrets
import sqlite3
import tempfile
import threading
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import fields, is_dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

from minerec.errors import RecorderError
from minerec.processing.bundle import OpenedBundle, open_bundle
from minerec.processing.bundle.finalizer import validate_fpv_render
from minerec.processing.scene.store_v2 import SceneStoreV2, validate_scene_store_v2

MAX_ACTION_LINE_BYTES = 32 * 1024 * 1024
MAX_TIMELINE_LINE_BYTES = 1024 * 1024
MAX_ACTION_RESULTS = 1_000
MAX_TIMELINE_RESULTS = 2_000
MAX_TRAJECTORY_POINTS = 10_000
MAX_SLICE_RADIUS = 64


def _strict_json(raw: bytes, *, label: str) -> Any:  # noqa: ANN401
    try:
        return json.loads(
            raw,
            parse_constant=lambda token: (_ for _ in ()).throw(ValueError(f"non-finite {token}")),
        )
    except (ValueError, UnicodeDecodeError, RecursionError) as exc:
        raise RecorderError(f"{label} contains invalid JSON") from exc


def _json_value(value: Any) -> Any:  # noqa: ANN401
    if is_dataclass(value):
        return {field.name: _json_value(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, Path):
        return value.as_posix()
    return value


def _bounded_int(
    value: int | None,
    *,
    name: str,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    result = default if value is None else value
    if not isinstance(result, int) or isinstance(result, bool) or not minimum <= result <= maximum:
        raise RecorderError(f"{name} must be between {minimum} and {maximum}")
    return result


def _iter_lines(path: Path, *, maximum_line_bytes: int, label: str) -> Iterator[tuple[int, int, bytes]]:
    offset = 0
    with path.open("rb") as handle:
        while raw := handle.readline(maximum_line_bytes + 1):
            if len(raw) > maximum_line_bytes:
                raise RecorderError(f"{label} line exceeds {maximum_line_bytes} bytes")
            length = len(raw)
            if not raw.endswith(b"\n"):
                raise RecorderError(f"{label} must end each record with a newline")
            yield offset, length, raw
            offset += length


class _RecordIndex:
    def __init__(self, directory: Path, actions_path: Path, timeline_path: Path | None) -> None:
        self.actions_path = actions_path
        self.timeline_path = timeline_path
        self.path = directory / "viewer-index.sqlite3"
        database = sqlite3.connect(self.path)
        try:
            database.executescript(
                """
                PRAGMA journal_mode = DELETE;
                PRAGMA synchronous = FULL;
                CREATE TABLE actions (
                    ordinal INTEGER PRIMARY KEY,
                    server_tick INTEGER NOT NULL,
                    sequence INTEGER,
                    byte_offset INTEGER NOT NULL,
                    byte_length INTEGER NOT NULL
                );
                CREATE INDEX actions_tick_sequence
                    ON actions(server_tick, sequence, ordinal);
                CREATE TABLE timeline (
                    frame_index INTEGER PRIMARY KEY,
                    server_tick INTEGER NOT NULL,
                    byte_offset INTEGER NOT NULL,
                    byte_length INTEGER NOT NULL
                );
                """
            )
            actions: list[tuple[int, int, int | None, int, int]] = []
            for ordinal, (offset, length, raw) in enumerate(_iter_lines(actions_path, maximum_line_bytes=MAX_ACTION_LINE_BYTES, label="actions.jsonl")):
                record = _strict_json(raw, label=f"actions.jsonl record {ordinal}")
                if not isinstance(record, dict):
                    raise RecorderError(f"actions.jsonl record {ordinal} must be an object")
                tick = record.get("server_tick")
                sequence = record.get("sequence")
                if not isinstance(tick, int) or isinstance(tick, bool):
                    raise RecorderError(f"actions.jsonl record {ordinal} lacks integer server_tick")
                if sequence is not None and (not isinstance(sequence, int) or isinstance(sequence, bool)):
                    raise RecorderError(f"actions.jsonl record {ordinal} has invalid sequence")
                actions.append((ordinal, tick, sequence, offset, length))
                if len(actions) >= 4_096:
                    database.executemany("INSERT INTO actions VALUES (?, ?, ?, ?, ?)", actions)
                    actions.clear()
            if actions:
                database.executemany("INSERT INTO actions VALUES (?, ?, ?, ?, ?)", actions)

            if timeline_path is not None:
                frames: list[tuple[int, int, int, int]] = []
                for ordinal, (offset, length, raw) in enumerate(
                    _iter_lines(
                        timeline_path,
                        maximum_line_bytes=MAX_TIMELINE_LINE_BYTES,
                        label="fpv.timeline.jsonl",
                    )
                ):
                    record = _strict_json(raw, label=f"fpv.timeline.jsonl record {ordinal}")
                    if not isinstance(record, dict):
                        raise RecorderError(f"fpv.timeline.jsonl record {ordinal} must be an object")
                    frame_index = record.get("frame_index", ordinal)
                    tick = record.get("server_tick")
                    if not isinstance(frame_index, int) or isinstance(frame_index, bool) or frame_index != ordinal:
                        raise RecorderError("fpv.timeline.jsonl frame_index must be contiguous from zero")
                    if not isinstance(tick, int) or isinstance(tick, bool):
                        raise RecorderError(f"fpv.timeline.jsonl record {ordinal} lacks integer server_tick")
                    frames.append((frame_index, tick, offset, length))
                    if len(frames) >= 4_096:
                        database.executemany("INSERT INTO timeline VALUES (?, ?, ?, ?)", frames)
                        frames.clear()
                if frames:
                    database.executemany("INSERT INTO timeline VALUES (?, ?, ?, ?)", frames)
            database.commit()
        finally:
            database.close()

    def actions(self, *, from_tick: int | None, to_tick: int | None, limit: int | None) -> dict[str, Any]:
        page_size = _bounded_int(
            limit,
            name="limit",
            default=200,
            minimum=1,
            maximum=MAX_ACTION_RESULTS,
        )
        clauses: list[str] = []
        parameters: list[object] = []
        if from_tick is not None:
            clauses.append("server_tick >= ?")
            parameters.append(from_tick)
        if to_tick is not None:
            clauses.append("server_tick <= ?")
            parameters.append(to_tick)
        if from_tick is not None and to_tick is not None and from_tick > to_tick:
            raise RecorderError("from_tick cannot be greater than to_tick")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with sqlite3.connect(f"file:{self.path}?mode=ro&immutable=1", uri=True) as database:
            rows = database.execute(
                f"""
                SELECT byte_offset, byte_length FROM actions
                {where}
                ORDER BY server_tick, COALESCE(sequence, 9223372036854775807), ordinal
                LIMIT ?
                """,
                [*parameters, page_size + 1],
            ).fetchall()
        truncated = len(rows) > page_size
        records: list[dict[str, Any]] = []
        with self.actions_path.open("rb") as handle:
            for offset, length in rows[:page_size]:
                handle.seek(offset)
                record = _strict_json(handle.read(length), label="indexed action")
                if not isinstance(record, dict):
                    raise RecorderError("indexed action is not an object")
                records.append(record)
        return {"actions": records, "truncated": truncated}

    def timeline(self, *, from_frame: int | None, limit: int | None) -> dict[str, Any]:
        if self.timeline_path is None:
            raise RecorderError("bundle has no FPV render timeline")
        start = _bounded_int(from_frame, name="from_frame", default=0, minimum=0, maximum=2**31 - 1)
        page_size = _bounded_int(
            limit,
            name="limit",
            default=500,
            minimum=1,
            maximum=MAX_TIMELINE_RESULTS,
        )
        with sqlite3.connect(f"file:{self.path}?mode=ro&immutable=1", uri=True) as database:
            rows = database.execute(
                """
                SELECT frame_index, byte_offset, byte_length FROM timeline
                WHERE frame_index >= ? ORDER BY frame_index LIMIT ?
                """,
                (start, page_size + 1),
            ).fetchall()
        records: list[dict[str, Any]] = []
        with self.timeline_path.open("rb") as handle:
            for _frame_index, offset, length in rows[:page_size]:
                handle.seek(offset)
                record = _strict_json(handle.read(length), label="indexed render timeline")
                if not isinstance(record, dict):
                    raise RecorderError("indexed render timeline row is not an object")
                records.append(record)
        next_frame = int(rows[page_size][0]) if len(rows) > page_size else None
        return {"frames": records, "next_frame": next_frame}


class ViewerBundle:
    def __init__(self, opened: OpenedBundle) -> None:
        self.opened = opened
        self._temporary = tempfile.TemporaryDirectory(prefix="minerec-viewer-index-")
        self._lease_lock = threading.Lock()
        self._lease_count = 0
        self._close_pending = False
        self._closed = False
        try:
            self.index = _RecordIndex(
                Path(self._temporary.name),
                opened.actions_path,
                opened.timeline_path,
            )
        except BaseException:
            self._temporary.cleanup()
            opened.close()
            raise

    def _close_resources(self) -> None:
        self.opened.close()
        self._temporary.cleanup()

    def close(self) -> None:
        close_now = False
        with self._lease_lock:
            if self._closed or self._close_pending:
                return
            if self._lease_count:
                self._close_pending = True
            else:
                self._closed = True
                close_now = True
        if close_now:
            self._close_resources()

    def acquire_lease(self) -> None:
        with self._lease_lock:
            if self._closed or self._close_pending:
                raise RecorderError("play bundle is closing")
            self._lease_count += 1

    def release_lease(self) -> None:
        close_now = False
        with self._lease_lock:
            if self._lease_count <= 0:
                raise RuntimeError("play bundle lease underflow")
            self._lease_count -= 1
            if self._lease_count == 0 and self._close_pending:
                self._close_pending = False
                self._closed = True
                close_now = True
        if close_now:
            self._close_resources()

    def summary(self) -> dict[str, Any]:
        metadata = _json_value(self.opened.metadata)
        return {
            "bundle_id": self.opened.bundle_id,
            "metadata": metadata,
            "has_render": self.opened.fpv_path is not None,
            "replays": metadata["replays"],
        }

    def tick(self, tick: int) -> dict[str, Any]:
        with SceneStoreV2(self.opened.scene_path) as store:
            return {
                "frame": _json_value(store.frame(tick)),
                "state": _json_value(store.player_state(tick)),
            }

    def trajectory(self, *, max_points: int | None) -> dict[str, Any]:
        limit = _bounded_int(
            max_points,
            name="max_points",
            default=2_400,
            minimum=2,
            maximum=MAX_TRAJECTORY_POINTS,
        )
        uri = f"file:{quote(str(self.opened.scene_path), safe='/')}?mode=ro&immutable=1"
        with sqlite3.connect(uri, uri=True) as database:
            database.row_factory = sqlite3.Row
            total = int(database.execute("SELECT COUNT(*) FROM player_states").fetchone()[0])
            if total == 0:
                return {
                    "points": [],
                    "total_points": 0,
                    "returned_points": 0,
                    "truncated": False,
                }

            # Preserve both sides of dimension/tick discontinuities when they fit.
            # Sampling happens by source ordinal, so duration does not affect memory.
            break_rows = database.execute(
                """
                WITH ordered AS (
                    SELECT ROW_NUMBER() OVER (ORDER BY server_tick) AS ordinal,
                           server_tick, dimension,
                           LAG(server_tick) OVER (ORDER BY server_tick) AS previous_tick,
                           LAG(dimension) OVER (ORDER BY server_tick) AS previous_dimension
                    FROM player_states
                )
                SELECT ordinal FROM ordered
                WHERE previous_tick IS NOT NULL
                  AND (server_tick != previous_tick + 1 OR dimension != previous_dimension)
                ORDER BY ordinal LIMIT ?
                """,
                (limit + 1,),
            ).fetchall()
            break_candidates = {1, total}
            for row in break_rows:
                ordinal = int(row[0])
                break_candidates.update((max(1, ordinal - 1), ordinal))
            preserve_all_breaks = len(break_rows) <= limit and len(break_candidates) <= limit
            selected = break_candidates if preserve_all_breaks else {1, total}
            remaining = limit - len(selected)
            if remaining > 0 and total > len(selected):
                for index in range(1, remaining + 1):
                    selected.add(1 + ((total - 1) * index // (remaining + 1)))
            if len(selected) > limit:
                interior = sorted(selected - {1, total})
                keep = max(0, limit - min(2, total))
                if keep < len(interior):
                    interior = [interior[(len(interior) - 1) * index // max(1, keep - 1)] for index in range(keep)] if keep else []
                selected = {1, total, *interior}

            ordinals = sorted(selected)
            placeholders = ",".join("?" for _ in ordinals)
            rows = database.execute(
                f"""
                WITH ordered AS (
                    SELECT ROW_NUMBER() OVER (ORDER BY server_tick) AS ordinal,
                           server_tick, dimension,
                           position_x, position_y, position_z,
                           entity_instance_id
                    FROM player_states
                )
                SELECT * FROM ordered WHERE ordinal IN ({placeholders}) ORDER BY ordinal
                """,
                ordinals,
            ).fetchall()
        points = [
            {
                "tick": int(row["server_tick"]),
                "dimension": row["dimension"],
                "position": [row["position_x"], row["position_y"], row["position_z"]],
                "entity_instance_id": row["entity_instance_id"],
                "source_ordinal": int(row["ordinal"]),
            }
            for row in rows
        ]
        return {
            "points": points,
            "total_points": total,
            "returned_points": len(points),
            "truncated": len(points) < total or not preserve_all_breaks,
        }

    def scene_slice(self, *, tick: int, dimension: str | None, y: int | None, radius: int | None) -> Any:  # noqa: ANN401
        slice_radius = _bounded_int(
            radius,
            name="radius",
            default=24,
            minimum=1,
            maximum=MAX_SLICE_RADIUS,
        )
        with SceneStoreV2(self.opened.scene_path) as store:
            frame = store.frame(tick)
            state = store.player_state(tick)
            state_dimension = state.dimension
            if dimension is not None and dimension != state_dimension:
                raise RecorderError("requested dimension does not match player state")
            position_x, position_y, position_z = state.position
            coordinate = int(position_y // 1) if y is None else y
            result = store.slice(
                frame.frame_id,
                axis="y",
                coordinate=coordinate,
                center=(int(position_x // 1), int(position_y // 1), int(position_z // 1)),
                radius=slice_radius,
            )
            return _json_value(result)


class ViewerService:
    """Atomic active/staged bundle holder with leased render-media access."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._bundle: ViewerBundle | None = None
        self._staged: tuple[str, ViewerBundle] | None = None

    def close(self) -> None:
        with self._lock:
            current, self._bundle = self._bundle, None
            staged, self._staged = self._staged, None
        for bundle in (current, staged[1] if staged is not None else None):
            if bundle is not None:
                bundle.close()

    def import_bundle(self, path: Path) -> dict[str, Any]:
        staged = self.stage_bundle(path)
        return self.commit_staged(str(staged["staged_import_id"]))

    def stage_bundle(self, path: Path) -> dict[str, Any]:
        opened = open_bundle(
            path,
            scene_validator=validate_scene_store_v2,
            render_validator=validate_fpv_render,
        )
        candidate = ViewerBundle(opened)
        staged_import_id = secrets.token_urlsafe(24)
        with self._lock:
            previous, self._staged = self._staged, (staged_import_id, candidate)
        if previous is not None:
            previous[1].close()
        return {
            "staged_import_id": staged_import_id,
            "bundle": candidate.summary(),
        }

    def commit_staged(self, staged_import_id: str) -> dict[str, Any]:
        with self._lock:
            if self._staged is None or self._staged[0] != staged_import_id:
                raise RecorderError("staged play-bundle import was not found")
            candidate = self._staged[1]
            self._staged = None
            previous, self._bundle = self._bundle, candidate
        if previous is not None:
            previous.close()
        return candidate.summary()

    def discard_staged(self, staged_import_id: str) -> None:
        with self._lock:
            if self._staged is None or self._staged[0] != staged_import_id:
                raise RecorderError("staged play-bundle import was not found")
            candidate = self._staged[1]
            self._staged = None
        candidate.close()

    def with_bundle(
        self,
        operation: Callable[[ViewerBundle], Any],
        *,
        staged_import_id: str | None = None,
    ) -> Any:  # noqa: ANN401
        with self._lock:
            if staged_import_id is not None:
                if self._staged is None or self._staged[0] != staged_import_id:
                    raise RecorderError("staged play-bundle import was not found")
                return operation(self._staged[1])
            if self._bundle is None:
                raise RecorderError("no play bundle is open")
            return operation(self._bundle)

    def summary(self, *, staged_import_id: str | None = None) -> dict[str, Any]:
        return self.with_bundle(lambda bundle: bundle.summary(), staged_import_id=staged_import_id)

    def actions(self, *, staged_import_id: str | None = None, **filters: Any) -> dict[str, Any]:
        return self.with_bundle(
            lambda bundle: bundle.index.actions(**filters),
            staged_import_id=staged_import_id,
        )

    def tick(self, tick: int, *, staged_import_id: str | None = None) -> dict[str, Any]:
        return self.with_bundle(lambda bundle: bundle.tick(tick), staged_import_id=staged_import_id)

    def trajectory(
        self,
        *,
        max_points: int | None,
        staged_import_id: str | None = None,
    ) -> dict[str, Any]:
        return self.with_bundle(
            lambda bundle: bundle.trajectory(max_points=max_points),
            staged_import_id=staged_import_id,
        )

    def scene_slice(self, *, staged_import_id: str | None = None, **query: Any) -> Any:  # noqa: ANN401
        return self.with_bundle(
            lambda bundle: bundle.scene_slice(**query),
            staged_import_id=staged_import_id,
        )

    def replays(self, *, staged_import_id: str | None = None) -> dict[str, Any]:
        return self.with_bundle(
            lambda bundle: {"replays": _json_value(bundle.opened.metadata["replays"])},
            staged_import_id=staged_import_id,
        )

    def timeline(
        self,
        *,
        from_frame: int | None,
        limit: int | None,
        staged_import_id: str | None = None,
    ) -> dict[str, Any]:
        return self.with_bundle(
            lambda bundle: bundle.index.timeline(from_frame=from_frame, limit=limit),
            staged_import_id=staged_import_id,
        )

    @contextmanager
    def render_lease(self, bundle_id: str) -> Iterator[Path]:
        with self._lock:
            bundle = self._bundle
            if bundle is None:
                raise RecorderError("no play bundle is open")
            if bundle.opened.bundle_id != bundle_id:
                raise RecorderError("requested render does not belong to the active bundle")
            if bundle.opened.fpv_path is None:
                raise RecorderError("bundle has no FPV render")
            bundle.acquire_lease()
            path = bundle.opened.fpv_path
        try:
            yield path
        finally:
            bundle.release_lease()
