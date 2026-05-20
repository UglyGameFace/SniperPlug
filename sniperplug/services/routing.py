from __future__ import annotations

from dataclasses import dataclass

from sniperplug.models.deal import NormalizedDeal


DEFAULT_ROUTE = "default"
PRICE_GLITCH_ROUTE = "price_glitch"
HOT_DEALS_ROUTE = "hot_deals"
AMAZON_ROUTE = "amazon"
HIGH_RISK_ROUTE = "high_risk"
REVIEW_ROUTE = "review"
DEAD_REPORTS_ROUTE = "dead_reports"

ALERT_ROUTES: tuple[str, ...] = (
    DEFAULT_ROUTE,
    PRICE_GLITCH_ROUTE,
    HOT_DEALS_ROUTE,
    AMAZON_ROUTE,
    HIGH_RISK_ROUTE,
    REVIEW_ROUTE,
    DEAD_REPORTS_ROUTE,
)

ROUTE_LABELS: dict[str, str] = {
    DEFAULT_ROUTE: "Default Deals",
    PRICE_GLITCH_ROUTE: "Price Glitches",
    HOT_DEALS_ROUTE: "Hot Deals",
    AMAZON_ROUTE: "Amazon",
    HIGH_RISK_ROUTE: "High Risk",
    REVIEW_ROUTE: "Review Queue",
    DEAD_REPORTS_ROUTE: "Dead Deal Reports",
}

ROUTE_DESCRIPTIONS: dict[str, str] = {
    DEFAULT_ROUTE: "Fallback channel for normal alerts and anything not routed elsewhere.",
    PRICE_GLITCH_ROUTE: "Likely price errors, extreme discounts, and fast-moving glitches.",
    HOT_DEALS_ROUTE: "Strong discounts that are not necessarily glitches.",
    AMAZON_ROUTE: "Amazon-specific alerts and YMMV Amazon offers.",
    HIGH_RISK_ROUTE: "Merchant-fulfilled, third-party, renewed/used, or risky alerts.",
    REVIEW_ROUTE: "Private/staff review lane for incomplete or questionable alerts.",
    DEAD_REPORTS_ROUTE: "Future route for dead deal reports and cleanup signals.",
}


@dataclass(frozen=True)
class RouteDecision:
    route: str
    reason: str


def choose_primary_route(deal: NormalizedDeal) -> RouteDecision:
    """
    Choose one primary public route for a deal.

    This keeps SniperPlug from spamming the same alert into every matching channel.
    Priority is intentional: urgent glitches beat retailer/category routing.
    """
    if deal.risk_level.lower() == "high":
        return RouteDecision(HIGH_RISK_ROUTE, "high risk alert")

    if deal.is_possible_price_error:
        return RouteDecision(PRICE_GLITCH_ROUTE, "possible price glitch")

    if deal.retailer.lower() == "amazon":
        return RouteDecision(AMAZON_ROUTE, "Amazon alert")

    if (deal.discount_percent or 0) >= 40:
        return RouteDecision(HOT_DEALS_ROUTE, "hot deal discount")

    return RouteDecision(DEFAULT_ROUTE, "default fallback")


def is_valid_route(route: str) -> bool:
    return route in ALERT_ROUTES


def route_label(route: str) -> str:
    return ROUTE_LABELS.get(route, route.replace("_", " ").title())
