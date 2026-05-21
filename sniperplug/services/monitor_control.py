from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from sniperplug.providers.base import ProviderScanRequest
from sniperplug.services.snipe_planner import SnipePlan, build_default_snipe_batch
from sniperplug.services.watchlist_seeds import best_seed_for_category, seeded_terms


class MonitorMode(str, Enum):
    PREVIEW_ONLY = "preview_only"
    STAFF_REVIEW = "staff_review"
    PUBLIC_ALLOWED = "public_allowed"


class VerificationRequirement(str, Enum):
    PRICE_AND_LINK = "price_and_link"
    PRICE_LINK_AND_IMAGE = "price_link_and_image"
    PRICE_LINK_AND_CART = "price_link_and_cart"
    PRICE_LINK_CART_AND_HISTORY = "price_link_cart_and_history"


@dataclass(frozen=True)
class MonitorTarget:
    """A controlled watch target for future live provider scans.

    This object is a control-plane definition only. It does not perform network
    calls, scrape retailers, or post alerts. Live workers/providers should use it
    to decide what is allowed to be scanned and what level of proof is required
    before an alert can go public.
    """

    monitor_id: str
    source_key: str
    source_name: str
    category_key: str
    category_label: str
    priority: int
    enabled: bool
    mode: MonitorMode
    cadence_seconds: int
    cooldown_seconds: int
    watch_terms: tuple[str, ...]
    product_ids: tuple[str, ...] = ()
    skus: tuple[str, ...] = ()
    upcs: tuple[str, ...] = ()
    verification_required: VerificationRequirement = VerificationRequirement.PRICE_AND_LINK
    route_hint: str | None = None
    seed_label: str | None = None
    min_normal_value: float | None = None
    near_zero_trigger_price: float | None = None
    last_seen_price: float | None = None
    last_alerted_price: float | None = None
    last_seen_at: str | None = None
    last_alerted_at: str | None = None

    @property
    def public_alert_allowed(self) -> bool:
        return self.mode == MonitorMode.PUBLIC_ALLOWED

    def to_provider_requests(self, max_results: int = 25) -> tuple[ProviderScanRequest, ...]:
        requests: list[ProviderScanRequest] = []

        for term in self.watch_terms:
            requests.append(
                ProviderScanRequest(
                    source_key=self.source_key,
                    category=self.category_key,
                    query=term,
                    max_results=max_results,
                    metadata=self._metadata(),
                )
            )

        for product_id in (*self.product_ids, *self.skus, *self.upcs):
            requests.append(
                ProviderScanRequest(
                    source_key=self.source_key,
                    category=self.category_key,
                    product_ids=(product_id,),
                    max_results=max_results,
                    metadata=self._metadata(),
                )
            )

        return tuple(requests)

    def _metadata(self) -> dict[str, str]:
        return {
            "monitor_id": self.monitor_id,
            "priority": str(self.priority),
            "cadence_seconds": str(self.cadence_seconds),
            "cooldown_seconds": str(self.cooldown_seconds),
            "mode": self.mode.value,
            "verification_required": self.verification_required.value,
            "route_hint": self.route_hint or "",
            "seed_label": self.seed_label or "",
            "min_normal_value": str(self.min_normal_value or ""),
            "near_zero_trigger_price": str(self.near_zero_trigger_price or ""),
        }


@dataclass(frozen=True)
class MonitorControlPlane:
    targets: tuple[MonitorTarget, ...]

    def active_targets(self) -> tuple[MonitorTarget, ...]:
        return tuple(target for target in self.targets if target.enabled)

    def public_targets(self) -> tuple[MonitorTarget, ...]:
        return tuple(target for target in self.active_targets() if target.public_alert_allowed)

    def by_source(self, source_key: str) -> tuple[MonitorTarget, ...]:
        normalized = source_key.strip().lower()
        return tuple(target for target in self.targets if target.source_key == normalized)

    def to_provider_requests(self, max_results: int = 25) -> tuple[ProviderScanRequest, ...]:
        requests: list[ProviderScanRequest] = []
        for target in self.active_targets():
            requests.extend(target.to_provider_requests(max_results=max_results))
        return tuple(requests)


def build_default_monitor_control_plane(limit_targets: int = 24) -> MonitorControlPlane:
    plans = build_default_snipe_batch(limit_sources=18, limit_categories=12).plans
    targets = tuple(build_target_from_plan(plan) for plan in plans[:limit_targets])
    return MonitorControlPlane(targets=targets)


def build_target_from_plan(plan: SnipePlan) -> MonitorTarget:
    seed = best_seed_for_category(plan.category_key)
    return MonitorTarget(
        monitor_id=f"{plan.source_key}:{plan.category_key}",
        source_key=plan.source_key,
        source_name=plan.source_name,
        category_key=plan.category_key,
        category_label=plan.category_label,
        priority=plan.priority,
        enabled=True,
        mode=mode_for_plan(plan),
        cadence_seconds=plan.cadence_seconds,
        cooldown_seconds=cooldown_for_priority(plan.priority),
        watch_terms=seeded_terms(plan.category_key, plan.queries)[:16],
        product_ids=(),
        skus=seed.skus if seed else (),
        upcs=seed.upcs if seed else (),
        verification_required=verification_for_category(plan.category_key),
        route_hint=route_hint_for_category(plan.category_key),
        seed_label=seed.label if seed else None,
        min_normal_value=seed.min_normal_value if seed else None,
        near_zero_trigger_price=seed.near_zero_trigger_price if seed else None,
    )


def cooldown_for_priority(priority: int) -> int:
    if priority >= 195:
        return 180
    if priority >= 185:
        return 300
    if priority >= 170:
        return 600
    return 900


def verification_for_category(category_key: str) -> VerificationRequirement:
    if category_key in {"gpus", "brand_direct_electronics", "apple", "gold_jewelry"}:
        return VerificationRequirement.PRICE_LINK_CART_AND_HISTORY
    if category_key in {"sneakers", "business_bulk", "motor_oil", "tools"}:
        return VerificationRequirement.PRICE_LINK_AND_CART
    return VerificationRequirement.PRICE_AND_LINK


def route_hint_for_category(category_key: str) -> str | None:
    if category_key == "business_bulk":
        return "business_deals"
    if category_key in {"gpus", "brand_direct_electronics", "apple", "sneakers", "gold_jewelry", "motor_oil", "tools"}:
        return "price_glitches"
    return None


def mode_for_plan(plan: SnipePlan) -> MonitorMode:
    """Default all generated monitors to staff review first.

    Public alerts should only be enabled after the provider is proven live,
    rate-safe, and accurate. This prevents new monitors from immediately
    blasting public channels.
    """
    return MonitorMode.STAFF_REVIEW
