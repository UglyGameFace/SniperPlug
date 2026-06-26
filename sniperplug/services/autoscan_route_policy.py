from __future__ import annotations

from sniperplug.cogs.deal_scanner import HuntPreset
from sniperplug.services import verified_discount_hunt
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

PUBLIC_AUTOSCAN_ROUTE_POLICY_NOTE = (
    "Public Walmart autoscan routes exclude Walmart Cash, OnePay, and generic cashback terms. "
    "Cash Finder owns those private diagnostics and Walmart Cash never public-posts as markdown/open-box."
)


def is_private_promo_route(query: str) -> bool:
    text = " ".join(str(query or "").lower().replace("-", " ").split())
    return any(term in text for term in PRIVATE_PROMO_ROUTE_TERMS)


def public_autoscan_queries(queries: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    """Return routes safe for public markdown/open-box autoscan.

    This deliberately removes private promo/Cash routes without weakening the
    later public proof gates. Routes like rollback, clearance, open-box,
    restored, and refurbished remain eligible because they can produce real
    public markdown or condition-lane candidates.
    """

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


def install_public_autoscan_route_policy() -> None:
    """Scrub private promo routes out of public Walmart hunt/autoscan presets.

    This is a native route table normalization step, not a public quality gate
    bypass. It keeps Walmart Cash available through Cash Finder while preventing
    public autoscan from wasting route slots on private-only promo searches.
    """

    if getattr(verified_discount_hunt, "_sniperplug_public_autoscan_route_policy_installed", False):
        return

    filtered_routes: dict[str, tuple[str, str, str, tuple[str, ...]]] = {}
    for key, (label, emoji, description, queries) in verified_discount_hunt.CATEGORY_ROUTES.items():
        filtered_routes[key] = (label, emoji, description, public_autoscan_queries(queries))

    verified_discount_hunt.CATEGORY_ROUTES.clear()
    verified_discount_hunt.CATEGORY_ROUTES.update(filtered_routes)
    verified_discount_hunt.DISCOVERY_QUERIES = verified_discount_hunt.CATEGORY_ROUTES["all"][3]

    verified_discount_hunt.HUNT_PRESETS.clear()
    verified_discount_hunt.HUNT_PRESETS.update(
        {
            key: HuntPreset(key, label, emoji, description, queries, verified_discount_hunt.TRUE_DISCOUNT_MIN)
            for key, (label, emoji, description, queries) in verified_discount_hunt.CATEGORY_ROUTES.items()
        }
    )
    verified_discount_hunt.ALL_VERIFIED_PRESET = HuntPreset(
        verified_discount_hunt.ALL_VERIFIED_HUNT_KEY,
        verified_discount_hunt.CATEGORY_ROUTES["all"][0],
        verified_discount_hunt.CATEGORY_ROUTES["all"][1],
        verified_discount_hunt.CATEGORY_ROUTES["all"][2],
        verified_discount_hunt.DISCOVERY_QUERIES,
        verified_discount_hunt.TRUE_DISCOUNT_MIN,
    )
    verified_discount_hunt.deal_scanner.HUNT_PRESETS.clear()
    verified_discount_hunt.deal_scanner.HUNT_PRESETS.update(verified_discount_hunt.HUNT_PRESETS)
    verified_discount_hunt._sniperplug_public_autoscan_route_policy_installed = True
