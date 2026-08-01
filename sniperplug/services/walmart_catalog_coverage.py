from __future__ import annotations

import time
from collections.abc import Mapping

from sniperplug.cogs.deal_scanner import HuntPreset
from sniperplug.services.autoscan_route_policy import is_private_promo_route


SCHEDULED_COVERAGE_SLOT_SECONDS = 6 * 60 * 60
MANUAL_COVERAGE_SLOT_SECONDS = 15 * 60
SCHEDULED_ROUTE_COUNT_HINT = 4

# Broad sale terms are discovery only. Every result still has to pass the
# official exact-item detail gate before it can become a card or price proof.
CORE_SALE_ROUTES: tuple[str, ...] = (
    "clearance",
    "rollback",
    "walmart deals",
    "price drop",
    "reduced price",
    "special buy",
    "online clearance",
)

# This permanent narrow lane exists because broad `tool clearance` searches can
# bury Walmart house-brand items below the first bounded pages. It covers the
# demonstrated HART brushless jigsaw miss while also finding the rest of that
# compact HART brushless catalog.
HART_CORE_CLEARANCE_ROUTE = "hart brushless tools clearance"

# These probes intentionally use narrow brand/category combinations. The global
# exact-detail queue deduplicates the resulting item IDs and verifies each offer
# once, so stronger discovery coverage does not weaken public proof.
CATALOG_PROBE_ROUTES: tuple[str, ...] = (
    "hart jigsaw clearance",
    "hart 20v clearance",
    "hart saw clearance",
    "hart power tools clearance",
    "hyper tough tools clearance",
    "onn electronics clearance",
    "onn tv clearance",
    "mainstays clearance",
    "better homes gardens clearance",
    "shark vacuum clearance",
    "ninja appliance clearance",
    "gaming monitor clearance",
    "prepaid phone clearance",
    "lego clearance",
    "patio furniture clearance",
    "designer fragrance clearance",
)

DEPARTMENT_ROTATION: tuple[str, ...] = (
    "tech",
    "home",
    "beauty",
    "toys",
    "essentials",
    "open_box",
)

AUTO_TOOLS_FALLBACK: tuple[str, ...] = (
    "tool clearance",
    "tool rollback",
    "auto clearance",
    "drill clearance",
    "dewalt clearance",
    "milwaukee clearance",
    "hart tools clearance",
    "pressure washer clearance",
    "car care clearance",
    "motor oil rollback",
)


def build_complete_broad_preset(
    presets: Mapping[str, HuntPreset],
    *,
    guild_id: int,
    query_count: int,
    now: float | None = None,
) -> HuntPreset:
    """Build a deterministic broad preset without six-hour rotation gaps.

    The old selector used a 15-minute bucket even though scheduled scans have a
    six-hour safety floor. A scheduled run therefore advanced 24 buckets at a
    time; for route lists sharing a divisor with 24, some entries could remain
    unreachable indefinitely. This planner advances exactly one coverage slot
    per scheduled opportunity while preserving the four-route production cap.
    """

    count = max(1, int(query_count))
    timestamp = time.time() if now is None else float(now)
    scheduled = count <= SCHEDULED_ROUTE_COUNT_HINT
    slot_seconds = (
        SCHEDULED_COVERAGE_SLOT_SECONDS if scheduled else MANUAL_COVERAGE_SLOT_SECONDS
    )
    slot = int(timestamp // slot_seconds)
    salt = abs(int(guild_id))

    selected: list[str] = []
    seen: set[str] = set()

    def add(query: str | None) -> None:
        text = " ".join(str(query or "").split())
        key = text.lower()
        if (
            not text
            or key in seen
            or len(selected) >= count
            or is_private_promo_route(text)
        ):
            return
        seen.add(key)
        selected.append(text)

    # Lane 1: broad official sale discovery.
    add(_rotating_value(CORE_SALE_ROUTES, slot=slot, salt=salt))

    # Lane 2: guaranteed compact HART brushless catalog coverage. This catches
    # the demonstrated HART 20V orbital jigsaw without hard-coding one item ID.
    add(HART_CORE_CLEARANCE_ROUTE)

    # Lane 3: rotate the other major departments, then rotate within the chosen
    # department. Consecutive scheduled slots move by one real six-hour step.
    department_key = _rotating_value(DEPARTMENT_ROTATION, slot=slot, salt=salt // 31)
    department = presets.get(str(department_key))
    department_queries = tuple(getattr(department, "queries", ()) or ())
    if department_queries:
        department_slot = slot // max(1, len(DEPARTMENT_ROTATION))
        add(_rotating_value(department_queries, slot=department_slot, salt=salt // 47))

    # Lane 4+ (and extra manual lanes): one complete tools + narrow catalog pool.
    # Walking one position per real scheduled slot guarantees every tool and
    # catalog probe is reachable without increasing the production route cap.
    auto_tools = tuple(getattr(presets.get("auto_tools"), "queries", ()) or ())
    if not auto_tools:
        auto_tools = AUTO_TOOLS_FALLBACK
    coverage_pool = _dedupe((*auto_tools, *CATALOG_PROBE_ROUTES))
    if coverage_pool:
        coverage_start = (slot + salt // 17) % len(coverage_pool)
        for offset in range(len(coverage_pool)):
            add(coverage_pool[(coverage_start + offset) % len(coverage_pool)])
            if len(selected) >= count:
                break

    # Defensive fallback when a future preset edit empties a department or
    # duplicates a probe. `add()` enforces the same private-promo exclusion for
    # every lane, including this fallback, so no Cash/OnePay route can leak in.
    fallback = presets.get("deal_week") or presets.get("all")
    fallback_queries = tuple(getattr(fallback, "queries", ()) or ())
    if fallback_queries and len(selected) < count:
        fallback_start = (slot + salt // 61) % len(fallback_queries)
        for offset in range(len(fallback_queries)):
            add(fallback_queries[(fallback_start + offset) % len(fallback_queries)])
            if len(selected) >= count:
                break

    base = presets.get("deal_week") or presets.get("all") or next(iter(presets.values()))
    return HuntPreset(
        "broad_public_safe",
        "Broad Public-Safe Sweep",
        "🌐",
        (
            "Four-lane Walmart discovery with exact-detail verification: core sale surface, "
            "HART brushless clearance, department rotation, and a complete tools/catalog probe pool."
        ),
        tuple(selected[:count]),
        int(getattr(base, "min_discount", 50) or 50),
    )


def _rotating_value(values: tuple[str, ...], *, slot: int, salt: int) -> str:
    if not values:
        return ""
    return values[(int(slot) + int(salt)) % len(values)]


def _dedupe(values: tuple[str, ...]) -> tuple[str, ...]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = " ".join(str(value or "").split())
        key = text.lower()
        if not text or key in seen or is_private_promo_route(text):
            continue
        seen.add(key)
        output.append(text)
    return tuple(output)
