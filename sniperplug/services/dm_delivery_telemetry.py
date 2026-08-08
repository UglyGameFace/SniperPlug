from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any


_REJECTION_BUCKETS = (
    "disabled",
    "proof",
    "price",
    "cash",
    "keywords",
    "category",
    "discount",
    "score",
    "savings",
    "other",
)


@dataclass(frozen=True)
class UserDmDeliveryTelemetry:
    user_id: int
    checked: int = 0
    matched: int = 0
    rejected_filter: int = 0
    rejected_disabled: int = 0
    rejected_proof: int = 0
    rejected_price: int = 0
    rejected_cash: int = 0
    rejected_keywords: int = 0
    rejected_category: int = 0
    rejected_discount: int = 0
    rejected_score: int = 0
    rejected_savings: int = 0
    rejected_other: int = 0
    skipped_location: int = 0
    skipped_duplicate: int = 0
    skipped_daily_cap: int = 0
    sent: int = 0
    disabled: int = 0
    errors: int = 0
    last_event_key: str = ""
    last_event_at: str = ""
    last_filter_reason: str = ""
    last_outcome: str = ""

    def summary_lines(self) -> tuple[str, ...]:
        if self.checked <= 0:
            return (
                "No exact deal events have been evaluated for your personal filter since this bot process started.",
            )
        reasons = []
        for bucket in _REJECTION_BUCKETS:
            value = int(getattr(self, f"rejected_{bucket}"))
            if value:
                reasons.append(f"{bucket}: {value}")
        lines = [
            f"Exact events checked: **{self.checked}**",
            f"Matched your filter: **{self.matched}**",
            f"Sent: **{self.sent}**",
            f"Filter rejects: **{self.rejected_filter}**"
            + (f" ({', '.join(reasons)})" if reasons else ""),
            (
                "Other skips — location: **"
                f"{self.skipped_location}**, already sent: **{self.skipped_duplicate}**, "
                f"daily cap: **{self.skipped_daily_cap}**"
            ),
            f"Closed/unavailable DMs: **{self.disabled}** • delivery errors: **{self.errors}**",
        ]
        if self.last_filter_reason:
            lines.append(f"Last filter rejection: `{self.last_filter_reason[:180]}`")
        if self.last_outcome:
            lines.append(f"Last outcome: **{self.last_outcome}**")
        return tuple(lines)


_USER_TELEMETRY: dict[int, UserDmDeliveryTelemetry] = {}


def dm_rejection_bucket(reason: str) -> str:
    text = str(reason or "").strip().lower()
    if "alerts are disabled" in text:
        return "disabled"
    if "proof" in text or "markdown is not positive" in text:
        return "proof"
    if "above your maximum" in text:
        return "price"
    if "walmart cash" in text:
        return "cash"
    if "keyword" in text or "required keywords" in text:
        return "keywords"
    if "category" in text:
        return "category"
    if "% is below" in text or ("required" in text and "%" in text):
        return "discount"
    if text.startswith("score ") or ("score" in text and "below" in text):
        return "score"
    if text.startswith("saves $") or "savings" in text:
        return "savings"
    return "other"


def get_user_dm_delivery_telemetry(user_id: int) -> UserDmDeliveryTelemetry:
    key = int(user_id)
    return _USER_TELEMETRY.get(key, UserDmDeliveryTelemetry(user_id=key))


def clear_user_dm_delivery_telemetry(user_id: int) -> None:
    _USER_TELEMETRY.pop(int(user_id), None)


def record_user_dm_decision(
    user_id: int,
    *,
    event_key: str,
    decision: Any | None = None,
    location_skipped: bool = False,
) -> None:
    key = int(user_id)
    current = get_user_dm_delivery_telemetry(key)
    now = datetime.now(timezone.utc).isoformat()
    updates: dict[str, Any] = {
        "checked": current.checked + 1,
        "last_event_key": str(event_key or "")[:300],
        "last_event_at": now,
    }
    if location_skipped:
        updates["skipped_location"] = current.skipped_location + 1
        updates["last_outcome"] = "location skipped"
    elif bool(getattr(decision, "matched", False)):
        updates["matched"] = current.matched + 1
        updates["last_outcome"] = "matched filter"
    else:
        reason = str(
            getattr(decision, "reason", "unknown filter rejection")
            or "unknown filter rejection"
        )
        bucket = dm_rejection_bucket(reason)
        updates["rejected_filter"] = current.rejected_filter + 1
        updates[f"rejected_{bucket}"] = getattr(current, f"rejected_{bucket}") + 1
        updates["last_filter_reason"] = reason[:500]
        updates["last_outcome"] = f"filter rejected: {bucket}"
    _USER_TELEMETRY[key] = replace(current, **updates)


def record_user_dm_outcome(user_id: int, outcome: str) -> None:
    key = int(user_id)
    current = get_user_dm_delivery_telemetry(key)
    normalized = str(outcome or "").strip().lower()
    updates: dict[str, Any] = {
        "last_outcome": normalized or current.last_outcome,
    }
    if normalized == "duplicate":
        updates["skipped_duplicate"] = current.skipped_duplicate + 1
    elif normalized == "daily_cap":
        updates["skipped_daily_cap"] = current.skipped_daily_cap + 1
    elif normalized == "sent":
        updates["sent"] = current.sent + 1
    elif normalized == "disabled":
        updates["disabled"] = current.disabled + 1
    elif normalized == "error":
        updates["errors"] = current.errors + 1
    _USER_TELEMETRY[key] = replace(current, **updates)
