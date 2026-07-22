from __future__ import annotations

import re
from typing import Any

from .errors import RecorderError

UNSUPPORTED_PACKET_POLICY = "ignore_flashback_unsupported_v1"
MAX_UNSUPPORTED_PACKET_TYPES = 256
MAX_UNSUPPORTED_PACKET_COUNT = 2**63 - 1
_PACKET_TYPE_RE = re.compile(r"^[a-z0-9_.-]+:[a-z0-9_./-]+$")


def validate_unsupported_packet_summary(
    value: object, label: str = "renderer unsupported_packets"
) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"policy", "total_count", "types"}:
        raise RecorderError(f"{label} has invalid fields")
    if value.get("policy") != UNSUPPORTED_PACKET_POLICY:
        raise RecorderError(f"{label} has an unsupported policy")
    total = value.get("total_count")
    if (
        not isinstance(total, int)
        or isinstance(total, bool)
        or not 0 <= total <= MAX_UNSUPPORTED_PACKET_COUNT
    ):
        raise RecorderError(f"{label} total_count is invalid")
    types = value.get("types")
    if not isinstance(types, list) or len(types) > MAX_UNSUPPORTED_PACKET_TYPES:
        raise RecorderError(f"{label} types are invalid")

    normalized_types: list[dict[str, Any]] = []
    previous = ""
    observed_total = 0
    for index, entry in enumerate(types):
        entry_label = f"{label} types[{index}]"
        if not isinstance(entry, dict) or set(entry) != {"packet_type", "count"}:
            raise RecorderError(f"{entry_label} has invalid fields")
        packet_type = entry.get("packet_type")
        count = entry.get("count")
        if (
            not isinstance(packet_type, str)
            or len(packet_type) > 256
            or _PACKET_TYPE_RE.fullmatch(packet_type) is None
            or packet_type <= previous
        ):
            raise RecorderError(f"{entry_label} packet_type is invalid or unsorted")
        if (
            not isinstance(count, int)
            or isinstance(count, bool)
            or not 1 <= count <= MAX_UNSUPPORTED_PACKET_COUNT
        ):
            raise RecorderError(f"{entry_label} count is invalid")
        observed_total += count
        if observed_total > MAX_UNSUPPORTED_PACKET_COUNT:
            raise RecorderError(f"{label} count exceeds the supported range")
        normalized_types.append({"packet_type": packet_type, "count": count})
        previous = packet_type
    if observed_total != total:
        raise RecorderError(f"{label} total_count does not match its packet counts")
    return {
        "policy": UNSUPPORTED_PACKET_POLICY,
        "total_count": total,
        "types": normalized_types,
    }


__all__ = [
    "UNSUPPORTED_PACKET_POLICY",
    "validate_unsupported_packet_summary",
]
