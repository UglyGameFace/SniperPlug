from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sniperplug.services import walmart_global_deal_fanout as legacy
from sniperplug.services.deal_category_preferences import decide_category
from sniperplug.services.discord_snowflake import snowflake_text
from sniperplug.services.public_deal_posts import card_deal_key
from sniperplug.services.public_deal_quality import (
    is_public_deal_candidate,
    structured_discount,
)
from sniperplug.services.walmart_exact_verification_queue import (
    _candidate_from_snapshot,
)


DEFAULT_WINDOW_HOURS = 24
DEFAULT_EVENT_LIMIT = 80
SOURCE_LABEL = "global_catalog_autoscan:exact_verified"


@dataclass(frozen=True)
class WalmartDeliveryDecision:
    deal_key: str
    label: str
    event_at: str
    discount: float | None
    threshold: int
    outcome: str
    detail: str

    def compact_line(self) -> str:
        discount = "unknown" if self.discount is None else f"{self.discount:.0f}%"
        label = _compact(self.label or self.deal_key, 72)
        return f"**{label}** • {discount} • {self.detail}"


@dataclass(frozen=True)
class WalmartDeliveryHealth:
    window_hours: int = DEFAULT_WINDOW_HOURS
    events_seen: int = 0
    events_processed: int = 0
    events_pending: int = 0
    events_with_errors: int = 0
    posted: int = 0
    below_threshold: int = 0
    category_muted: int = 0
    quality_blocked: int = 0
    duplicate_or_reserved: int = 0
    eligible_without_post: int = 0
    invalid_snapshot: int = 0
    total_event_rows: int = 0
    total_pending_rows: int = 0
    latest_event_at: str | None = None
    latest_decision: WalmartDeliveryDecision | None = None
    query_error: str | None = None

    @property
    def has_delivery_problem(self) -> bool:
        return bool(
            self.query_error
            or self.events_with_errors
            or self.eligible_without_post
        )

    def summary_line(self, *, threshold: int) -> str:
        if self.query_error:
            return (
                "⚠️ Walmart delivery audit could not read the live fanout tables: "
                f"`{_compact(self.query_error, 260)}`"
            )
        if self.events_seen == 0:
            return (
                f"Exact events in the last **{self.window_hours}h**: **0**\n"
                f"Posts to this server: **0**\n"
                "The catalog and exact queue can be healthy without producing a post. "
                f"Nothing new reached the current **{int(threshold)}%+** server gate in this window."
            )

        lines = [
            f"Exact events in the last **{self.window_hours}h**: **{self.events_seen}** "
            f"(processed **{self.events_processed}** • pending **{self.events_pending}** • errors **{self.events_with_errors}**)",
            f"Posts to this server: **{self.posted}**",
            (
                f"No-post reasons: below **{int(threshold)}%** **{self.below_threshold}** • "
                f"muted category **{self.category_muted}** • proof/quality guard **{self.quality_blocked}** • "
                f"duplicate/reserved **{self.duplicate_or_reserved}**"
            ),
        ]
        if self.eligible_without_post:
            lines.append(
                "⚠️ Current-rule eligible events without a durable post receipt: "
                f"**{self.eligible_without_post}**. This indicates a delivery or historical dedupe decision that needs inspection."
            )
        if self.latest_decision is not None:
            lines.append("Latest decision: " + self.latest_decision.compact_line())
        return "\n".join(lines)

    def storage_line(self, *, threshold: int) -> str:
        if self.query_error:
            return f"Live Walmart pipeline unavailable: `{_compact(self.query_error, 300)}`"
        return (
            f"Global event rows: **{self.total_event_rows}** • pending fanout: **{self.total_pending_rows}**\n"
            f"Last {self.window_hours}h events: **{self.events_seen}** • processed/pending/errors: "
            f"**{self.events_processed}/{self.events_pending}/{self.events_with_errors}**\n"
            f"This server posted: **{self.posted}** • below {int(threshold)}%: **{self.below_threshold}** • "
            f"muted: **{self.category_muted}** • proof/quality: **{self.quality_blocked}** • "
            f"eligible without receipt: **{self.eligible_without_post}**"
        )


async def load_walmart_delivery_health(
    db: Any,
    *,
    guild_id: int,
    threshold: int,
    category_preferences: dict[str, str] | None = None,
    window_hours: int = DEFAULT_WINDOW_HOURS,
    event_limit: int = DEFAULT_EVENT_LIMIT,
) -> WalmartDeliveryHealth:
    """Audit recent exact Walmart events against this guild's current rules.

    The global fanout historically logged destination skips without persisting a
    per-guild explanation. This read-only audit reconstructs recent exact event
    snapshots, compares them with the guild's current threshold/category rules,
    and checks durable public-post receipts. It never sends, reserves, replays,
    or mutates a deal.
    """

    hours = max(1, min(24 * 30, int(window_hours)))
    limit = max(1, min(250, int(event_limit)))
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    conn = db.require_conn()

    try:
        event_cursor = await conn.execute(
            f"""
            SELECT deal_key, snapshot_json, first_seen_at, source_verified_at,
                   processed_at, last_error
            FROM {legacy.EVENT_TABLE}
            WHERE first_seen_at >= ?
            ORDER BY first_seen_at DESC
            LIMIT ?
            """,
            (cutoff, limit),
        )
        event_rows = list(await event_cursor.fetchall())

        global_cursor = await conn.execute(
            f"""
            SELECT COUNT(*) AS total,
                   SUM(CASE WHEN processed_at IS NULL THEN 1 ELSE 0 END) AS pending
            FROM {legacy.EVENT_TABLE}
            """
        )
        global_row = await global_cursor.fetchone()

        post_cursor = await conn.execute(
            """
            SELECT deal_key, status, first_seen_at, posted_at
            FROM guild_public_deal_posts
            WHERE CAST(guild_id AS TEXT) = ?
              AND retailer = 'walmart'
              AND first_seen_at >= ?
            """,
            (snowflake_text(guild_id), cutoff),
        )
        post_rows = list(await post_cursor.fetchall())
    except Exception as exc:
        return WalmartDeliveryHealth(
            window_hours=hours,
            query_error=f"{type(exc).__name__}: {exc}",
        )

    post_status = {
        str(_row_get(row, "deal_key", 0) or ""): str(
            _row_get(row, "status", 1) or ""
        ).lower()
        for row in post_rows
    }

    counts = {
        "events_processed": 0,
        "events_pending": 0,
        "events_with_errors": 0,
        "posted": 0,
        "below_threshold": 0,
        "category_muted": 0,
        "quality_blocked": 0,
        "duplicate_or_reserved": 0,
        "eligible_without_post": 0,
        "invalid_snapshot": 0,
    }
    decisions: list[WalmartDeliveryDecision] = []

    for row in event_rows:
        deal_key = str(_row_get(row, "deal_key", 0) or "")
        snapshot_json = str(_row_get(row, "snapshot_json", 1) or "")
        event_at = str(
            _row_get(row, "source_verified_at", 3)
            or _row_get(row, "first_seen_at", 2)
            or "unknown"
        )
        processed_at = str(_row_get(row, "processed_at", 4) or "")
        last_error = str(_row_get(row, "last_error", 5) or "").strip()

        candidate = _candidate_from_snapshot(snapshot_json)
        card = legacy._exact_card_for_candidate(candidate)
        if card is None:
            counts["invalid_snapshot"] += 1
            decisions.append(
                WalmartDeliveryDecision(
                    deal_key=deal_key,
                    label="Unreadable exact snapshot",
                    event_at=event_at,
                    discount=None,
                    threshold=int(threshold),
                    outcome="invalid_snapshot",
                    detail="snapshot could not rebuild an exact public card",
                )
            )
            continue

        label = str(getattr(card, "label", None) or "Walmart deal")
        discount = _float_or_none(structured_discount(card))
        category = decide_category(card, category_preferences or {})
        retailer = str(getattr(card, "retailer", None) or "walmart")
        public_key = str(
            getattr(card, "public_post_key", None)
            or card_deal_key(card, retailer=retailer)
        )
        status = post_status.get(public_key) or post_status.get(deal_key) or ""

        if not processed_at:
            counts["events_pending"] += 1
            outcome = "pending"
            detail = "waiting for global fanout"
        else:
            counts["events_processed"] += 1
            if last_error:
                counts["events_with_errors"] += 1
                outcome = "fanout_error"
                detail = "fanout recorded an error"
            elif status == "posted":
                counts["posted"] += 1
                outcome = "posted"
                detail = "posted to this server"
            elif status in {"reserved", "sending"}:
                counts["duplicate_or_reserved"] += 1
                outcome = "reserved"
                detail = f"durable post slot is {status}"
            elif category.action == "suppress":
                counts["category_muted"] += 1
                outcome = "category_muted"
                detail = f"muted category: {category.category_label}"
            elif discount is not None and discount < int(threshold):
                counts["below_threshold"] += 1
                outcome = "below_threshold"
                detail = f"below this server's {int(threshold)}% threshold"
            elif not is_public_deal_candidate(
                card,
                source_label=SOURCE_LABEL,
                min_discount=int(threshold),
            ):
                counts["quality_blocked"] += 1
                outcome = "quality_blocked"
                detail = "blocked by exact proof/quality guard"
            else:
                counts["eligible_without_post"] += 1
                outcome = "eligible_without_post"
                detail = "current rules allow it, but no durable post receipt exists"

        decisions.append(
            WalmartDeliveryDecision(
                deal_key=deal_key,
                label=label,
                event_at=event_at,
                discount=discount,
                threshold=int(threshold),
                outcome=outcome,
                detail=detail,
            )
        )

    latest = decisions[0] if decisions else None
    total = int(_row_get(global_row, "total", 0) or 0)
    pending = int(_row_get(global_row, "pending", 1) or 0)
    return WalmartDeliveryHealth(
        window_hours=hours,
        events_seen=len(event_rows),
        total_event_rows=total,
        total_pending_rows=pending,
        latest_event_at=latest.event_at if latest else None,
        latest_decision=latest,
        **counts,
    )


def _row_get(row: Any, key: str, index: int) -> Any:
    if row is None:
        return None
    try:
        return row[key]
    except Exception:
        pass
    try:
        return row[index]
    except Exception:
        pass
    if isinstance(row, dict):
        return row.get(key)
    return getattr(row, key, None)


def _float_or_none(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return round(float(value), 2)
    except (TypeError, ValueError, OverflowError):
        return None


def _compact(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)].rstrip() + "…"
