"""Durable public-fanout health: per-cycle totals, turn-away reasons, and
the silent-posting-outage alarm.

The 2026-11-18 incident is the reason this exists: a new live pre-send
recheck rejected 100% of public deals and posting stayed dead for ~40
hours because nothing persisted the fanout's per-cycle totals and nothing
alerted anyone. The fanout already computed the totals and logged a
summary line; this module keeps them in the shared database instead, so

* the staff health screen can show *why* deals were turned away, and
* an alarm can DM the bot owner when deals demonstrably flow into the
  delivery pipeline for hours yet none of them post.

Everything here is observability only. Nothing in this module changes
what posts, what is skipped, or any price-proof rule — and every entry
point is best-effort: a bookkeeping failure must never break delivery
(the same contract as ``walmart_worker_cycle_health``).
"""

from __future__ import annotations

import json
import logging
import os
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping


log = logging.getLogger("sniperplug.autoscan.fanout")

CYCLE_TABLE = "walmart_fanout_cycle_health"
STATE_TABLE = "walmart_fanout_alarm_state"
_STATE_SCOPE = "global"
_STATE_ATTR = "_sniperplug_walmart_fanout_health_ready"

# Cycle rows are small and bounded: two days is plenty for the trailing
# windows below plus a diagnosis margin, and the prune runs on every
# recorded cycle so the table cannot grow without limit.
HISTORY_RETENTION_HOURS = 48

# Outage alarm shape. The window must be long enough that a genuinely
# quiet stretch (few markdowns overnight) cannot look like an outage,
# and the flow floor must be high enough that a trickle of stale events
# cannot trip it, yet far below real traffic: the fanout claims events
# in most cycles whenever Walmart has live verified markdowns, so 10
# claimed deals inside 3 hours with zero posts means deals demonstrably
# flowed into the pipeline and every one of them failed to post — the
# exact shape of the 2026-11-18 silent outage.
OUTAGE_WINDOW_HOURS = 3
OUTAGE_MIN_CLAIMED = 10
# After a failed DM attempt (Discord hiccup, DMs closed), wait before
# trying again so a broken alert channel cannot spin every cycle.
ALARM_RETRY_MINUTES = 30

# The health screen shows a wider window than the alarm: staff diagnosing
# "why is nothing posting" want the day's shape, not just the last 3h.
DISPLAY_WINDOW_HOURS = 24


# Plain-words labels for the live pre-send recheck rejection reasons
# (computed in walmart_global_deal_fanout_bulk._live_pre_send_recheck_reason).
# Two fetch-failure reasons share one label on purpose: to a human they
# are the same event, "the live check could not be completed".
RECHECK_REASON_LABELS: dict[str, str] = {
    "live_price_changed": "Price had already changed at the store",
    "live_fetch_failed": "Live price check couldn't be completed",
    "live_fetch_unverified": "Live price check couldn't be completed",
    "live_seller_name_changed": "Seller changed",
    "live_not_buyable": "No longer buyable",
    "live_not_online": "No longer sold online",
    "live_offer_or_variant_changed": "Item or variant changed",
    "live_checkout_restricted": "Price only visible at checkout or members-only",
    "live_price_missing": "Live price missing",
    "original_identity_missing": "Deal was missing its proof details",
    "live_provider_unavailable": "Price-check service unavailable",
}

# Plain-words labels for the other public skip buckets the fanout counts.
SKIP_BUCKET_LABELS: dict[str, str] = {
    "not_alertable": "Below a server's minimum discount or outside its categories",
    "duplicate": "Already posted recently (duplicate)",
    "disabled": "A server's deal posts are turned off",
    "wrong_retailer": "Store not picked for that server",
}


def recheck_reason_label(reason: str) -> str:
    """Plain-words label for one recheck reason; unknown reasons degrade
    to a cleaned-up version of the key so future reasons still render."""

    label = RECHECK_REASON_LABELS.get(str(reason or ""))
    if label:
        return label
    text = str(reason or "unknown").replace("_", " ").strip()
    return text[:1].upper() + text[1:] if text else "Unknown reason"


@dataclass(frozen=True)
class FanoutCycleTotals:
    """One fanout cycle's counters, as persisted."""

    events_loaded: int = 0
    events_claimed: int = 0
    events_processed: int = 0
    public_posts: int = 0
    skipped_duplicate: int = 0
    skipped_not_alertable: int = 0
    skipped_disabled: int = 0
    skipped_wrong_retailer: int = 0
    skipped_live_recheck: int = 0
    public_errors: int = 0
    recheck_reasons: Mapping[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class FanoutHealth:
    """Aggregated fanout health over a trailing window (fail-safe)."""

    available: bool = False
    window_hours: int = DISPLAY_WINDOW_HOURS
    cycles: int = 0
    events_claimed: int = 0
    events_processed: int = 0
    public_posts: int = 0
    skipped_duplicate: int = 0
    skipped_not_alertable: int = 0
    skipped_disabled: int = 0
    skipped_wrong_retailer: int = 0
    skipped_live_recheck: int = 0
    public_errors: int = 0
    reason_counts: Mapping[str, int] = field(default_factory=dict)
    last_cycle_at: str = ""
    last_public_post_at: str = ""
    alarm_fired: bool = False

    def aggregated_reason_counts(self) -> list[tuple[str, int]]:
        """Recheck rejection reasons grouped by their plain-words label,
        biggest first (the two fetch-failure reasons share one label)."""

        by_label: Counter[str] = Counter()
        for reason, count in (self.reason_counts or {}).items():
            amount = int(count or 0)
            if amount > 0:
                by_label[recheck_reason_label(reason)] += amount
        return sorted(by_label.items(), key=lambda item: (-item[1], item[0]))

    def skip_bucket_counts(self) -> list[tuple[str, int]]:
        """The non-recheck public skip buckets, biggest first."""

        buckets = [
            (SKIP_BUCKET_LABELS["not_alertable"], int(self.skipped_not_alertable or 0)),
            (SKIP_BUCKET_LABELS["duplicate"], int(self.skipped_duplicate or 0)),
            (SKIP_BUCKET_LABELS["disabled"], int(self.skipped_disabled or 0)),
            (SKIP_BUCKET_LABELS["wrong_retailer"], int(self.skipped_wrong_retailer or 0)),
        ]
        return sorted(
            (item for item in buckets if item[1] > 0),
            key=lambda item: (-item[1], item[0]),
        )

    def turnaway_summary(self) -> str:
        """The health-screen field body: why deals were turned away."""

        if not self.available:
            return (
                "No turn-aways recorded yet — this fills in as deals are "
                "checked and posted."
            )
        lines = [
            f"Deals checked for posting: **{self.events_claimed}** • "
            f"posted: **{self.public_posts}**"
        ]
        reasons = self.aggregated_reason_counts()[:3]
        buckets = self.skip_bucket_counts()[:2]
        if not reasons and not buckets:
            lines.append(
                f"No deals were turned away in the last {self.window_hours} hours."
            )
            return "\n".join(lines)
        lines.append("Turned away:")
        for label, count in reasons + buckets:
            lines.append(f"• {label}: **{count}**")
        return "\n".join(lines)


@dataclass(frozen=True)
class OutageEvaluation:
    """The alarm decision for one evaluation instant."""

    should_fire: bool = False
    reason: str = ""
    claimed_in_window: int = 0
    posts_in_window: int = 0
    hours_since_post: float | None = None
    last_public_post_at: str = ""
    top_reason: str = ""
    top_reason_count: int = 0


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_datetime(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or ""))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


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


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


async def ensure_fanout_health_schema(db: Any) -> None:
    """Create the fanout-health tables once per live database connection."""

    if db is None:
        return
    conn = db.require_conn()
    if getattr(conn, _STATE_ATTR, False):
        return
    await conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {CYCLE_TABLE} (
            recorded_at TEXT NOT NULL,
            events_loaded INTEGER NOT NULL DEFAULT 0,
            events_claimed INTEGER NOT NULL DEFAULT 0,
            events_processed INTEGER NOT NULL DEFAULT 0,
            public_posts INTEGER NOT NULL DEFAULT 0,
            skipped_duplicate INTEGER NOT NULL DEFAULT 0,
            skipped_not_alertable INTEGER NOT NULL DEFAULT 0,
            skipped_disabled INTEGER NOT NULL DEFAULT 0,
            skipped_wrong_retailer INTEGER NOT NULL DEFAULT 0,
            skipped_live_recheck INTEGER NOT NULL DEFAULT 0,
            public_errors INTEGER NOT NULL DEFAULT 0,
            recheck_reasons_json TEXT NOT NULL DEFAULT '{{}}'
        )
        """
    )
    await conn.execute(
        f"CREATE INDEX IF NOT EXISTS idx_{CYCLE_TABLE}_recorded_at "
        f"ON {CYCLE_TABLE} (recorded_at)"
    )
    await conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {STATE_TABLE} (
            scope TEXT PRIMARY KEY,
            alarm_fired INTEGER NOT NULL DEFAULT 0,
            alarm_fired_at TEXT,
            alarm_attempt_at TEXT,
            last_public_post_at TEXT,
            tracking_since TEXT
        )
        """
    )
    await conn.commit()
    try:
        setattr(conn, _STATE_ATTR, True)
    except Exception:
        pass


async def record_fanout_cycle(
    db: Any,
    totals: FanoutCycleTotals,
    *,
    now: datetime | None = None,
) -> None:
    """Persist one fanout cycle and maintain the alarm state.

    Best-effort: never raises. A cycle with public posts re-arms the
    alarm (a new outage episode can fire again) and stamps the last
    successful post time.
    """

    if db is None:
        return
    try:
        await ensure_fanout_health_schema(db)
        conn = db.require_conn()
        moment = now or _utcnow()
        now_iso = moment.isoformat()
        reasons = {
            str(reason): int(count or 0)
            for reason, count in (totals.recheck_reasons or {}).items()
            if int(count or 0) > 0
        }
        await conn.execute(
            f"""
            INSERT INTO {CYCLE_TABLE} (
                recorded_at, events_loaded, events_claimed, events_processed,
                public_posts, skipped_duplicate, skipped_not_alertable,
                skipped_disabled, skipped_wrong_retailer, skipped_live_recheck,
                public_errors, recheck_reasons_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                now_iso,
                int(totals.events_loaded or 0),
                int(totals.events_claimed or 0),
                int(totals.events_processed or 0),
                int(totals.public_posts or 0),
                int(totals.skipped_duplicate or 0),
                int(totals.skipped_not_alertable or 0),
                int(totals.skipped_disabled or 0),
                int(totals.skipped_wrong_retailer or 0),
                int(totals.skipped_live_recheck or 0),
                int(totals.public_errors or 0),
                json.dumps(reasons, sort_keys=True),
            ),
        )
        cutoff = (moment - timedelta(hours=HISTORY_RETENTION_HOURS)).isoformat()
        await conn.execute(
            f"DELETE FROM {CYCLE_TABLE} WHERE recorded_at < ?",
            (cutoff,),
        )
        posted = int(totals.public_posts or 0) > 0
        await conn.execute(
            f"""
            INSERT INTO {STATE_TABLE} (
                scope, alarm_fired, alarm_fired_at, alarm_attempt_at,
                last_public_post_at, tracking_since
            ) VALUES (?, 0, NULL, NULL, ?, ?)
            ON CONFLICT(scope) DO UPDATE SET
                tracking_since = COALESCE(
                    {STATE_TABLE}.tracking_since, excluded.tracking_since
                ),
                last_public_post_at = CASE
                    WHEN excluded.last_public_post_at IS NOT NULL
                    THEN excluded.last_public_post_at
                    ELSE {STATE_TABLE}.last_public_post_at
                END,
                alarm_fired = CASE
                    WHEN excluded.last_public_post_at IS NOT NULL THEN 0
                    ELSE {STATE_TABLE}.alarm_fired
                END,
                alarm_attempt_at = CASE
                    WHEN excluded.last_public_post_at IS NOT NULL THEN NULL
                    ELSE {STATE_TABLE}.alarm_attempt_at
                END
            """,
            (_STATE_SCOPE, now_iso if posted else None, now_iso),
        )
        await conn.commit()
    except Exception:
        # Health bookkeeping must never turn a delivery cycle into a
        # failure, and must never mask the real outcome in the caller.
        log.debug("Fanout cycle-health record failed safely", exc_info=True)


async def _load_state(conn: Any) -> dict[str, Any]:
    cursor = await conn.execute(
        f"""
        SELECT alarm_fired, alarm_fired_at, alarm_attempt_at,
               last_public_post_at, tracking_since
        FROM {STATE_TABLE}
        WHERE scope = ?
        """,
        (_STATE_SCOPE,),
    )
    row = await cursor.fetchone()
    if row is None:
        return {}
    return {
        "alarm_fired": bool(_as_int(_row_get(row, "alarm_fired", 0))),
        "alarm_fired_at": str(_row_get(row, "alarm_fired_at", 1) or ""),
        "alarm_attempt_at": str(_row_get(row, "alarm_attempt_at", 2) or ""),
        "last_public_post_at": str(_row_get(row, "last_public_post_at", 3) or ""),
        "tracking_since": str(_row_get(row, "tracking_since", 4) or ""),
    }


async def _load_window(
    db: Any,
    *,
    window_hours: int,
    now: datetime,
) -> tuple[dict[str, int], Counter[str], str]:
    """Sum cycle counters over the trailing window; also returns the
    most recent cycle timestamp inside (or before) the window."""

    conn = db.require_conn()
    cutoff = (now - timedelta(hours=max(1, int(window_hours)))).isoformat()
    cursor = await conn.execute(
        f"""
        SELECT
            COUNT(*) AS cycles,
            COALESCE(SUM(events_claimed), 0) AS claimed,
            COALESCE(SUM(events_processed), 0) AS processed,
            COALESCE(SUM(public_posts), 0) AS posts,
            COALESCE(SUM(skipped_duplicate), 0) AS dup,
            COALESCE(SUM(skipped_not_alertable), 0) AS not_alertable,
            COALESCE(SUM(skipped_disabled), 0) AS disabled,
            COALESCE(SUM(skipped_wrong_retailer), 0) AS wrong_retailer,
            COALESCE(SUM(skipped_live_recheck), 0) AS live_recheck,
            COALESCE(SUM(public_errors), 0) AS errors,
            MAX(recorded_at) AS last_cycle
        FROM {CYCLE_TABLE}
        WHERE recorded_at >= ?
        """,
        (cutoff,),
    )
    row = await cursor.fetchone()
    totals = {
        "cycles": _as_int(_row_get(row, "cycles", 0)),
        "claimed": _as_int(_row_get(row, "claimed", 1)),
        "processed": _as_int(_row_get(row, "processed", 2)),
        "posts": _as_int(_row_get(row, "posts", 3)),
        "duplicate": _as_int(_row_get(row, "dup", 4)),
        "not_alertable": _as_int(_row_get(row, "not_alertable", 5)),
        "disabled": _as_int(_row_get(row, "disabled", 6)),
        "wrong_retailer": _as_int(_row_get(row, "wrong_retailer", 7)),
        "live_recheck": _as_int(_row_get(row, "live_recheck", 8)),
        "errors": _as_int(_row_get(row, "errors", 9)),
    }
    last_cycle = str(_row_get(row, "last_cycle", 10) or "")

    reasons: Counter[str] = Counter()
    cursor = await conn.execute(
        f"SELECT recheck_reasons_json FROM {CYCLE_TABLE} WHERE recorded_at >= ?",
        (cutoff,),
    )
    for reason_row in await cursor.fetchall():
        raw = _row_get(reason_row, "recheck_reasons_json", 0)
        try:
            payload = json.loads(str(raw or "{}"))
        except (TypeError, ValueError):
            continue
        if isinstance(payload, dict):
            for reason, count in payload.items():
                amount = _as_int(count)
                if amount > 0:
                    reasons[str(reason)] += amount
    return totals, reasons, last_cycle


async def load_fanout_health(
    db: Any,
    *,
    window_hours: int = DISPLAY_WINDOW_HOURS,
    now: datetime | None = None,
) -> FanoutHealth:
    """Aggregated fanout health for the staff health screen, fail-safe."""

    empty = FanoutHealth(available=False, window_hours=int(window_hours))
    if db is None:
        return empty
    try:
        await ensure_fanout_health_schema(db)
        conn = db.require_conn()
        moment = now or _utcnow()
        totals, reasons, last_cycle = await _load_window(
            db, window_hours=window_hours, now=moment
        )
        state = await _load_state(conn)
    except Exception:
        return empty
    if totals["cycles"] <= 0 and not state:
        return empty
    return FanoutHealth(
        available=True,
        window_hours=int(window_hours),
        cycles=totals["cycles"],
        events_claimed=totals["claimed"],
        events_processed=totals["processed"],
        public_posts=totals["posts"],
        skipped_duplicate=totals["duplicate"],
        skipped_not_alertable=totals["not_alertable"],
        skipped_disabled=totals["disabled"],
        skipped_wrong_retailer=totals["wrong_retailer"],
        skipped_live_recheck=totals["live_recheck"],
        public_errors=totals["errors"],
        reason_counts=dict(reasons),
        last_cycle_at=last_cycle,
        last_public_post_at=str(state.get("last_public_post_at") or ""),
        alarm_fired=bool(state.get("alarm_fired")),
    )


def evaluate_outage_snapshot(
    *,
    posts_in_window: int,
    claimed_in_window: int,
    enrolled_guilds: int,
    alarm_fired: bool,
    alarm_attempt_at: str = "",
    last_public_post_at: str = "",
    tracking_since: str = "",
    reason_counts: Mapping[str, int] | None = None,
    now: datetime,
) -> OutageEvaluation:
    """Pure alarm decision over one loaded snapshot.

    Fires only when deals demonstrably flowed (claimed >= floor) across
    a full outage window (>= OUTAGE_WINDOW_HOURS since the last post, or
    since tracking began when no post has ever been recorded) yet zero
    posts landed, at least one server has public alerts on, and this
    episode has not already fired (or retried too recently).
    """

    top_reason = ""
    top_reason_count = 0
    if reason_counts:
        probe = FanoutHealth(reason_counts=dict(reason_counts))
        ranked = probe.aggregated_reason_counts()
        if ranked:
            top_reason, top_reason_count = ranked[0]

    anchor = _parse_datetime(last_public_post_at) or _parse_datetime(tracking_since)
    hours_since_post: float | None = None
    if anchor is not None:
        hours_since_post = max(0.0, (now - anchor).total_seconds() / 3600.0)

    base = dict(
        claimed_in_window=int(claimed_in_window or 0),
        posts_in_window=int(posts_in_window or 0),
        hours_since_post=hours_since_post,
        last_public_post_at=str(last_public_post_at or ""),
        top_reason=top_reason,
        top_reason_count=int(top_reason_count or 0),
    )
    if int(enrolled_guilds or 0) < 1:
        return OutageEvaluation(reason="no guild has public alerts enabled", **base)
    if alarm_fired:
        return OutageEvaluation(reason="already fired for this outage episode", **base)
    attempt = _parse_datetime(alarm_attempt_at)
    if attempt is not None and now - attempt < timedelta(minutes=ALARM_RETRY_MINUTES):
        return OutageEvaluation(reason="alarm attempt too recent", **base)
    if int(posts_in_window or 0) > 0:
        return OutageEvaluation(reason="posts landed inside the window", **base)
    if int(claimed_in_window or 0) < OUTAGE_MIN_CLAIMED:
        return OutageEvaluation(reason="not enough deals flowed to judge", **base)
    if hours_since_post is None or hours_since_post < OUTAGE_WINDOW_HOURS:
        return OutageEvaluation(reason="outage window not yet full", **base)
    return OutageEvaluation(should_fire=True, reason="outage shape matched", **base)


async def evaluate_outage(
    db: Any,
    *,
    enrolled_guilds: int,
    now: datetime | None = None,
) -> OutageEvaluation:
    """Load the trailing outage window + alarm state and decide."""

    moment = now or _utcnow()
    if db is None:
        return OutageEvaluation(reason="no database")
    await ensure_fanout_health_schema(db)
    conn = db.require_conn()
    totals, reasons, _last_cycle = await _load_window(
        db, window_hours=OUTAGE_WINDOW_HOURS, now=moment
    )
    state = await _load_state(conn)
    return evaluate_outage_snapshot(
        posts_in_window=totals["posts"],
        claimed_in_window=totals["claimed"],
        enrolled_guilds=enrolled_guilds,
        alarm_fired=bool(state.get("alarm_fired")),
        alarm_attempt_at=str(state.get("alarm_attempt_at") or ""),
        last_public_post_at=str(state.get("last_public_post_at") or ""),
        tracking_since=str(state.get("tracking_since") or ""),
        reason_counts=dict(reasons),
        now=moment,
    )


async def count_enabled_public_alert_guilds(db: Any) -> int:
    """How many servers currently have public deal alerts switched on."""

    from sniperplug.services.public_alert_config import ensure_public_alert_table

    await ensure_public_alert_table(db)
    conn = db.require_conn()
    cursor = await conn.execute(
        "SELECT COUNT(*) FROM guild_public_alert_settings WHERE enabled = 1"
    )
    row = await cursor.fetchone()
    return _as_int(_row_get(row, "COUNT(*)", 0))


async def _mark_alarm_attempt(db: Any, *, now: datetime) -> None:
    await ensure_fanout_health_schema(db)
    conn = db.require_conn()
    now_iso = now.isoformat()
    await conn.execute(
        f"""
        INSERT INTO {STATE_TABLE} (
            scope, alarm_fired, alarm_attempt_at, tracking_since
        ) VALUES (?, 0, ?, ?)
        ON CONFLICT(scope) DO UPDATE SET
            alarm_attempt_at = excluded.alarm_attempt_at
        """,
        (_STATE_SCOPE, now_iso, now_iso),
    )
    await conn.commit()


async def _mark_alarm_fired(db: Any, *, now: datetime) -> None:
    await ensure_fanout_health_schema(db)
    conn = db.require_conn()
    now_iso = now.isoformat()
    await conn.execute(
        f"""
        INSERT INTO {STATE_TABLE} (
            scope, alarm_fired, alarm_fired_at, alarm_attempt_at, tracking_since
        ) VALUES (?, 1, ?, ?, ?)
        ON CONFLICT(scope) DO UPDATE SET
            alarm_fired = 1,
            alarm_fired_at = excluded.alarm_fired_at,
            alarm_attempt_at = excluded.alarm_attempt_at
        """,
        (_STATE_SCOPE, now_iso, now_iso, now_iso),
    )
    await conn.commit()


async def resolve_owner_user_id(bot: Any) -> int | None:
    """Identify the bot owner's user id without any hardcoded id.

    Order: explicit env override, then the ids discord.py already knows
    (bot.owner_id / bot.owner_ids, populated for application owners),
    then the application info's owner (the Discord app's owner, or the
    owning team's owner).
    """

    raw = os.getenv("SNIPERPLUG_OWNER_USER_ID", "").strip()
    if raw.isdigit():
        return int(raw)
    owner_id = getattr(bot, "owner_id", None)
    if isinstance(owner_id, int) and owner_id > 0:
        return owner_id
    owner_ids = getattr(bot, "owner_ids", None)
    if owner_ids:
        for candidate in owner_ids:
            try:
                value = int(candidate)
            except (TypeError, ValueError):
                continue
            if value > 0:
                return value
    application_info = getattr(bot, "application_info", None)
    if not callable(application_info):
        return None
    try:
        info = await application_info()
    except Exception:
        return None
    owner = getattr(info, "owner", None)
    if owner is not None and getattr(owner, "id", None):
        try:
            return int(owner.id)
        except (TypeError, ValueError):
            return None
    team = getattr(info, "team", None)
    if team is not None and getattr(team, "owner_id", None):
        try:
            return int(team.owner_id)
        except (TypeError, ValueError):
            return None
    return None


def build_outage_alarm_message(evaluation: OutageEvaluation) -> str:
    """The owner DM, in plain words."""

    hours = evaluation.hours_since_post
    if hours is None:
        duration = f"over {OUTAGE_WINDOW_HOURS} hours"
    elif hours >= 1:
        duration = f"over {int(hours)} hours"
    else:
        duration = "over an hour"
    lines = [
        "⚠️ **SniperPlug deal posts look stuck.**",
        "",
        (
            f"No deal has posted to any server in {duration}, even though "
            f"**{evaluation.claimed_in_window}** deals were checked for "
            f"posting in the last {OUTAGE_WINDOW_HOURS} hours and none of "
            "them posted."
        ),
    ]
    if evaluation.top_reason and evaluation.top_reason_count > 0:
        lines.append(
            f"Most common turn-away reason: {evaluation.top_reason} "
            f"({evaluation.top_reason_count} deals)."
        )
    if evaluation.last_public_post_at:
        lines.append(f"Last successful post: `{evaluation.last_public_post_at}`.")
    lines.append("Run /autoscan_health in your server for the full picture.")
    return "\n".join(lines)


async def maybe_send_outage_alarm(
    bot: Any,
    db: Any,
    *,
    now: datetime | None = None,
) -> bool:
    """Evaluate the outage alarm and DM the owner at most once per episode.

    Returns True only when a DM was actually sent. Never raises: a
    broken alarm must not break posting.
    """

    if bot is None or db is None:
        return False
    moment = now or _utcnow()
    try:
        enrolled = await count_enabled_public_alert_guilds(db)
        evaluation = await evaluate_outage(db, enrolled_guilds=enrolled, now=moment)
        if not evaluation.should_fire:
            return False
        # Stamp the attempt before sending so a failing DM channel is
        # retried on the retry cadence, not on every fanout cycle.
        await _mark_alarm_attempt(db, now=moment)
        owner_id = await resolve_owner_user_id(bot)
        if owner_id is None:
            log.warning("Outage alarm: could not identify the bot owner; not sent")
            return False
        user = None
        get_user = getattr(bot, "get_user", None)
        if callable(get_user):
            user = get_user(owner_id)
        if user is None:
            fetch_user = getattr(bot, "fetch_user", None)
            if callable(fetch_user):
                user = await fetch_user(owner_id)
        if user is None:
            log.warning("Outage alarm: owner user %s unavailable", owner_id)
            return False
        await user.send(build_outage_alarm_message(evaluation))
        await _mark_alarm_fired(db, now=moment)
        log.warning(
            "Outage alarm sent to owner: claimed=%s posts=%s hours_since_post=%s",
            evaluation.claimed_in_window,
            evaluation.posts_in_window,
            evaluation.hours_since_post,
        )
        return True
    except Exception:
        log.warning("Outage alarm failed safely", exc_info=True)
        return False
