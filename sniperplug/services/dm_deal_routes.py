from __future__ import annotations

"""Personal DM delivery can follow the same *verified* deal-type routes as channels.

Route subscriptions are optional and stored in the existing categories_json field,
so subscribers retain their current preferences without a table migration.
"""

from typing import Any, Iterable

from sniperplug.models.deal import NormalizedDeal
from sniperplug.services.routing import (
    BUSINESS_ROUTE,
    COUPON_STACK_ROUTE,
    DEFAULT_ROUTE,
    HOT_DEALS_ROUTE,
    PRICE_GLITCH_ROUTE,
    ROUTE_LABELS,
    YMMV_ROUTE,
    choose_primary_route,
)

DM_ROUTE_PREFIX = "dm_route:"
# Staff-review and dead-report routes are not consumer deal notifications.
# Amazon is intentionally excluded until its verified events feed personal DMs.
PERSONAL_DEAL_ROUTES: tuple[str, ...] = (
    PRICE_GLITCH_ROUTE,
    HOT_DEALS_ROUTE,
    COUPON_STACK_ROUTE,
    BUSINESS_ROUTE,
    YMMV_ROUTE,
    DEFAULT_ROUTE,
)


def route_labels() -> tuple[tuple[str, str], ...]:
    return tuple((route, ROUTE_LABELS[route]) for route in PERSONAL_DEAL_ROUTES)


def normalize_dm_routes(values: Iterable[str] | str | None) -> tuple[str, ...]:
    if values is None:
        return ()
    source = values.replace("\n", ",").split(",") if isinstance(values, str) else values
    aliases = {
        "".join(ch if ch.isalnum() else "_" for ch in label.lower()).strip("_"): route
        for route, label in route_labels()
    }
    selected: list[str] = []
    for value in source:
        key = "_".join(str(value or "").strip().lower().replace("-", "_").split())
        key = aliases.get(key, key)
        if key in PERSONAL_DEAL_ROUTES and key not in selected:
            selected.append(key)
    return tuple(selected)


def split_dm_route_preferences(categories: Iterable[str] | None) -> tuple[str, ...]:
    return normalize_dm_routes(
        str(token)[len(DM_ROUTE_PREFIX):]
        for token in (categories or ())
        if str(token).startswith(DM_ROUTE_PREFIX)
    )


def dm_route_tokens(routes: Iterable[str] | str | None) -> tuple[str, ...]:
    return tuple(f"{DM_ROUTE_PREFIX}{route}" for route in normalize_dm_routes(routes))


def verified_card_route(card: Any) -> str | None:
    """Use the shared route authority, never guess 'glitch' from a title.

    Cards must retain their evaluated NormalizedDeal. Unknown/unsupported
    metadata fails closed for route-only subscribers.
    """
    deal = getattr(card, "deal", None)
    if not isinstance(deal, NormalizedDeal):
        return None
    route = choose_primary_route(deal).route
    return route if route in PERSONAL_DEAL_ROUTES else None
