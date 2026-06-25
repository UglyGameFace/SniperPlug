from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import uuid4

from sniperplug.models.deal import NormalizedDeal
from sniperplug.services.safe_links import normalize_product_url


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

    # Public deal proof. These fields are intentionally separate from display
    # copy so public posting never guesses from embed wording such as MSRP.
    deal_lane: str | None = None
    api_current_price: float | None = None
    api_reference_price: float | None = None
    api_discount_percent: float | None = None
    api_condition: str | None = None
    api_condition_path: str | None = None
    api_reference_path: str | None = None
    api_price_path: str | None = None
    direct_product_url: str | None = None

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

    seller_name: str | None = None
    fulfillment_type: str | None = None
    condition: str | None = None

    stock_status: str | None = None
    can_add_to_cart: bool | None = None
    is_business_offer: bool = False
    is_member_only: bool = False
    is_checkout_price: bool = False

    signals: list[str] = field(default_factory=list)
    candidate_id: str = field(default_factory=lambda: uuid4().hex)
    first_seen_at: str = field(default_factory=utc_now_iso)
    last_checked_at: str = field(default_factory=utc_now_iso)

    def __post_init__(self) -> None:
        asin = self.product_id if self.product_id_type == "asin" else None
        normalized = normalize_product_url(
            retailer=self.retailer,
            url=self.product_url,
            product_id=self.product_id,
            sku=self.sku,
            asin=asin,
        )
        self.product_url = normalized.url
        if not self.direct_product_url:
            self.direct_product_url = normalized.url
        for note in normalized.notes:
            if note not in self.signals:
                self.signals.append(note)

    def to_normalized_deal(self) -> NormalizedDeal:
        availability_bits: list[str] = []
        if self.stock_status:
            availability_bits.append(f"Stock: {self.stock_status}")
        if self.can_add_to_cart is True:
            availability_bits.append("Add-to-cart observed")
        elif self.can_add_to_cart is False:
            availability_bits.append("Add-to-cart not confirmed")
        if self.seller_name:
            availability_bits.append(f"Seller: {self.seller_name}")
        if self.fulfillment_type:
            availability_bits.append(f"Fulfillment: {self.fulfillment_type}")
        if self.condition:
            availability_bits.append(f"Condition: {self.condition}")
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
            selected_offer_id=self.selected_offer_id,
            variant_label=self.variant_label,
            variant_attributes=dict(self.variant_attributes),
            pack_size=self.pack_size,
            color=self.color,
            platform=self.platform,
            model=self.model,
            parent_title=self.parent_title,
            option_mismatch_warning=self.option_mismatch_warning,
            seller_name=self.seller_name,
            fulfillment_type=self.fulfillment_type,
            condition=self.condition,
            availability_message="; ".join(availability_bits) if availability_bits else None,
        )

        structured_attrs = {
            "dealLane": self.deal_lane,
            "apiCurrentPrice": _money_attr(self.api_current_price),
            "apiReferencePrice": _money_attr(self.api_reference_price),
            "apiDiscountPercent": _percent_attr(self.api_discount_percent),
            "apiCondition": self.api_condition,
            "apiConditionPath": self.api_condition_path,
            "apiReferencePath": self.api_reference_path,
            "apiPricePath": self.api_price_path,
            "directProductUrl": self.direct_product_url,
        }
        for key, value in structured_attrs.items():
            if value is not None and value != "":
                deal.variant_attributes.setdefault(key, str(value))

        coupon_savings = _float_or_none(self.variant_attributes.get("couponSavings"))
        if coupon_savings and coupon_savings > 0 and self.current_price is not None:
            deal.pre_coupon_price = round(self.current_price + coupon_savings, 2)
            deal.coupon_savings = round(coupon_savings, 2)
            deal.coupon_terms.append(f"Walmart coupon: {money(coupon_savings)}")
            deal.alert_tags.append("Walmart Coupon")
            deal.verification_notes.append(f"Walmart coupon detected: {money(coupon_savings)}")

        walmart_cash = _float_or_none(self.variant_attributes.get("walmartCashSavings"))
        if walmart_cash and walmart_cash > 0:
            deal.coupon_terms.append(f"Walmart Cash reward: {money(walmart_cash)}")
            deal.alert_tags.append("Walmart Cash")
            deal.verification_notes.append(f"Walmart Cash detected: {money(walmart_cash)} reward/value")

        api_savings = _float_or_none(self.variant_attributes.get("apiSavingsAmount"))
        api_promo_cap = _float_or_none(self.variant_attributes.get("apiPromotionSavingsCap"))
        api_promo = str(self.variant_attributes.get("apiPromotionText") or "").strip()
        api_kind = str(self.variant_attributes.get("apiValueKind") or "").strip()

        if api_savings and api_savings > 0:
            deal.alert_tags.append("Walmart API Savings")
            deal.verification_notes.append(f"Walmart API savings from payload: {money(api_savings)}")
        if api_promo_cap and api_promo_cap > 0:
            deal.alert_tags.append("Walmart API Promo")
            deal.verification_notes.append(f"Walmart API promo savings cap: {money(api_promo_cap)}")
        if api_promo:
            deal.alert_tags.append("Walmart API Promo")
            deal.verification_notes.append(f"Walmart API promo text: {api_promo[:180]}")
        if api_kind:
            deal.verification_notes.append(f"Walmart API value type: {api_kind}")

        deal.recalculate_prices()

        if self.variant_label:
            deal.verification_notes.append(f"Selected option: {self.variant_label}")
        if self.variant_attributes:
            attrs = ", ".join(f"{key}: {value}" for key, value in self.variant_attributes.items())
            deal.verification_notes.append(f"Variant attributes: {attrs}")
        if self.selected_offer_id:
            deal.verification_notes.append(f"Selected offer ID: {self.selected_offer_id}")
        if self.seller_name:
            deal.verification_notes.append(f"Selected offer seller: {self.seller_name}")
        if self.fulfillment_type:
            deal.verification_notes.append(f"Selected offer fulfillment: {self.fulfillment_type}")
        if self.condition:
            deal.verification_notes.append(f"Selected offer condition: {self.condition}")
        if self.parent_title and self.parent_title != self.title:
            deal.verification_notes.append(f"Parent listing title: {self.parent_title}")
        if self.option_mismatch_warning:
            deal.risk_flags.append(self.option_mismatch_warning)
            deal.verification_notes.append(self.option_mismatch_warning)
            deal.risk_level = "high"

        if self.is_business_offer:
            deal.alert_tags.append("Business Deal")
            deal.risk_flags.append("May require business account")

        if self.is_member_only:
            deal.is_ymmv = True
            deal.risk_flags.append("May require membership")

        if self.signals:
            deal.risk_flags.extend(self.signals[:5])

        return deal


def _float_or_none(value) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, str):
        value = value.replace("$", "").replace(",", "").strip().rstrip("%")
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _money_attr(value: float | None) -> str | None:
    parsed = _float_or_none(value)
    if parsed is None:
        return None
    return f"{parsed:.2f}"


def _percent_attr(value: float | None) -> str | None:
    parsed = _float_or_none(value)
    if parsed is None:
        return None
    return f"{parsed:.2f}"


def money(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"${value:,.2f}"
