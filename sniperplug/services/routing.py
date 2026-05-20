from __future__ import annotations

from dataclasses import dataclass

from sniperplug.models.deal import NormalizedDeal


DEFAULT_ROUTE = "default"
PRICE_GLITCH_ROUTE = "price_glitch"
HOT_DEALS_ROUTE = "hot_deals"
AMAZON_ROUTE = "amazon"
YMMV_ROUTE = "ymmv"
STAFF_REVIEW_ROUTE = "staff_review"
DEAD_REPORTS_ROUTE = "dead_reports"

ALERT_ROUTES: tuple[str, ...] = (
    DEFAULT_ROUTE,
    PRICE_GLITCH_ROUTE,
    HOT_DEALS_ROUTE,
    AMAZON_ROUTE,
    YMMV_ROUTE,
    STAFF_REVIEW_ROUTE,
    DEAD_REPORTS_ROUTE,
)

ROUTE_LABELS: dict[str, str] = {
    DEFAULT_ROUTE: "Default Deals",
    PRICE_GLITCH_ROUTE: "Price Glitches",
    HOT_DEALS_ROUTE: "Hot Deals",
    AMAZON_ROUTE: "Amazon Deals",
    YMMV_ROUTE: "YMMV Deals",
    STAFF_REVIEW_ROUTE: "Staff Review",
    DEAD_REPORTS_ROUTE: "Dead Deal Reports",
}

ROUTE_DESCRIPTIONS: dict[str, str] = {
    DEFAULT_ROUTE: "Fallback channel for normal alerts and anything not routed elsewhere.",
    PRICE_GLITCH_ROUTE: "Possible price errors, extreme discounts, and fast-moving glitches.",
    HOT_DEALS_ROUTE: "Strong discounts that are not necessarily glitches.",
    AMAZON_ROUTE: "Amazon-specific alerts.",
    YMMV_ROUTE: "Deals that may vary by account, ZIP, Prime status, seller, or final checkout.",
    STAFF_REVIEW_ROUTE: "Private staff lane for deals that need manual review before public posting.",
    DEAD_REPORTS_ROUTE: "Future route for dead deal reports and cleanup signals.",
}


@dataclass(frozen=True)
class RouteDecision:
    route: str
    reason: str


def choose_primary_route(deal: NormalizedDeal) -> RouteDecision:
    """
    Choose one primary route for a deal.

    SniperPlug avoids fear-based public buckets. Deals that need extra caution are
    routed as YMMV or Staff Review instead of being called risky.
    """
    if deal.risk_level.lower() == "high":
        return RouteDecision(STAFF_REVIEW_ROUTE, "staff review before public posting")

    if deal.is_possible_price_error:
        return RouteDecision(PRICE_GLITCH_ROUTE, "possible price glitch")

    if deal.is_ymmv:
        return RouteDecision(YMMV_ROUTE, "may vary by account or checkout")

    if deal.retailer.lower() == "amazon":
        return RouteDecision(AMAZON_ROUTE, "Amazon alert")

    if (deal.discount_percent or 0) >= 40:
        return RouteDecision(HOT_DEALS_ROUTE, "hot deal discount")

    return RouteDecision(DEFAULT_ROUTE, "default fallback")


def is_valid_route(route: str) -> bool:
    return route in ALERT_ROUTES


def route_label(route: str) -> str:
    return ROUTE_LABELS.get(route, route.replace("_", " ").title())
