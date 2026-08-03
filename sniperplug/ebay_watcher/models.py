from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class EbayWatchRule:
    rule_id: str
    label: str
    query: str = ""
    category_id: str = ""
    gtin: str = ""
    epid: str = ""
    seller: str = ""
    sought_after: bool = False
    enabled: bool = True
    priority: int = 50
    min_discount_percent: int = 69
    min_reference_price: float = 200.0
    allowed_conditions: tuple[str, ...] = ()
    min_seller_feedback_percentage: float = 97.0
    min_seller_feedback_score: int = 10
    search_limit: int = 100
    scan_interval_seconds: int = 300
    next_scan_at: str = ""
    last_scan_at: str = ""
    consecutive_failures: int = 0
    last_error: str = ""

    @property
    def has_search_identity(self) -> bool:
        return bool(self.query or self.category_id or self.gtin or self.epid or self.seller)


@dataclass(frozen=True)
class EbayListing:
    item_id: str
    legacy_item_id: str
    title: str
    product_url: str
    image_url: str
    item_price: float
    shipping_price: float | None
    delivered_price: float | None
    currency: str
    shipping_known: bool
    condition_id: str
    condition_name: str
    condition_bucket: str
    seller_id: str
    seller_feedback_percentage: float | None
    seller_feedback_score: int | None
    buying_options: tuple[str, ...]
    item_creation_date: str
    item_end_date: str
    estimated_availability_status: str
    gtin: str
    epid: str
    brand: str
    model: str
    mpn: str
    aspects: dict[str, str] = field(default_factory=dict)
    short_description: str = ""
    marketing_original_price: float | None = None
    fingerprint: str = ""
    exact_identity: bool = False
    suspicious_reason: str = ""
    watch_count: int | None = None
    bid_count: int | None = None

    @property
    def fixed_price(self) -> bool:
        return "FIXED_PRICE" in self.buying_options

    @property
    def active(self) -> bool:
        value = self.estimated_availability_status.strip().upper()
        return value not in {"OUT_OF_STOCK", "UNAVAILABLE", "ENDED"}

    @property
    def comparable_key(self) -> tuple[str, str]:
        return self.fingerprint, self.condition_bucket


@dataclass(frozen=True)
class ComparableReference:
    price: float
    sample_size: int
    source: str = "sniperplug.ebay.exact_comparable_median"


@dataclass(frozen=True)
class ListingHistory:
    item_id: str
    first_seen_at: str
    previous_delivered_price: float | None
    prior_baseline_price: float | None
    prior_baseline_observations: int
    prior_baseline_first_seen_at: str
    last_alert_price: float | None
    last_event_key: str
    is_new: bool


@dataclass(frozen=True)
class EbayDealDecision:
    should_publish: bool
    event_key: str = ""
    event_type: str = ""
    reference_price: float | None = None
    reference_source: str = ""
    discount_percent: float = 0.0
    comparable_count: int = 0
    reason: str = ""


@dataclass(frozen=True)
class TrackedListingTarget:
    item_id: str
    rule_id: str
    next_check_at: str = ""
    consecutive_failures: int = 0


@dataclass(frozen=True)
class EbayCycleResult:
    rules_claimed: int = 0
    rules_succeeded: int = 0
    rules_failed: int = 0
    searches: int = 0
    listings_seen: int = 0
    tracked_checked: int = 0
    observations: int = 0
    confirmations: int = 0
    events: int = 0
    blocked: int = 0

    def add(self, **changes: int) -> "EbayCycleResult":
        values: dict[str, Any] = dict(self.__dict__)
        for key, value in changes.items():
            if key in values:
                values[key] = int(values[key]) + int(value)
        return EbayCycleResult(**values)
