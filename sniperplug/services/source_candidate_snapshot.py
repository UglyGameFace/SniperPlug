from __future__ import annotations

from dataclasses import asdict, fields
import json
from typing import Any

from sniperplug.models.candidate import SourceCandidate


_CANDIDATE_FIELDS = {field.name for field in fields(SourceCandidate)}


def serialize_source_candidate(candidate: SourceCandidate) -> str:
    """Serialize a source candidate for durable cross-process delivery."""

    return json.dumps(
        asdict(candidate),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def deserialize_source_candidate(value: str | bytes | bytearray | dict[str, Any]) -> SourceCandidate | None:
    """Load a candidate while tolerating newer optional fields safely.

    Required identity fields still flow through ``SourceCandidate`` validation;
    malformed snapshots fail closed instead of creating a partial public alert.
    """

    try:
        payload = json.loads(value) if isinstance(value, (str, bytes, bytearray)) else dict(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None

    cleaned = {key: payload[key] for key in _CANDIDATE_FIELDS if key in payload}
    required = ("source_key", "retailer", "title", "product_url")
    if any(not str(cleaned.get(key) or "").strip() for key in required):
        return None
    try:
        return SourceCandidate(**cleaned)
    except (TypeError, ValueError):
        return None
