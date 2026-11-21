"""Home Depot penny tracker: watch clearance items until they hit $0.01.

The tracker keeps a durable watch list of Home Depot products, polls each
one through the SerpApi ``home_depot_product`` engine (one credit per item
per poll, daily-capped), records every price/stage change, and publishes
verified-retailer events when an item enters the final markdown stage or
is observed at exactly $0.01. The existing retailer fanout turns those
events into the deals-channel post and personal DMs.

Stage decoding lives in :mod:`sniperplug.services.hd_penny_stages` and is
grounded in the sourced research doc; this module owns storage, polling,
learned stage timing, and event publication. Prices are stored as integer
cents so the penny is exact, never a float rounding artifact.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Any, Callable

from sniperplug.models.candidate import SourceCandidate
from sniperplug.services.hd_penny_stages import (
    PennyStage,
    STAGE_LABELS,
    STAGE_ORDER,
    DropEstimate,
    decode_price_stage,
    estimate_next_drop,
    price_to_cents,
    stage_sort_priority,
)
from sniperplug.services.verified_retailer_events import publish_verified_retailer_event


__all__ = [
    "EVENT_TYPE_FINAL",
    "EVENT_TYPE_PENNY",
    "HdPennyConfig",
    "HdPennyPollSummary",
    "HdPennyStatus",
    "HdPennyStatusItem",
    "SeedResult",
    "StageTransition",
    "TrackedItem",
    "build_hd_penny_event_candidate",
    "ensure_hd_penny_tables",
    "get_hd_penny_item",
    "hd_penny_event_key",
    "learned_stage_dwell_days",
    "list_hd_penny_items",
    "load_hd_penny_status",
    "product_id_from_url",
    "record_hd_penny_observation",
    "run_hd_penny_poll_cycle",
    "seed_from_clearance_seed",
    "seed_from_hunt_candidates",
    "stage_dwells_from_history",
    "stage_label",
    "track_hd_penny_item",
]

log = logging.getLogger("sniperplug.hd_penny_tracker")

ITEMS_TABLE = "hd_penny_items"
HISTORY_TABLE = "hd_penny_price_history"
META_TABLE = "hd_penny_meta"

EVENT_TYPE_FINAL = "penny_final_stage"
EVENT_TYPE_PENNY = "penny_price"

# A hunt candidate with no decodable stage still earns tracking when the
# observed markdown is this deep — the scan-data sources identify discount
# depth, not the cents ending, as the real predictor (PenVue documents a
# real item whose final tag ended in .33, never touching the ladder).
DEEP_MARKDOWN_TRACK_FRACTION = 0.50


@dataclass(frozen=True)
class HdPennyConfig:
    enabled: bool = True
    store_id: str | None = None
    zip_code: str | None = None
    poll_interval_hours: float = 6.0
    daily_poll_limit: int = 30

    @classmethod
    def from_env(cls) -> "HdPennyConfig":
        return cls(
            enabled=_env_bool("SNIPERPLUG_HD_PENNY_ENABLED", default=True),
            store_id=os.getenv("SNIPERPLUG_HD_PENNY_STORE_ID", "").strip() or None,
            zip_code=os.getenv("SNIPERPLUG_HD_PENNY_ZIP", "").strip() or None,
            poll_interval_hours=max(
                1.0, _env_float("SNIPERPLUG_HD_PENNY_POLL_HOURS", 6.0)
            ),
            daily_poll_limit=max(
                0, _env_int("SNIPERPLUG_HD_PENNY_DAILY_POLL_LIMIT", 30)
            ),
        )


@dataclass(frozen=True)
class TrackedItem:
    item_id: int
    product_id: str
    store_id: str
    zip_code: str | None
    sku: str | None
    title: str
    product_url: str
    image_url: str | None
    price_cents: int | None
    reference_cents: int | None
    stage: PennyStage
    stage_entered_at: datetime | None
    status: str
    source: str
    guild_id: int | None
    consecutive_failures: int
    first_seen_at: datetime
    last_seen_at: datetime
    last_polled_at: datetime | None


@dataclass(frozen=True)
class StageTransition:
    item_id: int
    product_id: str
    store_id: str
    previous_stage: PennyStage
    new_stage: PennyStage
    price_cents: int
    entered_final: bool
    entered_penny: bool


@dataclass
class SeedResult:
    tracked_new: int = 0
    tracked_existing: int = 0
    skipped: int = 0


@dataclass
class HdPennyPollSummary:
    skipped_reason: str | None = None
    key_configured: bool = True
    tracked: int = 0
    due: int = 0
    polled: int = 0
    credits_used: int = 0
    price_changes: int = 0
    stage_transitions: int = 0
    events_published: int = 0
    failures: int = 0

    def log_line(self) -> str:
        if self.skipped_reason:
            return f"hd penny poll skipped: {self.skipped_reason}"
        return (
            "hd penny poll: "
            f"tracked={self.tracked} due={self.due} polled={self.polled} "
            f"credits={self.credits_used} price_changes={self.price_changes} "
            f"transitions={self.stage_transitions} events={self.events_published} "
            f"failures={self.failures}"
        )


@dataclass(frozen=True)
class HdPennyStatusItem:
    title: str
    product_url: str
    price_cents: int | None
    reference_cents: int | None
    stage: PennyStage
    days_in_stage: float | None
    estimate: DropEstimate | None
    store_label: str
    status: str


@dataclass(frozen=True)
class HdPennyStatus:
    key_configured: bool
    enabled: bool
    watching: int
    pennied: int
    stage_counts: dict[str, int]
    items: tuple[HdPennyStatusItem, ...]
    last_cycle_at: str | None
    last_cycle_summary: str | None


# ---------------------------------------------------------------------------
# Schema


async def ensure_hd_penny_tables(db: Any) -> None:
    conn = db.require_conn()
    if getattr(db, "_hd_penny_tables_ready", False):
        return
    await conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {ITEMS_TABLE} (
            item_id INTEGER PRIMARY KEY,
            product_id TEXT NOT NULL,
            store_id TEXT NOT NULL DEFAULT '',
            zip_code TEXT,
            sku TEXT,
            title TEXT NOT NULL,
            product_url TEXT NOT NULL,
            image_url TEXT,
            current_price_cents INTEGER,
            reference_price_cents INTEGER,
            stage TEXT NOT NULL DEFAULT 'unknown',
            stage_entered_at TEXT,
            status TEXT NOT NULL DEFAULT 'watching',
            source TEXT NOT NULL DEFAULT 'manual',
            guild_id INTEGER,
            consecutive_failures INTEGER NOT NULL DEFAULT 0,
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            last_polled_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    await conn.execute(
        f"CREATE UNIQUE INDEX IF NOT EXISTS idx_{ITEMS_TABLE}_identity "
        f"ON {ITEMS_TABLE} (product_id, store_id)"
    )
    await conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {HISTORY_TABLE} (
            history_id INTEGER PRIMARY KEY,
            item_id INTEGER NOT NULL,
            price_cents INTEGER,
            stage TEXT NOT NULL,
            observed_at TEXT NOT NULL
        )
        """
    )
    await conn.execute(
        f"CREATE INDEX IF NOT EXISTS idx_{HISTORY_TABLE}_item "
        f"ON {HISTORY_TABLE} (item_id, observed_at)"
    )
    await conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {META_TABLE} (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    await conn.commit()
    try:
        setattr(db, "_hd_penny_tables_ready", True)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Tracking / seeding


async def track_hd_penny_item(
    db: Any,
    *,
    product_id: str,
    title: str,
    product_url: str,
    store_id: str = "",
    zip_code: str | None = None,
    sku: str | None = None,
    image_url: str | None = None,
    price_cents: int | None = None,
    reference_cents: int | None = None,
    source: str = "manual",
    guild_id: int | None = None,
    now: datetime | None = None,
) -> tuple[TrackedItem, bool]:
    """Add one item to the watch list (or refresh it). Returns (item, created).

    A supplied price is recorded as an observation, so re-seeding an item
    that has moved stages updates its stage and history. Seeding never
    publishes alert events — the hunt/bank UI already showed the item;
    alerts fire on poll-observed transitions only.
    """

    await ensure_hd_penny_tables(db)
    clean_product_id = str(product_id or "").strip()
    if not clean_product_id:
        raise ValueError("product_id is required to track a Home Depot item")
    now_dt = _utc(now)
    now_iso = now_dt.isoformat()
    conn = db.require_conn()

    existing = await _find_item(conn, clean_product_id, store_id or "")
    if existing is None:
        reading = decode_price_stage(price_cents, clearance_context=True)
        cursor = await conn.execute(
            f"""
            INSERT INTO {ITEMS_TABLE} (
                product_id, store_id, zip_code, sku, title, product_url,
                image_url, current_price_cents, reference_price_cents,
                stage, stage_entered_at, status, source, guild_id,
                consecutive_failures, first_seen_at, last_seen_at,
                last_polled_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'watching', ?, ?, 0, ?, ?, NULL, ?, ?)
            RETURNING item_id
            """,
            (
                clean_product_id,
                store_id or "",
                zip_code,
                sku,
                str(title or "").strip() or clean_product_id,
                str(product_url or "").strip()
                or f"https://www.homedepot.com/p/{clean_product_id}",
                image_url,
                price_cents,
                reference_cents,
                reading.stage.value,
                now_iso if price_cents is not None else None,
                source,
                guild_id,
                now_iso,
                now_iso,
                now_iso,
                now_iso,
            ),
        )
        row = await cursor.fetchone()
        item_id = int(_row_get(row, "item_id", 0))
        if price_cents is not None:
            await _append_history(conn, item_id, price_cents, reading.stage, now_iso)
            if reading.stage == PennyStage.PENNY:
                await conn.execute(
                    f"UPDATE {ITEMS_TABLE} SET status = 'pennied' WHERE item_id = ?",
                    (item_id,),
                )
        await conn.commit()
        created = True
    else:
        item_id = existing.item_id
        await conn.execute(
            f"""
            UPDATE {ITEMS_TABLE}
            SET title = ?, product_url = ?, image_url = COALESCE(?, image_url),
                sku = COALESCE(?, sku), zip_code = COALESCE(?, zip_code),
                last_seen_at = ?, updated_at = ?
            WHERE item_id = ?
            """,
            (
                str(title or "").strip() or existing.title,
                str(product_url or "").strip() or existing.product_url,
                image_url,
                sku,
                zip_code,
                now_iso,
                now_iso,
                item_id,
            ),
        )
        await conn.commit()
        created = False

    if not created and price_cents is not None:
        await record_hd_penny_observation(
            db,
            item_id=item_id,
            price_cents=price_cents,
            reference_cents=reference_cents,
            now=now_dt,
        )
    item = await get_hd_penny_item(db, item_id)
    assert item is not None
    return item, created


async def get_hd_penny_item(db: Any, item_id: int) -> TrackedItem | None:
    await ensure_hd_penny_tables(db)
    conn = db.require_conn()
    cursor = await conn.execute(
        f"SELECT * FROM {ITEMS_TABLE} WHERE item_id = ?", (int(item_id),)
    )
    row = await cursor.fetchone()
    return _item_from_row(row) if row is not None else None


async def list_hd_penny_items(
    db: Any, *, status: str | None = None, limit: int = 500
) -> list[TrackedItem]:
    await ensure_hd_penny_tables(db)
    conn = db.require_conn()
    if status:
        cursor = await conn.execute(
            f"SELECT * FROM {ITEMS_TABLE} WHERE status = ? ORDER BY item_id ASC LIMIT ?",
            (status, int(limit)),
        )
    else:
        cursor = await conn.execute(
            f"SELECT * FROM {ITEMS_TABLE} ORDER BY item_id ASC LIMIT ?",
            (int(limit),),
        )
    return [_item_from_row(row) for row in await cursor.fetchall()]


async def record_hd_penny_observation(
    db: Any,
    *,
    item_id: int,
    price_cents: int,
    reference_cents: int | None = None,
    now: datetime | None = None,
) -> StageTransition | None:
    """Record one observed price. Returns a transition when the stage moved."""

    await ensure_hd_penny_tables(db)
    conn = db.require_conn()
    item = await get_hd_penny_item(db, item_id)
    if item is None:
        return None
    now_dt = _utc(now)
    now_iso = now_dt.isoformat()
    reading = decode_price_stage(price_cents, clearance_context=True)

    reference = item.reference_cents
    if reference_cents is not None and reference_cents > price_cents:
        reference = max(reference or 0, reference_cents)

    price_changed = price_cents != item.price_cents
    stage_changed = reading.stage != item.stage
    stage_entered_at = item.stage_entered_at
    if stage_changed or stage_entered_at is None:
        stage_entered_at = now_dt
    status = "pennied" if reading.stage == PennyStage.PENNY else item.status
    if status == "pennied" and reading.stage != PennyStage.PENNY:
        status = "watching"

    await conn.execute(
        f"""
        UPDATE {ITEMS_TABLE}
        SET current_price_cents = ?, reference_price_cents = ?, stage = ?,
            stage_entered_at = ?, status = ?, consecutive_failures = 0,
            last_seen_at = ?, last_polled_at = ?, updated_at = ?
        WHERE item_id = ?
        """,
        (
            price_cents,
            reference,
            reading.stage.value,
            stage_entered_at.isoformat() if stage_entered_at else None,
            status,
            now_iso,
            now_iso,
            now_iso,
            int(item_id),
        ),
    )
    if price_changed or stage_changed:
        await _append_history(conn, int(item_id), price_cents, reading.stage, now_iso)
    await conn.commit()

    if not stage_changed:
        return None
    return StageTransition(
        item_id=int(item_id),
        product_id=item.product_id,
        store_id=item.store_id,
        previous_stage=item.stage,
        new_stage=reading.stage,
        price_cents=price_cents,
        entered_final=(
            reading.stage == PennyStage.FINAL
            and STAGE_ORDER[item.stage] < STAGE_ORDER[PennyStage.FINAL]
        ),
        entered_penny=reading.stage == PennyStage.PENNY
        and item.stage != PennyStage.PENNY,
    )


async def seed_from_hunt_candidates(
    db: Any,
    candidates: Any,
    *,
    guild_id: int | None = None,
    store_id: str = "",
    zip_code: str | None = None,
    now: datetime | None = None,
) -> SeedResult:
    """Auto-track penny-hunt results worth watching toward a penny.

    An item earns tracking when its price ending decodes to a clearance
    stage, or when the observed markdown is deep (>= 50% off a returned
    was-price) — discount depth is the predictor the scan-data sources
    trust even when the ending is off-ladder.
    """

    result = SeedResult()
    for candidate in candidates or ():
        product_id = str(getattr(candidate, "product_id", None) or "").strip()
        price_cents = price_to_cents(getattr(candidate, "current_price", None))
        if not product_id or price_cents is None:
            result.skipped += 1
            continue
        reference_cents = price_to_cents(
            getattr(candidate, "typical_price", None)
            or getattr(candidate, "api_reference_price", None)
        )
        reading = decode_price_stage(price_cents, clearance_context=True)
        discount_fraction = None
        if reference_cents is not None and reference_cents > price_cents:
            discount_fraction = (reference_cents - price_cents) / reference_cents
        stage_worthy = reading.stage in {
            PennyStage.FIRST,
            PennyStage.MID,
            PennyStage.FINAL,
            PennyStage.PENNY,
        }
        if not stage_worthy and not (
            discount_fraction is not None
            and discount_fraction >= DEEP_MARKDOWN_TRACK_FRACTION
        ):
            result.skipped += 1
            continue
        _, created = await track_hd_penny_item(
            db,
            product_id=product_id,
            title=str(getattr(candidate, "title", None) or product_id),
            product_url=str(getattr(candidate, "product_url", None) or ""),
            store_id=store_id or "",
            zip_code=zip_code,
            sku=getattr(candidate, "sku", None),
            image_url=getattr(candidate, "image_url", None),
            price_cents=price_cents,
            reference_cents=reference_cents,
            source="penny_hunt",
            guild_id=guild_id,
            now=now,
        )
        if created:
            result.tracked_new += 1
        else:
            result.tracked_existing += 1
    return result


async def seed_from_clearance_seed(
    db: Any, seed: dict, *, now: datetime | None = None
) -> bool:
    """Track one Home Depot clearance-bank lead. Returns True when tracked."""

    product_id = product_id_from_url(seed.get("product_url")) or _clean(seed.get("sku"))
    if not product_id:
        return False
    price_cents = price_to_cents(seed.get("observed_price"))
    await track_hd_penny_item(
        db,
        product_id=product_id,
        title=str(seed.get("title") or product_id),
        product_url=str(seed.get("product_url") or "")
        or f"https://www.homedepot.com/p/{product_id}",
        store_id=str(seed.get("store_id") or ""),
        zip_code=_clean(seed.get("zip_code")),
        sku=_clean(seed.get("sku")),
        price_cents=price_cents,
        source="clearance_bank",
        guild_id=seed.get("guild_id"),
        now=now,
    )
    return True


def product_id_from_url(url: Any) -> str | None:
    text = str(url or "").strip()
    if not text:
        return None
    path = text.split("?", 1)[0].split("#", 1)[0].rstrip("/")
    tail = path.rsplit("/", 1)[-1] if "/" in path else path
    if len(tail) >= 4 and any(ch.isdigit() for ch in tail) and tail.lower() != "p":
        return tail
    return None


# ---------------------------------------------------------------------------
# Polling


async def run_hd_penny_poll_cycle(
    db: Any,
    *,
    fetch_detail: Callable[..., Any] | None = None,
    config: HdPennyConfig | None = None,
    now: datetime | None = None,
) -> HdPennyPollSummary:
    """One polling pass over due tracked items.

    ``fetch_detail`` defaults to the SerpApi product-detail lookup and is
    injectable for tests. The cycle is credit-frugal: only due items are
    fetched, final-stage items first, and a persisted daily counter caps
    total fetches per UTC day.
    """

    from sniperplug.services.home_depot_product_lookup import (
        fetch_home_depot_product_detail,
    )

    cfg = config or HdPennyConfig.from_env()
    summary = HdPennyPollSummary()
    if not cfg.enabled:
        summary.skipped_reason = "penny tracking is turned off"
        summary.key_configured = bool(os.getenv("SERPAPI_API_KEY", "").strip())
        return summary
    if not os.getenv("SERPAPI_API_KEY", "").strip():
        summary.skipped_reason = "SERPAPI_API_KEY is not set"
        summary.key_configured = False
        return summary

    await ensure_hd_penny_tables(db)
    conn = db.require_conn()
    now_dt = _utc(now)
    fetch = fetch_detail or fetch_home_depot_product_detail

    items = await list_hd_penny_items(db, status="watching", limit=1000)
    summary.tracked = len(items)
    cutoff = now_dt - timedelta(hours=cfg.poll_interval_hours)
    due = [
        item
        for item in items
        if item.last_polled_at is None or item.last_polled_at <= cutoff
    ]
    due.sort(
        key=lambda item: (
            stage_sort_priority(item.stage),
            item.last_polled_at.isoformat() if item.last_polled_at else "",
            item.item_id,
        )
    )
    day_key = f"poll_credits:{now_dt.strftime('%Y-%m-%d')}"
    used_today = int(await _get_meta(conn, day_key) or 0)
    remaining = max(0, cfg.daily_poll_limit - used_today)
    due = due[:remaining]
    summary.due = len(due)

    for item in due:
        summary.polled += 1
        summary.credits_used += 1
        used_today += 1
        try:
            detail = await fetch(
                item.product_id,
                zip_code=item.zip_code or cfg.zip_code,
                store_id=item.store_id or cfg.store_id,
                db=db,
            )
        except Exception:  # noqa: BLE001 - one bad item cannot stop the cycle.
            log.exception("hd penny poll fetch failed item=%s", item.item_id)
            detail = None
        price_cents = price_to_cents(getattr(detail, "price", None))
        if detail is None or price_cents is None:
            summary.failures += 1
            await _mark_poll_failure(db, item.item_id, now=now_dt)
            continue
        previous_price = item.price_cents
        transition = await record_hd_penny_observation(
            db,
            item_id=item.item_id,
            price_cents=price_cents,
            reference_cents=price_to_cents(getattr(detail, "original_price", None)),
            now=now_dt,
        )
        if price_cents != previous_price:
            summary.price_changes += 1
        if transition is None:
            continue
        summary.stage_transitions += 1
        if not (transition.entered_final or transition.entered_penny):
            continue
        fresh = await get_hd_penny_item(db, item.item_id)
        if fresh is None:
            continue
        candidate = build_hd_penny_event_candidate(
            fresh,
            detail=detail,
            effective_store_id=item.store_id or cfg.store_id,
            now=now_dt,
        )
        event_type = (
            EVENT_TYPE_PENNY if transition.entered_penny else EVENT_TYPE_FINAL
        )
        inserted = await publish_verified_retailer_event(
            db,
            event_key=hd_penny_event_key(
                event_type=event_type,
                product_id=fresh.product_id,
                store_id=fresh.store_id,
                stage_entered_at=fresh.stage_entered_at or now_dt,
            ),
            retailer="home_depot",
            product_key=f"hd:{fresh.product_id}:{fresh.store_id or 'online'}",
            event_type=event_type,
            candidate=candidate,
            source_verified_at=now_dt.isoformat(),
        )
        summary.events_published += int(inserted)

    await _set_meta(conn, day_key, str(used_today))
    await _set_meta(conn, "last_cycle_at", now_dt.isoformat())
    await _set_meta(
        conn,
        "last_cycle_summary",
        json.dumps(
            {
                "tracked": summary.tracked,
                "polled": summary.polled,
                "credits": summary.credits_used,
                "transitions": summary.stage_transitions,
                "events": summary.events_published,
                "failures": summary.failures,
            }
        ),
    )
    await conn.commit()
    return summary


def hd_penny_event_key(
    *, event_type: str, product_id: str, store_id: str, stage_entered_at: datetime
) -> str:
    stamp = _utc(stage_entered_at).strftime("%Y%m%d%H%M%S")
    return f"hd-penny:{event_type}:{product_id}:{store_id or 'online'}:{stamp}"


def build_hd_penny_event_candidate(
    item: TrackedItem,
    *,
    detail: Any = None,
    effective_store_id: str | None = None,
    now: datetime | None = None,
) -> SourceCandidate:
    """The durable candidate snapshot carried by a penny-tracker event."""

    now_dt = _utc(now)
    current = (item.price_cents or 0) / 100.0
    reference = (item.reference_cents or 0) / 100.0 or None
    discount = None
    if reference is not None and reference > current:
        discount = round((reference - current) / reference * 100.0, 2)
    event_type = EVENT_TYPE_PENNY if item.stage == PennyStage.PENNY else EVENT_TYPE_FINAL
    store_label = item.store_id or effective_store_id or "online"
    reference_source = getattr(detail, "reference_price_source", None)
    attrs = {
        "hdPennyProof": "yes",
        "hdPennyStage": item.stage.value,
        "hdPennyEventType": event_type,
        "hdPennyStore": str(store_label),
        "hdPennyStageEnteredAt": (
            item.stage_entered_at.isoformat() if item.stage_entered_at else ""
        ),
        "hdPennyObservedAt": now_dt.isoformat(),
    }
    if item.sku:
        attrs["store_sku_number"] = str(item.sku)
    return SourceCandidate(
        source_key="home_depot_penny_tracker",
        retailer="Home Depot",
        title=item.title,
        product_url=item.product_url,
        current_price=current,
        typical_price=reference,
        image_url=item.image_url,
        deal_lane=(
            "hd_penny_penny" if item.stage == PennyStage.PENNY else "hd_penny_final_stage"
        ),
        api_current_price=current,
        api_reference_price=reference,
        api_discount_percent=discount,
        api_price_path="serpapi.home_depot_product.price",
        api_reference_path=(
            f"serpapi.home_depot_product.{reference_source}"
            if reference_source
            else "sniperplug.hd_penny_tracker.observed_history"
        ),
        direct_product_url=item.product_url,
        product_id=item.product_id,
        product_id_type="home_depot_product_id",
        sku=item.sku,
        seller_name="Home Depot",
        variant_attributes=attrs,
        signals=[
            "Home Depot penny tracker observation (SerpApi product price)",
            f"clearance stage: {item.stage.value}",
            f"store scope: {store_label}",
        ],
    )


async def _mark_poll_failure(db: Any, item_id: int, *, now: datetime) -> None:
    conn = db.require_conn()
    now_iso = _utc(now).isoformat()
    await conn.execute(
        f"""
        UPDATE {ITEMS_TABLE}
        SET consecutive_failures = consecutive_failures + 1,
            last_polled_at = ?, updated_at = ?
        WHERE item_id = ?
        """,
        (now_iso, now_iso, int(item_id)),
    )
    await conn.commit()


# ---------------------------------------------------------------------------
# Learned stage timing


def stage_dwells_from_history(
    rows: list[tuple[str, datetime]],
) -> dict[str, list[float]]:
    """Completed stage durations (days) from one item's (stage, at) history."""

    dwells: dict[str, list[float]] = {}
    current_stage: str | None = None
    entered_at: datetime | None = None
    for stage, observed_at in rows:
        if current_stage is None:
            current_stage, entered_at = stage, observed_at
            continue
        if stage != current_stage:
            assert entered_at is not None
            days = (observed_at - entered_at).total_seconds() / 86400.0
            if days > 0:
                dwells.setdefault(current_stage, []).append(days)
            current_stage, entered_at = stage, observed_at
    return dwells


async def _history_rows(
    conn: Any, item_id: int | None = None
) -> list[tuple[int, str, datetime]]:
    if item_id is not None:
        cursor = await conn.execute(
            f"SELECT item_id, stage, observed_at FROM {HISTORY_TABLE} "
            "WHERE item_id = ? ORDER BY observed_at ASC, history_id ASC",
            (int(item_id),),
        )
    else:
        cursor = await conn.execute(
            f"SELECT item_id, stage, observed_at FROM {HISTORY_TABLE} "
            "ORDER BY item_id ASC, observed_at ASC, history_id ASC"
        )
    rows: list[tuple[int, str, datetime]] = []
    for row in await cursor.fetchall():
        observed = _parse_iso(_row_get(row, "observed_at", 2))
        if observed is None:
            continue
        rows.append(
            (int(_row_get(row, "item_id", 0)), str(_row_get(row, "stage", 1)), observed)
        )
    return rows


async def learned_stage_dwell_days(
    db: Any, *, item_id: int, stage: PennyStage
) -> float | None:
    """Observed dwell for a stage: the item's own history first, then the
    median across all tracked items (needs >= 2 samples), else None."""

    await ensure_hd_penny_tables(db)
    conn = db.require_conn()
    own = stage_dwells_from_history(
        [(stage_name, at) for _, stage_name, at in await _history_rows(conn, item_id)]
    )
    own_samples = own.get(stage.value) or []
    if own_samples:
        return float(median(own_samples))
    all_rows = await _history_rows(conn)
    by_item: dict[int, list[tuple[str, datetime]]] = {}
    for row_item_id, stage_name, at in all_rows:
        by_item.setdefault(row_item_id, []).append((stage_name, at))
    global_samples: list[float] = []
    for item_rows in by_item.values():
        global_samples.extend(
            stage_dwells_from_history(item_rows).get(stage.value) or []
        )
    if len(global_samples) >= 2:
        return float(median(global_samples))
    return None


# ---------------------------------------------------------------------------
# Status snapshot (menu page)


async def load_hd_penny_status(
    db: Any, *, config: HdPennyConfig | None = None, now: datetime | None = None
) -> HdPennyStatus:
    cfg = config or HdPennyConfig.from_env()
    key_configured = bool(os.getenv("SERPAPI_API_KEY", "").strip())
    await ensure_hd_penny_tables(db)
    conn = db.require_conn()
    now_dt = _utc(now)
    items = await list_hd_penny_items(db, limit=1000)
    watching_items = [item for item in items if item.status == "watching"]
    pennied_items = [item for item in items if item.status == "pennied"]
    stage_counts: dict[str, int] = {}
    for item in watching_items:
        stage_counts[item.stage.value] = stage_counts.get(item.stage.value, 0) + 1

    display: list[HdPennyStatusItem] = []
    ordered = sorted(
        [*watching_items, *pennied_items],
        key=lambda item: (
            0 if item.status == "watching" else 1,
            stage_sort_priority(item.stage),
            item.stage_entered_at.isoformat() if item.stage_entered_at else "",
            item.item_id,
        ),
    )
    for item in ordered[:25]:
        days_in_stage = None
        if item.stage_entered_at is not None:
            days_in_stage = max(
                0.0, (now_dt - item.stage_entered_at).total_seconds() / 86400.0
            )
        estimate = None
        if item.status == "watching" and item.stage_entered_at is not None:
            learned = await learned_stage_dwell_days(
                db, item_id=item.item_id, stage=item.stage
            )
            estimate = estimate_next_drop(
                item.stage, item.stage_entered_at, learned_dwell_days=learned
            )
        display.append(
            HdPennyStatusItem(
                title=item.title,
                product_url=item.product_url,
                price_cents=item.price_cents,
                reference_cents=item.reference_cents,
                stage=item.stage,
                days_in_stage=days_in_stage,
                estimate=estimate,
                store_label=item.store_id or cfg.store_id or "online",
                status=item.status,
            )
        )
    return HdPennyStatus(
        key_configured=key_configured,
        enabled=cfg.enabled,
        watching=len(watching_items),
        pennied=len(pennied_items),
        stage_counts=stage_counts,
        items=tuple(display),
        last_cycle_at=await _get_meta(conn, "last_cycle_at"),
        last_cycle_summary=await _get_meta(conn, "last_cycle_summary"),
    )


def stage_label(stage: PennyStage) -> str:
    return STAGE_LABELS[stage]


# ---------------------------------------------------------------------------
# Internals


async def _find_item(
    conn: Any, product_id: str, store_id: str
) -> TrackedItem | None:
    cursor = await conn.execute(
        f"SELECT * FROM {ITEMS_TABLE} WHERE product_id = ? AND store_id = ? LIMIT 1",
        (product_id, store_id),
    )
    row = await cursor.fetchone()
    return _item_from_row(row) if row is not None else None


async def _append_history(
    conn: Any, item_id: int, price_cents: int, stage: PennyStage, observed_iso: str
) -> None:
    await conn.execute(
        f"INSERT INTO {HISTORY_TABLE} (item_id, price_cents, stage, observed_at) "
        "VALUES (?, ?, ?, ?)",
        (int(item_id), int(price_cents), stage.value, observed_iso),
    )


async def _get_meta(conn: Any, key: str) -> str | None:
    cursor = await conn.execute(
        f"SELECT value FROM {META_TABLE} WHERE key = ?", (key,)
    )
    row = await cursor.fetchone()
    if row is None:
        return None
    value = _row_get(row, "value", 0)
    return str(value) if value is not None else None


async def _set_meta(conn: Any, key: str, value: str) -> None:
    await conn.execute(
        f"INSERT INTO {META_TABLE} (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def _item_from_row(row: Any) -> TrackedItem:
    stage_raw = str(_row_get(row, "stage", 10) or "unknown")
    try:
        stage = PennyStage(stage_raw)
    except ValueError:
        stage = PennyStage.UNKNOWN
    return TrackedItem(
        item_id=int(_row_get(row, "item_id", 0)),
        product_id=str(_row_get(row, "product_id", 1) or ""),
        store_id=str(_row_get(row, "store_id", 2) or ""),
        zip_code=_clean(_row_get(row, "zip_code", 3)),
        sku=_clean(_row_get(row, "sku", 4)),
        title=str(_row_get(row, "title", 5) or ""),
        product_url=str(_row_get(row, "product_url", 6) or ""),
        image_url=_clean(_row_get(row, "image_url", 7)),
        price_cents=_int_or_none(_row_get(row, "current_price_cents", 8)),
        reference_cents=_int_or_none(_row_get(row, "reference_price_cents", 9)),
        stage=stage,
        stage_entered_at=_parse_iso(_row_get(row, "stage_entered_at", 11)),
        status=str(_row_get(row, "status", 12) or "watching"),
        source=str(_row_get(row, "source", 13) or "manual"),
        guild_id=_int_or_none(_row_get(row, "guild_id", 14)),
        consecutive_failures=int(_row_get(row, "consecutive_failures", 15) or 0),
        first_seen_at=_parse_iso(_row_get(row, "first_seen_at", 16))
        or datetime.now(timezone.utc),
        last_seen_at=_parse_iso(_row_get(row, "last_seen_at", 17))
        or datetime.now(timezone.utc),
        last_polled_at=_parse_iso(_row_get(row, "last_polled_at", 18)),
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


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def _int_or_none(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_iso(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return _utc(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        return _utc(datetime.fromisoformat(text.replace("Z", "+00:00")))
    except ValueError:
        return None


def _utc(value: datetime | None) -> datetime:
    result = value or datetime.now(timezone.utc)
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def _env_bool(name: str, *, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "").strip() or default)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "").strip() or default)
    except ValueError:
        return default
