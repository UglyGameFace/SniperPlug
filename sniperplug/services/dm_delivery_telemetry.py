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


@dataclass
class DmDeliveryCounters:
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

    def record_rejection(self, reason: str) -> str:
        bucket = dm_rejection_bucket(reason)
        self.rejected_filter += 1
        setattr(self, f"rejected_{bucket}", getattr(self, f"rejected_{bucket}") + 1)
        return bucket

    def merge(self, other: "DmDeliveryCounters") -> None:
        for field_name in self.__dataclass_fields__:
            setattr(self, field_name, int(getattr(self, field_name)) + int(getattr(other, field_name)))

    def summary_fragment(self) -> str:
        reason_bits = []
        for bucket in _REJECTION_BUCKETS:
            value = int(getattr(self, f"rejected_{bucket}"))
            if value:
                reason_bits.append(f"{bucket} **{value}**")
        reason_text = " • ".join(reason_bits) if reason_bits else "none"
        return (
            f"DM checked/matched/sent **{self.checked}/{self.matched}/{self.sent}** • "
            f"filter skips **{self.rejected_filter}** ({reason_text}) • "
            f"location/duplicate/cap **{self.skipped_location}/{self.skipped_duplicate}/{self.skipped_daily_cap}** • "
            f"disabled/errors **{self.disabled}/{self.errors}**"
        )

    def result_kwargs(self) -> dict[str, int]:
        return {
            "dm_preferences_checked": self.checked,
            "dm_matches": self.matched,
            "dm_rejected_filter": self.rejected_filter,
            "dm_rejected_disabled": self.rejected_disabled,
            "dm_rejected_proof": self.rejected_proof,
            "dm_rejected_price": self.rejected_price,
            "dm_rejected_cash": self.rejected_cash,
            "dm_rejected_keywords": self.rejected_keywords,
            "dm_rejected_category": self.rejected_category,
            "dm_rejected_discount": self.rejected_discount,
            "dm_rejected_score": self.rejected_score,
            "dm_rejected_savings": self.rejected_savings,
            "dm_rejected_other": self.rejected_other,
            "dm_skipped_location": self.skipped_location,
            "dm_skipped_duplicate": self.skipped_duplicate,
            "dm_skipped_daily_cap": self.skipped_daily_cap,
            "dm_sent": self.sent,
            "dm_disabled": self.disabled,
            "dm_errors": self.errors,
        }


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
            f"Filter rejects: **{self.rejected_filter}**" + (f" ({', '.join(reasons)})" if reasons else ""),
            (
                "Other skips — location: **"
                f"{self.skipped_location}**, already sent: **{self.skipped_duplicate}**, daily cap: **{self.skipped_daily_cap}**"
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
    if "% is below" in text or "required" in text and "%" in text:
        return "discount"
    if text.startswith("score ") or "score" in text and "below" in text:
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
        reason = str(getattr(decision, "reason", "unknown filter rejection") or "unknown filter rejection")
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
    updates: dict[str, Any] = {"last_outcome": normalized or current.last_outcome}
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
