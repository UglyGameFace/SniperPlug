from __future__ import annotations

from collections.abc import Mapping

from sniperplug.cogs.deal_scanner import HuntPreset
from sniperplug.services import verified_discount_hunt
from sniperplug.services.open_box_autoscan_routes import OPEN_BOX_AUTOSCAN_KEY, open_box_autoscan_preset
from sniperplug.services.walmart_cash_offers import DEFAULT_CASH_QUERIES


PRIVATE_WALMART_CASH_ROUTES: tuple[str, ...] = tuple(DEFAULT_CASH_QUERIES)

PRIVATE_PROMO_ROUTE_TERMS: tuple[str, ...] = (
    "walmart cash",
    "cash reward",
    "cash rewards",
    "cash back",
    "cashback",
    "onepay",
    "one pay",
)

PUBLIC_AUTOSCAN_FALLBACK_ROUTES: tuple[str, ...] = (
    "walmart deals",
    "rollback",
    "clearance",
    "open box electronics",
    "restored electronics",
)

PUBLIC_AUTOSCAN_ROUTE_POLICY_NOTE = (
    "Public Walmart autoscan routes exclude Walmart Cash, OnePay, and generic cashback terms. "
    "Cash Finder owns those private diagnostics and Walmart Cash never public-posts as markdown/open-box."
)


def is_private_promo_route(query: str) -> bool:
    text = " ".join(str(query or "").lower().replace("-", " ").split())
    return any(term in text for term in PRIVATE_PROMO_ROUTE_TERMS)


def public_autoscan_queries(queries: tuple[str, ...] | list[str], *, fallback: tuple[str, ...] = PUBLIC_AUTOSCAN_FALLBACK_ROUTES) -> tuple[str, ...]:
    """Return routes safe for public markdown/open-box autoscan.

    This deliberately removes private promo/Cash routes without weakening the
    later public proof gates. Routes like rollback, clearance, open-box,
    restored, and refurbished remain eligible because they can produce real
    public markdown or condition-lane candidates.

    If future edits accidentally make a category Cash-only, the fallback keeps
    autoscan from silently checking zero routes.
    """

    cleaned = dedupe_public_routes(queries or ())
    if cleaned:
        return cleaned
    return dedupe_public_routes(fallback or ())


def dedupe_public_routes(queries: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for query in queries or ():
        text = " ".join(str(query or "").split())
        key = text.lower()
        if not text or key in seen or is_private_promo_route(text):
            continue
        seen.add(key)
        cleaned.append(text)
    return tuple(cleaned)


def public_autoscan_hunt_presets(base_presets: Mapping[str, HuntPreset] | None = None) -> dict[str, HuntPreset]:
    """Build the public-safe autoscan preset map without mutating other modules."""

    source = base_presets or verified_discount_hunt.HUNT_PRESETS
    presets: dict[str, HuntPreset] = {}
    for key, preset in source.items():
        queries = public_autoscan_queries(tuple(preset.queries))
        presets[key] = HuntPreset(
            preset.key,
            preset.label,
            preset.emoji,
            preset.description,
            queries,
            preset.min_discount,
        )
    presets[OPEN_BOX_AUTOSCAN_KEY] = open_box_autoscan_preset()
    return presets
