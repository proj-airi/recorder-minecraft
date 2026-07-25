from __future__ import annotations

import json
from pathlib import Path
from typing import Any

PLAYER = "00000000-0000-4000-8000-000000000001"
CONNECTION = "00000000-0000-4000-8000-000000000002"
SESSION = "session-a"


def event(record_type: str, tick: int, sequence: int, **values: Any) -> dict[str, Any]:
    record: dict[str, Any] = {
        "schema_version": 1,
        "record_type": record_type,
        "session_id": SESSION,
        "server_tick": tick,
        "sequence": sequence,
        "player_uuid": PLAYER,
        "connection_id": CONNECTION,
    }
    record.update(values)
    return record


def completed_capture(
    root: Path,
    records: list[dict[str, Any]],
    *,
    player_uuid: str = PLAYER,
    connection_id: str = CONNECTION,
    session_id: str = SESSION,
) -> tuple[Path, Path]:
    if not records:
        raise ValueError("capture fixture requires at least one event")
    capture = root / "capture"
    capture.mkdir(parents=True)
    events = capture / "events.jsonl"
    events.write_bytes(b"".join(json.dumps(record, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n" for record in records))
    ticks = [int(record["server_tick"]) for record in records]
    metadata = root / "metadata.json"
    metadata.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "layout_version": "v1",
                "session_id": session_id,
                "server": {
                    "name": "minecraft",
                    "instance_id": "00000000-0000-4000-8000-000000000010",
                },
                "player": {"name": "RecorderPlayer", "uuid": player_uuid},
                "connection": {
                    "id": connection_id,
                    "started_at": "2026-01-01T00:00:00Z",
                    "start_server_tick": min(ticks),
                    "ended_at": "2026-01-01T00:00:01Z",
                    "end_server_tick": max(ticks),
                    "terminal_reason": "disconnect",
                },
                "capture": {
                    "events": "capture/events.jsonl",
                    "replay": "capture/replay.zip",
                    "replay_format": "flashback",
                },
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return metadata, events
