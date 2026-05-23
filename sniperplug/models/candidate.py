from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import uuid4

from sniperplug.models.deal import NormalizedDeal


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class SourceCandidate:
    """
    Raw-ish product opportunity found directly from a retailer/source.

    This is the object provider integrations should produce before SniperPlug
    decides whether it is worth turning into a public deal alert.
    """
    source_key: str
    retailer: str
    title: str
    product_url: str

    current_price: float | None = None
    typical_price: float | None = None
    image_url: str | None = None

    product_id: str | None = None
    product_id_type: str | None = None
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

    stock_status: str | None = None
    can_add_to_cart: bool | None = None
    is_business_offer: bool = False
    is_member_only: bool = False
    is_checkout_price: bool = False

    signals: list[str] = field(default_factory=list)
    candidate_id: str = field(default_factory=lambda: uuid4().hex)
    first_seen_at: str = field(default_factory=utc_now_iso)
    last_checked_at: str = field(default_factory=utc_now_iso)

    def to_normalized_deal(self) -> NormalizedDeal:
        availability_bits: list[str] = []
        if self.stock_status:
            availability_bits.append(f"Stock: {self.stock_status}")
        if self.can_add_to_cart is True:
            availability_bits.append("Add-to-cart observed")
        elif self.can_add_to_cart is False:
            availability_bits.append("Add-to-cart not confirmed")
        if self.is_business_offer:
            availability_bits.append("May require business account")
        if self.is_member_only:
            availability_bits.append("May require membership")
        if self.is_checkout_price:
            availability_bits.append("Checkout price observed")

        deal = NormalizedDeal(
            title=self.title,
            retailer=self.retailer,
            product_url=self.product_url,
            image_url=self.image_url,
            current_price=self.current_price,
            typical_price=self.typical_price,
            source=self.source_key,
            sku=self.sku or (self.product_id if self.product_id_type == "sku" else None),
            upc=self.upc or (self.product_id if self.product_id_type == "upc" else None),
            asin=self.product_id if self.product_id_type == "asin" else None,
            availability_message="; ".join(availability_bits) if availability_bits else None,
        )
        deal.recalculate_prices()

        if self.is_business_offer:
            deal.alert_tags.append("Business Deal")
            deal.risk_flags.append("May require business account")

        if self.is_member_only:
            deal.is_ymmv = True
            deal.risk_flags.append("May require membership")

        if self.signals:
            deal.risk_flags.extend(self.signals[:5])

        return deal
