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
from sniperplug.services.walmart_fanout_decisions import (
    load_walmart_fanout_decisions,
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
    delivery_disabled: int = 0
    wrong_retailer: int = 0
    eligible_without_post: int = 0
    legacy_without_decision: int = 0
    historical_decisions: int = 0
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
                f"duplicate/reserved **{self.duplicate_or_reserved}** • "
                f"delivery disabled/retailer **{self.delivery_disabled}/{self.wrong_retailer}**"
            ),
            (
                f"Historical destination decisions available: **{self.historical_decisions}/{self.events_seen}**"
            ),
        ]
        if self.eligible_without_post:
            lines.append(
                "⚠️ Processed events with a stored historical decision that still has no explained public outcome: "
                f"**{self.eligible_without_post}**. These are real delivery gaps to inspect."
            )
        if self.legacy_without_decision:
            lines.append(
                "ℹ️ Older processed events without stored per-server decision provenance: "
                f"**{self.legacy_without_decision}**. Current rules may allow them now, but they cannot be labeled "
                "missed posts because the historical threshold/category/dedupe decision was never persisted."
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
            f"true unexplained receipt gaps: **{self.eligible_without_post}** • "
            f"legacy decisions unknown: **{self.legacy_without_decision}**"
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
    """Audit recent exact Walmart events against durable destination outcomes.

    New fanout writes one bounded per-guild decision row before an exact event is
    marked processed. Those rows preserve the threshold/category/quality/dedupe
    reason that actually applied when fanout ran. Events created before this
    provenance table existed are still reconstructed under current rules for
    context, but an otherwise-eligible legacy row is labeled historical-unknown
    rather than incorrectly declared a missed post.
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
        historical = await load_walmart_fanout_decisions(
            db,
            guild_id=guild_id,
            event_keys=(str(_row_get(row, "deal_key", 0) or "") for row in event_rows),
        )
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
        "delivery_disabled": 0,
        "wrong_retailer": 0,
        "eligible_without_post": 0,
        "legacy_without_decision": 0,
        "historical_decisions": 0,
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
        stored = historical.get(deal_key)

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
            elif stored is not None:
                counts["historical_decisions"] += 1
                outcome = stored.outcome
                detail = stored.detail or f"historical destination decision: {stored.outcome}"
                if outcome == "posted":
                    counts["posted"] += 1
                elif outcome == "below_threshold":
                    counts["below_threshold"] += 1
                elif outcome == "category_muted":
                    counts["category_muted"] += 1
                elif outcome == "quality_blocked":
                    counts["quality_blocked"] += 1
                elif outcome in {"recent_duplicate", "reserved_duplicate"}:
                    counts["duplicate_or_reserved"] += 1
                elif outcome == "disabled":
                    counts["delivery_disabled"] += 1
                elif outcome == "wrong_retailer":
                    counts["wrong_retailer"] += 1
                elif outcome == "error":
                    counts["events_with_errors"] += 1
                else:
                    counts["eligible_without_post"] += 1
            elif category.action == "suppress":
                counts["category_muted"] += 1
                outcome = "legacy_category_muted_current_rules"
                detail = f"current rules mute category: {category.category_label}; historical decision unavailable"
            elif discount is not None and discount < int(threshold):
                counts["below_threshold"] += 1
                outcome = "legacy_below_threshold_current_rules"
                detail = f"current rules place it below {int(threshold)}%; historical decision unavailable"
            elif not is_public_deal_candidate(
                card,
                source_label=SOURCE_LABEL,
                min_discount=int(threshold),
            ):
                counts["quality_blocked"] += 1
                outcome = "legacy_quality_blocked_current_rules"
                detail = "current exact proof/quality rules block it; historical decision unavailable"
            else:
                counts["legacy_without_decision"] += 1
                outcome = "legacy_decision_unknown"
                detail = "current rules allow it, but this event predates durable per-server decision provenance"

        decisions.append(
            WalmartDeliveryDecision(
                deal_key=deal_key,
                label=label,
                event_at=event_at,
                discount=discount,
                threshold=(stored.threshold if stored is not None else int(threshold)),
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
