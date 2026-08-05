from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from uuid import uuid4


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class NormalizedDeal:
    title: str
    retailer: str
    product_url: str

    current_price: float | None = None
    typical_price: float | None = None
    discount_percent: float | None = None
    savings_amount: float | None = None

    # Selected-offer payable-price breakdown. For Walmart marketplace offers,
    # current_price is the delivered total whenever shipping is verified.
    item_price: float | None = None
    shipping_cost: float | None = None
    delivered_price: float | None = None
    shipping_status: str | None = None
    shipping_source: str | None = None

    pre_coupon_price: float | None = None
    coupon_savings: float | None = None
    coupon_percent: float | None = None
    coupon_terms: list[str] = field(default_factory=list)
    coupon_stack_detected: bool = False
    is_subscribe_save: bool = False

    source: str = "manual_test"
    marketplace: str = "US"
    image_url: str | None = None

    asin: str | None = None
    sku: str | None = None
    upc: str | None = None

    selected_offer_id: str | None = None
    variant_label: str | None = None
    variant_attributes: dict[str, str] = field(default_factory=dict)
    pack_size: str | None = None
    color: str | None = None
    platform: str | None = None
    model: str | None = None
    parent_title: str | None = None
    option_mismatch_warning: str | None = None

    seller_name: str | None = None
    fulfilled_by_amazon: bool | None = None
    fulfillment_type: str | None = None
    condition: str | None = None
    availability_message: str | None = None

    is_possible_price_error: bool = False
    is_ymmv: bool = False
    risk_level: str = "low"
    confidence_score: int = 50

    verification_status: str = "candidate"
    is_price_verified: bool = False
    is_link_verified: bool = False
    is_image_verified: bool = False
    requires_business_account: bool = False
    verification_notes: list[str] = field(default_factory=list)

    risk_flags: list[str] = field(default_factory=list)
    alert_tags: list[str] = field(default_factory=list)

    deal_id: str = field(default_factory=lambda: uuid4().hex)
    first_seen_at: str = field(default_factory=utc_now_iso)
    last_checked_at: str = field(default_factory=utc_now_iso)
    expires_at: str | None = None

    def recalculate_prices(self) -> None:
        if self.current_price is not None and self.typical_price:
            self.savings_amount = round(max(self.typical_price - self.current_price, 0), 2)
            self.discount_percent = round((self.savings_amount / self.typical_price) * 100, 2)

        if self.pre_coupon_price is not None and self.current_price is not None:
            self.coupon_savings = round(max(self.pre_coupon_price - self.current_price, 0), 2)
            if self.pre_coupon_price > 0 and self.coupon_savings:
                self.coupon_percent = round((self.coupon_savings / self.pre_coupon_price) * 100, 2)

        if self.coupon_terms:
            lowered_terms = {term.lower() for term in self.coupon_terms}
            self.coupon_stack_detected = len(self.coupon_terms) >= 2 or "subscribe and save" in lowered_terms

    def to_dict(self) -> dict:
        return asdict(self)
