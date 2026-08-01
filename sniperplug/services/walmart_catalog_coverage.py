from __future__ import annotations

import math
import time
from dataclasses import dataclass

from sniperplug.services import verified_discount_hunt as hunt
from sniperplug.services.walmart_cash_offers import DEFAULT_CASH_QUERIES


ROTATION_SECONDS = 15 * 60

# Broad department seeds fill gaps that the deal-focused curated routes do not
# explicitly name. These are discovery routes only; every returned item still
# has to pass exact-detail identity, price, seller, and markdown proof.
BROAD_DEPARTMENT_ROUTES: tuple[str, ...] = (
    "walmart electronics",
    "walmart cell phones",
    "walmart computers",
    "walmart video games",
    "walmart tv audio",
    "walmart home",
    "walmart furniture",
    "walmart kitchen dining",
    "walmart appliances",
    "walmart patio garden",
    "walmart tools",
    "walmart automotive",
    "walmart sports outdoors",
    "walmart toys",
    "walmart baby",
    "walmart pets",
    "walmart beauty",
    "walmart health wellness",
    "walmart personal care",
    "walmart household essentials",
    "walmart grocery",
    "walmart clothing",
    "walmart shoes",
    "walmart jewelry",
    "walmart office supplies",
    "walmart arts crafts",
    "walmart books",
    "walmart movies music",
    "walmart seasonal",
)


@dataclass(frozen=True)
class CatalogCoverageSlice:
    queries: tuple[str, ...]
    total_routes: int
    slot_index: int
    slot_count: int

    def summary_line(self) -> str:
        return (
            "Catalog-wide route rotation: "
            f"slot **{self.slot_index + 1}/{self.slot_count}** • "
            f"this pass **{len(self.queries)}** route(s) • "
            f"full route pool **{self.total_routes}**. "
            "Every discovered Walmart item ID is retained in the global exact-detail queue."
        )


def catalog_route_pool() -> tuple[str, ...]:
    """Return one deduplicated discovery pool spanning all known Walmart areas.

    The consumer search endpoint is keyword based and bounded, so no single
    request can enumerate Walmart's entire live catalog. SniperPlug instead
    rotates through every curated category, broad department, markdown surface,
    condition route, and Walmart Cash route, while the global exact-detail queue
    persists item IDs across passes.
    """

    values: list[str] = []
    for _key, (_label, _emoji, _description, queries) in hunt.CATEGORY_ROUTES.items():
        values.extend(queries)
    values.extend(BROAD_DEPARTMENT_ROUTES)
    values.extend(DEFAULT_CASH_QUERIES)
    return _dedupe(values)


def rotating_catalog_slice(
    *,
    guild_id: int,
    query_count: int,
    now: float | None = None,
) -> CatalogCoverageSlice:
    pool = catalog_route_pool()
    if not pool:
        return CatalogCoverageSlice(queries=(), total_routes=0, slot_index=0, slot_count=1)

    count = max(1, min(int(query_count), len(pool)))
    slot_count = max(1, math.ceil(len(pool) / count))
    bucket = int((time.time() if now is None else float(now)) // ROTATION_SECONDS)
    slot_index = (bucket + abs(int(guild_id))) % slot_count
    start = slot_index * count
    selected = list(pool[start : start + count])
    if len(selected) < count:
        selected.extend(pool[: count - len(selected)])

    return CatalogCoverageSlice(
        queries=tuple(selected),
        total_routes=len(pool),
        slot_index=slot_index,
        slot_count=slot_count,
    )


def _dedupe(values: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    seen: set[str] = set()
    cleaned: list[str] = []
    for value in values:
        text = " ".join(str(value or "").split())
        key = text.lower()
        if not text or key in seen:
            continue
        seen.add(key)
        cleaned.append(text)
    return tuple(cleaned)
