from __future__ import annotations

from sniperplug.models.deal import NormalizedDeal


AMAZON_YMMV_WARNING = (
    "Amazon offers may be account-specific, ZIP-based, Prime-only, seller-specific, "
    "regional, unavailable for some users, or gone by checkout."
)


def apply_risk_flags(deal: NormalizedDeal) -> NormalizedDeal:
    """
    Adds SniperPlug warning tags using only fields present on the deal object.

    Important:
    This does not claim an API confirmed a glitch.
    It labels large discounts as possible price glitches/errors based on our own rules.
    """
    deal.recalculate_prices()

    flags: list[str] = []
    tags: list[str] = []

    discount = deal.discount_percent or 0

    if discount >= 70:
        deal.is_possible_price_error = True
        tags.append("⚡ Possible Price Glitch")
        flags.append("Huge discount compared to the typical price. It may be an error or may disappear fast.")
    elif discount >= 40:
        tags.append("🔥 Hot Deal")
        flags.append("Large discount compared to the typical price.")

    if deal.retailer.lower() == "amazon":
        deal.is_ymmv = True
        tags.append("🧊 YMMV")
        flags.append(AMAZON_YMMV_WARNING)

        if deal.fulfilled_by_amazon is False:
            flags.append("Merchant fulfilled / not clearly fulfilled by Amazon. Check seller before buying.")

    if deal.seller_name and deal.seller_name.lower() not in {"amazon", "amazon.com"}:
        flags.append(f"Third-party seller: {deal.seller_name}. Verify seller rating, shipping, and return policy.")

    if deal.condition:
        condition = deal.condition.lower()
        if any(word in condition for word in ["renewed", "used", "open box", "refurbished"]):
            flags.append(f"Condition is listed as {deal.condition}. Check condition carefully before checkout.")

    if deal.availability_message:
        flags.append(f"Availability note: {deal.availability_message}")

    # Risk level is intentionally conservative.
    if deal.is_possible_price_error or len(flags) >= 3:
        deal.risk_level = "medium"
    if deal.fulfilled_by_amazon is False and discount >= 70:
        deal.risk_level = "high"

    deal.alert_tags = unique_keep_order(tags)
    deal.risk_flags = unique_keep_order(flags)
    deal.confidence_score = calculate_confidence_score(deal)

    return deal


def calculate_confidence_score(deal: NormalizedDeal) -> int:
    score = 50

    if deal.current_price is not None:
        score += 10
    if deal.typical_price is not None:
        score += 10
    if deal.product_url:
        score += 10
    if deal.image_url:
        score += 5
    if deal.seller_name:
        score += 5

    if deal.fulfilled_by_amazon is False:
        score -= 15
    if deal.is_possible_price_error:
        score -= 10
    if deal.condition and deal.condition.lower() in {"used", "renewed", "open box", "refurbished"}:
        score -= 5

    return max(0, min(100, score))


def unique_keep_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for item in items:
        if item not in seen:
            output.append(item)
            seen.add(item)
    return output
