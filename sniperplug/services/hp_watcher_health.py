from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sniperplug.hp_watcher.storage import (
    HEALTH_TABLE,
    PRODUCT_TABLE,
    SITEMAP_TABLE,
    ensure_hp_watcher_tables,
)
from sniperplug.services.verified_retailer_events import EVENT_TABLE, ensure_verified_retailer_event_table


@dataclass(frozen=True)
class HPWatcherHealth:
    status: str = "not_started"
    last_successful_cycle_at: str = ""
    products: int = 0
    identified_products: int = 0
    active_markdowns: int = 0
    due_product_pages: int = 0
    due_offers: int = 0
    pending_events: int = 0
    sitemap_failures: int = 0
    product_failures: int = 0
    stale: bool = True
    last_error: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "healthy" and not self.stale

    def summary_line(self) -> str:
        state = "healthy" if self.ok else "degraded" if self.status == "degraded" else self.status
        return (
            f"HP watcher **{state}** • catalog **{self.identified_products}/{self.products} identified** • "
            f"active markdowns **{self.active_markdowns}** • due pages/offers **{self.due_product_pages}/{self.due_offers}** • "
            f"pending fanout **{self.pending_events}** • source/product failures **{self.sitemap_failures}/{self.product_failures}**"
        )


async def load_hp_watcher_health(db: Any) -> HPWatcherHealth:
    await ensure_hp_watcher_tables(db)
    await ensure_verified_retailer_event_table(db)
    conn = db.require_conn()
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()

    state_cursor = await conn.execute(
        f"SELECT state_key, state_value FROM {HEALTH_TABLE}"
    )
    state = {
        str(_row_get(row, "state_key", 0) or ""): str(_row_get(row, "state_value", 1) or "")
        for row in await state_cursor.fetchall()
    }

    counts: dict[str, int] = {}
    queries = {
        "products": f"SELECT COUNT(*) FROM {PRODUCT_TABLE}",
        "identified_products": f"SELECT COUNT(*) FROM {PRODUCT_TABLE} WHERE sku <> '' AND catalog_entry_id <> ''",
        "active_markdowns": f"SELECT COUNT(*) FROM {PRODUCT_TABLE} WHERE current_price_cents > 0 AND reference_price_cents > current_price_cents",
        "due_product_pages": f"SELECT COUNT(*) FROM {PRODUCT_TABLE} WHERE page_next_check_at <= ?",
        "due_offers": f"SELECT COUNT(*) FROM {PRODUCT_TABLE} WHERE sku <> '' AND catalog_entry_id <> '' AND offer_next_check_at <= ?",
        "sitemap_failures": f"SELECT COUNT(*) FROM {SITEMAP_TABLE} WHERE consecutive_failures > 0",
        "product_failures": f"SELECT COUNT(*) FROM {PRODUCT_TABLE} WHERE consecutive_page_failures > 0 OR consecutive_offer_failures > 0",
        "pending_events": f"SELECT COUNT(*) FROM {EVENT_TABLE} WHERE retailer = 'hp' AND processed_at IS NULL",
    }
    for key, sql in queries.items():
        cursor = await conn.execute(sql, (now_iso,)) if "?" in sql else await conn.execute(sql)
        row = await cursor.fetchone()
        counts[key] = int(_row_get(row, "COUNT(*)", 0) or 0)

    last_successful = state.get("last_successful_cycle_at", "")
    parsed = _parse_datetime(last_successful)
    stale = parsed is None or parsed < now - timedelta(minutes=15)
    return HPWatcherHealth(
        status=state.get("service_status", "not_started"),
        last_successful_cycle_at=last_successful,
        products=counts["products"],
        identified_products=counts["identified_products"],
        active_markdowns=counts["active_markdowns"],
        due_product_pages=counts["due_product_pages"],
        due_offers=counts["due_offers"],
        pending_events=counts["pending_events"],
        sitemap_failures=counts["sitemap_failures"],
        product_failures=counts["product_failures"],
        stale=stale,
        last_error=state.get("last_cycle_error", ""),
    )


def _parse_datetime(value: str) -> datetime | None:
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
