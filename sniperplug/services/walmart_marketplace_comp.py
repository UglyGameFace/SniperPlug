from __future__ import annotations

import urllib.parse
from dataclasses import dataclass
from typing import Any


DEFAULT_MARKETPLACE_FEE_RATE = 0.13
DEFAULT_TAX_RATE = 0.07
DEFAULT_SHIPPING_ESTIMATE = 8.00
MIN_STRONG_FLIP_ROI = 0.35
MIN_STRONG_FLIP_NET = 15.00
MARKETPLACE_COMP_SOURCES = {"bestmarketplaceprice.price", "bestmarketplaceprice", "best_marketplace_price.price", "best_marketplace_price"}


@dataclass(frozen=True)
class FlipEstimate:
    walmart_price: float
    comp_price: float
    spread: float
    fee_estimate: float
    tax_estimate: float
    shipping_estimate: float
    net_estimate: float
    roi_percent: float
    score: int
    verdict: str


def is_marketplace_comp_source(source: str | None) -> bool:
    normalized = str(source or "").strip().lower().replace("_", "").replace(" ", "")
    return normalized in MARKETPLACE_COMP_SOURCES or "bestmarketplaceprice" in normalized


def marketplace_comp_from_item(item: dict[str, Any]) -> dict[str, str]:
    """Extract alternate-seller context and atomic selected-offer price truth.

    `bestMarketplacePrice` and top-level `minPrice` are alternate-offer context,
    never the selected buy-box price. Selected seller, offer ID, item price,
    shipping, and delivered total are normalized from one selected-offer node so
    downstream code cannot combine one seller's price with another seller's
    identity.
    """
    attrs = selected_offer_delivery_attributes(item)

    best_marketplace = item.get("bestMarketplacePrice") or item.get("best_marketplace_price")
    if isinstance(best_marketplace, dict):
        price = money_from_value(
            best_marketplace.get("price")
            or best_marketplace.get("amount")
            or best_marketplace.get("value")
        )
        if price is not None and price > 0:
            attrs.update(
                {
                    "marketplaceCompPrice": f"{price:.2f}",
                    "marketplaceCompSource": "bestMarketplacePrice.price",
                    "marketplaceCompNote": "Walmart API marketplace comp; not selected offer or was/regular price; use for flip research only",
                }
            )
            seller = clean_string(
                best_marketplace.get("sellerName")
                or best_marketplace.get("seller")
                or best_marketplace.get("sellerDisplayName")
                or best_marketplace.get("sellerId")
            )
            if seller:
                attrs["marketplaceCompSeller"] = seller

    alternate_min = first_money(
        ("minPrice", item.get("minPrice")),
        ("min_price", item.get("min_price")),
    )
    if alternate_min is not None and alternate_min[1] > 0:
        attrs["alternateSellerMinPrice"] = f"{alternate_min[1]:.2f}"
        attrs["alternateSellerMinPriceSource"] = alternate_min[0]
        attrs["alternateSellerMinPriceNote"] = "alternate seller context only; never selected-offer price proof"

    return attrs


def selected_offer_delivery_attributes(item: dict[str, Any]) -> dict[str, str]:
    node, node_path = selected_offer_node(item)
    attrs: dict[str, str] = {"selectedOfferNodeSource": node_path}

    seller_name = clean_string(
        node.get("sellerName")
        or node.get("sellerDisplayName")
        or nested(node, "seller", "name")
        or nested(node, "sellerInfo", "sellerName")
        or nested(node, "sellerInfo", "name")
        or item.get("sellerName")
        or item.get("sellerDisplayName")
        or nested(item, "seller", "name")
        or nested(item, "sellerInfo", "sellerName")
    )
    seller_id = clean_string(
        node.get("sellerId")
        or node.get("sellerID")
        or nested(node, "seller", "id")
        or nested(node, "sellerInfo", "sellerId")
        or item.get("sellerId")
        or item.get("sellerID")
        or nested(item, "seller", "id")
        or nested(item, "sellerInfo", "sellerId")
    )
    offer_id = clean_string(
        node.get("offerId")
        or node.get("offerID")
        or node.get("id")
        or item.get("selectedOfferId")
        or item.get("buyBoxOfferId")
        or item.get("offerId")
        or item.get("offerID")
    )
    fulfillment = clean_string(
        node.get("fulfillmentType")
        or node.get("fulfillment")
        or node.get("fulfillmentBadge")
        or nested(node, "fulfillmentSummary", "fulfillmentType")
        or nested(node, "fulfillmentSummary", "fulfillment")
        or item.get("fulfillmentType")
        or item.get("fulfillment")
        or item.get("fulfillmentBadge")
    )
    condition = clean_string(
        node.get("conditionType")
        or nested(node, "condition", "type")
        or nested(node, "condition", "name")
        or node.get("condition")
        or item.get("conditionType")
        or nested(item, "condition", "type")
        or nested(item, "condition", "name")
        or item.get("condition")
    )

    marketplace = first_bool(
        node.get("isMarketPlaceItem"),
        node.get("isMarketplaceItem"),
        node.get("marketplace"),
        item.get("isMarketPlaceItem"),
        item.get("isMarketplaceItem"),
        item.get("marketplace"),
    )
    if marketplace is None and seller_name and not seller_name_is_walmart(seller_name):
        marketplace = True

    item_price = first_money(
        (f"{node_path}.salePrice", node.get("salePrice")),
        (f"{node_path}.currentPrice", node.get("currentPrice")),
        (f"{node_path}.priceInfo.currentPrice", nested(node, "priceInfo", "currentPrice")),
        (f"{node_path}.price", node.get("price")),
        ("salePrice", item.get("salePrice")),
        ("currentPrice", item.get("currentPrice")),
        ("priceInfo.currentPrice", nested(item, "priceInfo", "currentPrice")),
        ("price", item.get("price")),
    )
    shipping = first_money_allow_zero(
        (f"{node_path}.shippingPrice", node.get("shippingPrice")),
        (f"{node_path}.shippingCost", node.get("shippingCost")),
        (f"{node_path}.shippingFee", node.get("shippingFee")),
        (f"{node_path}.priceInfo.shippingPrice", nested(node, "priceInfo", "shippingPrice")),
        (f"{node_path}.shippingOption.price", nested(node, "shippingOption", "price")),
        (f"{node_path}.shippingOption.cost", nested(node, "shippingOption", "cost")),
        ("shippingPrice", item.get("shippingPrice")),
        ("shippingCost", item.get("shippingCost")),
        ("shippingFee", item.get("shippingFee")),
        ("priceInfo.shippingPrice", nested(item, "priceInfo", "shippingPrice")),
    )
    explicit_free = first_bool(
        node.get("freeShipping"),
        node.get("isFreeShipping"),
        node.get("shippingIsFree"),
        nested(node, "shippingOption", "freeShipping"),
        item.get("freeShipping"),
        item.get("isFreeShipping"),
        item.get("shippingIsFree"),
    )

    if seller_name:
        attrs["selectedOfferSeller"] = seller_name
    if seller_id:
        attrs["selectedOfferSellerId"] = seller_id
    if offer_id:
        attrs["selectedOfferId"] = offer_id
    if fulfillment:
        attrs["selectedOfferFulfillment"] = fulfillment
    if condition:
        attrs["selectedOfferCondition"] = condition
    if marketplace is not None:
        attrs["selectedOfferMarketplace"] = "yes" if marketplace else "no"

    if item_price is not None:
        price_source, price = item_price
        attrs["selectedOfferItemPrice"] = f"{price:.2f}"
        attrs["selectedOfferItemPriceSource"] = price_source

        if shipping is not None:
            shipping_source, shipping_cost = shipping
            attrs["selectedOfferShippingCost"] = f"{shipping_cost:.2f}"
            attrs["selectedOfferShippingSource"] = shipping_source
            attrs["selectedOfferShippingStatus"] = "free" if shipping_cost == 0 else "paid"
            attrs["selectedOfferDeliveredPrice"] = f"{price + shipping_cost:.2f}"
            attrs["selectedOfferDeliveredPriceSource"] = f"{price_source}+{shipping_source}"
        elif explicit_free is True:
            attrs["selectedOfferShippingCost"] = "0.00"
            attrs["selectedOfferShippingSource"] = "explicit freeShipping flag"
            attrs["selectedOfferShippingStatus"] = "free"
            attrs["selectedOfferDeliveredPrice"] = f"{price:.2f}"
            attrs["selectedOfferDeliveredPriceSource"] = f"{price_source}+freeShipping"
        else:
            attrs["selectedOfferShippingStatus"] = "unknown"
            attrs["selectedOfferDeliveredPriceSource"] = "blocked: shipping not returned"

    return attrs


def selected_offer_node(item: dict[str, Any]) -> tuple[dict[str, Any], str]:
    for key in ("selectedOffer", "buyBoxOffer", "primaryOffer", "offer"):
        value = item.get(key)
        if isinstance(value, dict):
            return value, key
    return item, "item"


def marketplace_comp_from_attrs(attrs: dict[str, Any]) -> tuple[float | None, str | None, str | None, str | None]:
    comp_price = float_or_none(attrs.get("marketplaceCompPrice"))
    comp_source = clean_string(attrs.get("marketplaceCompSource"))
    comp_seller = clean_string(attrs.get("marketplaceCompSeller"))
    comp_note = clean_string(attrs.get("marketplaceCompNote"))
    if comp_price is not None:
        return comp_price, comp_source or "Walmart API marketplace comp", comp_seller, comp_note

    context_source = clean_string(attrs.get("referenceContextSource"))
    if not is_marketplace_comp_source(context_source):
        return None, None, None, None
    context_price = float_or_none(attrs.get("referenceContextPrice"))
    if context_price is None or context_price <= 0:
        return None, None, None, None
    return context_price, context_source or "bestMarketplacePrice.price", None, "flip research only; not Walmart discount proof"


def marketplace_api_lines(*, current_price: float | None, attrs: dict[str, Any]) -> list[str]:
    comp_price, comp_source, seller, note = marketplace_comp_from_attrs(attrs)
    if comp_price is None:
        return []
    source = comp_source or "Walmart API marketplace comp"
    note = note or "flip research only; not Walmart discount proof"
    line = f"• Marketplace comp: **${comp_price:,.2f}** `{source}`"
    if seller:
        line += f" seller: **{seller}**"
    line += f" — {note}"
    lines = [line]

    estimate = flip_estimate(walmart_price=current_price, comp_price=comp_price)
    if estimate:
        lines.append(
            "• Flip estimate: "
            f"score **{estimate.score}/100** • spread **${estimate.spread:,.2f}** • "
            f"est. net **${estimate.net_estimate:,.2f}** • ROI **{estimate.roi_percent:.1f}%** • {estimate.verdict}"
        )
        lines.append(
            "• Estimate assumptions: "
            f"{DEFAULT_MARKETPLACE_FEE_RATE:.0%} selling fee, {DEFAULT_TAX_RATE:.0%} tax, "
            f"${estimate.shipping_estimate:,.2f} shipping placeholder"
        )
    return lines


def flip_estimate(*, walmart_price: float | None, comp_price: float | None) -> FlipEstimate | None:
    if walmart_price is None or walmart_price <= 0 or comp_price is None or comp_price <= 0:
        return None
    spread = comp_price - walmart_price
    fee = comp_price * DEFAULT_MARKETPLACE_FEE_RATE
    tax = walmart_price * DEFAULT_TAX_RATE
    shipping = DEFAULT_SHIPPING_ESTIMATE if comp_price >= 25 else 5.00
    net = comp_price - walmart_price - fee - tax - shipping
    roi = (net / walmart_price) * 100 if walmart_price else 0.0
    score = max(0, min(100, round((roi * 1.2) + (net * 1.1))))
    if net >= MIN_STRONG_FLIP_NET and roi >= MIN_STRONG_FLIP_ROI * 100:
        verdict = "worth deeper comp check"
    elif net > 0:
        verdict = "thin margin / verify sold comps first"
    else:
        verdict = "not enough estimated margin"
    return FlipEstimate(
        walmart_price=round(walmart_price, 2),
        comp_price=round(comp_price, 2),
        spread=round(spread, 2),
        fee_estimate=round(fee, 2),
        tax_estimate=round(tax, 2),
        shipping_estimate=round(shipping, 2),
        net_estimate=round(net, 2),
        roi_percent=round(roi, 1),
        score=score,
        verdict=verdict,
    )


def comp_search_links(*, title: str, sku: str | None = None, upc: str | None = None) -> tuple[str, ...]:
    identity = upc or sku or title
    query = urllib.parse.quote_plus(identity)
    title_query = urllib.parse.quote_plus(title)
    return (
        f"[eBay sold](https://www.ebay.com/sch/i.html?_nkw={query}&LH_Sold=1&LH_Complete=1)",
        f"[eBay active](https://www.ebay.com/sch/i.html?_nkw={query})",
        f"[Google Shopping](https://www.google.com/search?tbm=shop&q={title_query})",
    )


def first_money(*candidates: tuple[str, Any]) -> tuple[str, float] | None:
    for source, value in candidates:
        parsed = money_from_value(value)
        if parsed is not None and parsed > 0:
            return source, parsed
    return None


def first_money_allow_zero(*candidates: tuple[str, Any]) -> tuple[str, float] | None:
    for source, value in candidates:
        parsed = money_from_value(value)
        if parsed is not None and parsed >= 0:
            return source, parsed
    return None


def money_from_value(value: Any) -> float | None:
    if isinstance(value, dict):
        for key in ("price", "amount", "value", "displayValue", "displayPrice", "currencyAmount", "currencyValue"):
            parsed = float_or_none(value.get(key))
            if parsed is not None:
                return parsed
        return None
    return float_or_none(value)


def nested(value: Any, *path: str) -> Any:
    current = value
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def first_bool(*values: Any) -> bool | None:
    for value in values:
        if isinstance(value, bool):
            return value
        text = str(value or "").strip().lower()
        if text in {"true", "yes", "1", "y"}:
            return True
        if text in {"false", "no", "0", "n"}:
            return False
    return None


def seller_name_is_walmart(value: str | None) -> bool:
    normalized = " ".join(str(value or "").lower().split())
    return normalized in {"walmart", "walmart.com", "walmart stores inc", "walmart stores, inc."}


def float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, str):
        value = value.replace("$", "").replace(",", "").strip()
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def clean_string(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    return str(value).strip() or None
