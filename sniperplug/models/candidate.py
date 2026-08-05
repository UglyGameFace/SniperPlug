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

    # Selected-offer payable-price truth. `current_price` remains the number used
    # by ranking and discount math; for Walmart it is normalized to delivered
    # price when mandatory shipping is known.
    item_price: float | None = None
    shipping_cost: float | None = None
    delivered_price: float | None = None
    shipping_status: str | None = None
    shipping_source: str | None = None

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
        self._apply_walmart_selected_offer_truth()
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

    def _apply_walmart_selected_offer_truth(self) -> None:
        if str(self.retailer or "").strip().lower() != "walmart":
            if self.item_price is None:
                self.item_price = _float_or_none(self.current_price)
            if self.delivered_price is None:
                self.delivered_price = _float_or_none(self.current_price)
            return

        attrs = dict(self.variant_attributes or {})
        selected_item = _float_or_none(attrs.get("selectedOfferItemPrice"))
        selected_shipping = _float_or_none(attrs.get("selectedOfferShippingCost"))
        selected_delivered = _float_or_none(attrs.get("selectedOfferDeliveredPrice"))
        shipping_status = str(attrs.get("selectedOfferShippingStatus") or self.shipping_status or "").strip().lower()
        marketplace = str(
            attrs.get("selectedOfferMarketplace")
            or attrs.get("isMarketPlaceItem")
            or attrs.get("isMarketplaceItem")
            or attrs.get("marketplace")
            or ""
        ).strip().lower()

        selected_seller = str(attrs.get("selectedOfferSeller") or "").strip()
        selected_seller_id = str(attrs.get("selectedOfferSellerId") or "").strip()
        selected_offer_id = str(attrs.get("selectedOfferId") or "").strip()
        selected_fulfillment = str(attrs.get("selectedOfferFulfillment") or "").strip()
        selected_condition = str(attrs.get("selectedOfferCondition") or "").strip()

        if selected_seller:
            self.seller_name = selected_seller
            attrs["seller"] = selected_seller
        if selected_seller_id:
            attrs["sellerId"] = selected_seller_id
        if selected_offer_id:
            self.selected_offer_id = selected_offer_id
        if selected_fulfillment:
            self.fulfillment_type = selected_fulfillment
            attrs["fulfillment"] = selected_fulfillment
        if selected_condition:
            self.condition = selected_condition
            self.api_condition = self.api_condition or selected_condition
            attrs["condition"] = selected_condition

        current_source = str(attrs.get("currentPriceSource") or self.api_price_path or "").strip()
        source_is_alternate_min = current_source in {"minPrice", "min_price"}

        if selected_item is not None and selected_item > 0:
            self.item_price = round(selected_item, 2)
            if selected_delivered is not None and selected_delivered > 0 and shipping_status in {"free", "paid"}:
                self.shipping_cost = round(max(selected_shipping or 0.0, 0.0), 2)
                self.delivered_price = round(selected_delivered, 2)
                self.shipping_status = shipping_status
                self.shipping_source = str(attrs.get("selectedOfferShippingSource") or "").strip() or None
                self.current_price = self.delivered_price
                self.api_current_price = self.delivered_price
                self.api_price_path = str(attrs.get("selectedOfferDeliveredPriceSource") or "").strip() or self.api_price_path
                attrs["currentPriceSource"] = self.api_price_path or "selectedOfferDeliveredPrice"
                attrs["selectedOfferPublicPriceStatus"] = "verified_delivered"
            else:
                self.shipping_status = shipping_status or "unknown"
                self.shipping_source = str(attrs.get("selectedOfferShippingSource") or "").strip() or None
                self.delivered_price = None
                if _is_third_party_marketplace(
                    marketplace=marketplace,
                    seller_name=self.seller_name,
                    seller_id=selected_seller_id,
                ):
                    self.current_price = None
                    self.api_current_price = None
                    self.api_discount_percent = None
                    attrs["selectedOfferPublicPriceStatus"] = "blocked_shipping_unknown"
                    _append_signal(
                        self.signals,
                        "Walmart selected marketplace offer blocked: shipping cost was not returned",
                    )
                else:
                    self.current_price = self.item_price
                    self.api_current_price = self.item_price
                    self.delivered_price = self.item_price
                    self.shipping_status = "checkout_dependent"
                    attrs["selectedOfferPublicPriceStatus"] = "item_price_shipping_checkout_dependent"
                    _append_signal(
                        self.signals,
                        "Walmart shipping not separately returned; item price may depend on order, location, or checkout",
                    )
        elif source_is_alternate_min:
            # A page-level minimum can belong to another seller. It is useful as
            # context only and is never a payable selected-offer price.
            self.current_price = None
            self.api_current_price = None
            self.api_discount_percent = None
            self.delivered_price = None
            attrs["selectedOfferPublicPriceStatus"] = "blocked_alternate_min_price"
            _append_signal(
                self.signals,
                "Walmart alternate seller minimum price blocked from selected-offer deal math",
            )
        else:
            fallback = _float_or_none(self.current_price)
            self.item_price = self.item_price or fallback
            self.delivered_price = self.delivered_price or fallback

        reference = _float_or_none(self.api_reference_price) or _float_or_none(self.typical_price)
        payable = _float_or_none(self.api_current_price) or _float_or_none(self.current_price)
        self.api_discount_percent = _percent_off(payable, reference)

        if self.item_price is not None:
            attrs["itemPrice"] = f"{self.item_price:.2f}"
        if self.shipping_cost is not None:
            attrs["shippingCost"] = f"{self.shipping_cost:.2f}"
        if self.delivered_price is not None:
            attrs["deliveredPrice"] = f"{self.delivered_price:.2f}"
        if self.shipping_status:
            attrs["shippingStatus"] = self.shipping_status
        if self.shipping_source:
            attrs["shippingSource"] = self.shipping_source
        self.variant_attributes = attrs

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
        if self.item_price is not None:
            availability_bits.append(f"Item price: {money(self.item_price)}")
        if self.shipping_status == "free":
            availability_bits.append("Shipping: free (API proof)")
        elif self.shipping_cost is not None:
            availability_bits.append(f"Shipping: {money(self.shipping_cost)}")
        elif self.shipping_status:
            availability_bits.append(f"Shipping: {self.shipping_status.replace('_', ' ')}")
        if self.delivered_price is not None:
            availability_bits.append(f"Delivered total: {money(self.delivered_price)}")
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
            item_price=self.item_price,
            shipping_cost=self.shipping_cost,
            delivered_price=self.delivered_price,
            shipping_status=self.shipping_status,
            shipping_source=self.shipping_source,
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
            "itemPrice": _money_attr(self.item_price),
            "shippingCost": _money_attr(self.shipping_cost),
            "deliveredPrice": _money_attr(self.delivered_price),
            "shippingStatus": self.shipping_status,
            "shippingSource": self.shipping_source,
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
        if self.item_price is not None:
            deal.verification_notes.append(f"Selected offer item price: {money(self.item_price)}")
        if self.shipping_status == "free":
            deal.verification_notes.append("Selected offer shipping: free (API proof)")
        elif self.shipping_cost is not None:
            deal.verification_notes.append(f"Selected offer shipping: {money(self.shipping_cost)}")
        elif self.shipping_status:
            deal.verification_notes.append(f"Selected offer shipping status: {self.shipping_status}")
        if self.delivered_price is not None:
            deal.verification_notes.append(f"Selected offer delivered total: {money(self.delivered_price)}")
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


def _is_third_party_marketplace(*, marketplace: str, seller_name: str | None, seller_id: str | None) -> bool:
    if marketplace in {"yes", "true", "1"}:
        return True
    normalized_name = " ".join(str(seller_name or "").lower().split())
    normalized_id = str(seller_id or "").strip().upper()
    if normalized_name in {"walmart", "walmart.com", "walmart stores inc", "walmart stores, inc."}:
        return False
    if normalized_id in {"0", "F55CDC31AB754BB68FE0B39041159D63", "WALMART"}:
        return False
    return bool(normalized_name or normalized_id)


def _append_signal(signals: list[str], value: str) -> None:
    if value not in signals:
        signals.append(value)


def _percent_off(current: float | None, reference: float | None) -> float | None:
    if current is None or reference is None or reference <= 0 or reference <= current:
        return None
    return round((reference - current) / reference * 100.0, 2)


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
