from __future__ import annotations

from dataclasses import dataclass

from sniperplug.providers.base import ProviderScanRequest
from sniperplug.services.opportunity_watchlist import OpportunityCategory, high_demand_categories
from sniperplug.services.source_registry import RetailSource, high_priority_sources, sources_for_category


@dataclass(frozen=True)
class SnipePlan:
    """A provider-agnostic source-first scan plan.

    This does not perform network calls. It decides what should be scanned first
    so future providers can hunt source data before social media posts appear.
    """
    source_key: str
    source_name: str
    category_key: str
    category_label: str
    priority: int
    cadence_seconds: int
    queries: tuple[str, ...]
    reason: str


@dataclass(frozen=True)
class SnipePlanBatch:
    plans: tuple[SnipePlan, ...]

    def to_provider_requests(self, max_results: int = 25) -> tuple[ProviderScanRequest, ...]:
        requests: list[ProviderScanRequest] = []
        for plan in self.plans:
            for query in plan.queries:
                requests.append(
                    ProviderScanRequest(
                        source_key=plan.source_key,
                        category=plan.category_key,
                        query=query,
                        max_results=max_results,
                        metadata={
                            "cadence_seconds": str(plan.cadence_seconds),
                            "priority": str(plan.priority),
                            "reason": plan.reason,
                        },
                    )
                )
        return tuple(requests)


HOT_CATEGORY_KEYS: tuple[str, ...] = (
    "gpus",
    "brand_direct_electronics",
    "ram",
    "ssds",
    "cpus",
    "apple",
    "sneakers",
    "gold_jewelry",
    "motor_oil",
    "tools",
    "business_bulk",
)

ZERO_PRICE_HUNT_TERMS: tuple[str, ...] = (
    "$0",
    "$0.00",
    "$0.01",
    "free",
    "100% off",
)


def build_default_snipe_batch(limit_sources: int = 18, limit_categories: int = 12) -> SnipePlanBatch:
    sources = high_priority_sources(limit_sources)
    categories = [category for category in high_demand_categories(limit_categories) if category.key in HOT_CATEGORY_KEYS]

    plans: list[SnipePlan] = []
    for source in sources:
        for category in categories:
            if category_matches_source(category, source):
                plans.append(build_plan(source, category))

    return SnipePlanBatch(plans=tuple(sorted(plans, key=lambda plan: plan.priority, reverse=True)))


def build_plans_for_category(category_key: str) -> SnipePlanBatch:
    category = next((item for item in high_demand_categories(50) if item.key == category_key), None)
    if category is None:
        return SnipePlanBatch(plans=())

    sources = sources_for_category(category_key)
    plans = [build_plan(source, category) for source in sources]
    return SnipePlanBatch(plans=tuple(sorted(plans, key=lambda plan: plan.priority, reverse=True)))


def build_plan(source: RetailSource, category: OpportunityCategory) -> SnipePlan:
    priority = source.priority + category.demand_level
    cadence = cadence_for_priority(priority)
    queries = top_terms(category)
    return SnipePlan(
        source_key=source.key,
        source_name=source.name,
        category_key=category.key,
        category_label=category.label,
        priority=priority,
        cadence_seconds=cadence,
        queries=queries,
        reason=f"Source-first watch: {source.name} + {category.label}",
    )


def category_matches_source(category: OpportunityCategory, source: RetailSource) -> bool:
    category_key = category.key
    source_categories = set(source.categories)

    if category_key in source_categories:
        return True

    compatibility: dict[str, set[str]] = {
        "gpus": {"electronics", "computer_parts", "gaming"},
        "cpus": {"electronics", "computer_parts", "computers", "gaming"},
        "ram": {"electronics", "computer_parts", "computers", "business"},
        "ssds": {"electronics", "computer_parts", "computers", "business"},
        "apple": {"electronics", "apple", "phones", "computers", "tablets", "wearables"},
        "brand_direct_electronics": {"electronics", "phones", "tvs", "appliances", "monitors", "gaming"},
        "sneakers": {"sneakers", "apparel", "sportswear", "streetwear"},
        "gold_jewelry": {"jewelry", "gold_jewelry", "diamonds", "watches", "warehouse"},
        "motor_oil": {"automotive", "motor_oil", "fluids"},
        "tools": {"tools", "home", "automotive", "garage"},
        "business_bulk": {"business", "office", "bulk", "warehouse"},
    }
    return bool(compatibility.get(category_key, set()) & source_categories)


def cadence_for_priority(priority: int) -> int:
    """Suggested scan cadence.

    Providers must still respect retailer limits and terms. This is only a
    priority planner, not permission to hammer websites.
    """
    if priority >= 190:
        return 30
    if priority >= 175:
        return 60
    if priority >= 160:
        return 180
    return 300


def top_terms(category: OpportunityCategory, max_terms: int = 8) -> tuple[str, ...]:
    terms = list(category.terms[:max_terms])

    # Near-zero terms are not standalone alert triggers. They are search hints
    # for providers that support feed/query scans. Real anomaly scoring still
    # requires product/price evidence.
    if category.key in {"gpus", "brand_direct_electronics", "apple", "gold_jewelry", "business_bulk"}:
        terms.extend(ZERO_PRICE_HUNT_TERMS)

    return tuple(dict.fromkeys(terms))
