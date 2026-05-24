from __future__ import annotations

from dataclasses import dataclass

from sniperplug.models.deal import NormalizedDeal


@dataclass(frozen=True)
class VerifiedDealValue:
    discount_percent: float | None
    savings_amount: float | None
    price_proof_level: str
    effective_savings: float
    effective_value_notes: tuple[str, ...]


def verified_deal_value(deal: NormalizedDeal) -> VerifiedDealValue:
    """Calculate discount/value without pretending MSRP/list price is proof.

    A true discount requires a trusted reference price. Walmart Cash and coupons
    are additive value signals, but Walmart Cash is not treated as the checkout
    price because it is usually a reward/credit after purchase.
    """
    current = deal.current_price
    typical = deal.typical_price
    attrs = deal.variant_attributes or {}
    trusted_reference = attrs.get("referencePriceTrusted") == "yes" or bool(
        typical and current is not None and typical > current
    )

    discount: float | None = None
    savings: float | None = None
    proof_level = "no_reference_price"
    if trusted_reference and current is not None and typical and typical > current:
        savings = round(typical - current, 2)
        discount = round((savings / typical) * 100, 2)
        proof_level = "trusted_reference_price"

    notes: list[str] = []
    effective_savings = savings or 0.0
    if deal.coupon_savings and deal.coupon_savings > 0:
        notes.append(f"Walmart coupon: ${deal.coupon_savings:,.2f}")
        effective_savings += deal.coupon_savings
    walmart_cash = _float_or_none(attrs.get("walmartCashSavings"))
    if walmart_cash and walmart_cash > 0:
        notes.append(f"Walmart Cash reward/value: ${walmart_cash:,.2f}")
        effective_savings += walmart_cash
    if attrs.get("referencePriceTrusted") == "no":
        notes.append("Ignored low-confidence MSRP/list/marketplace reference price")
    return VerifiedDealValue(discount, savings, proof_level, round(effective_savings, 2), tuple(notes))


def _float_or_none(value) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, str):
        value = value.replace("$", "").replace(",", "").strip()
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
