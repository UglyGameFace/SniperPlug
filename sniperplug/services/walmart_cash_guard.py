from __future__ import annotations

from typing import Any

from sniperplug.services.walmart_cash import strict_walmart_promotion_proof as _strict_walmart_promotion_proof


def strict_walmart_promotion_proof(item: dict[str, Any], *, current_price: float | None = None, coupon_amount: float | None = None) -> dict[str, str]:
    """Compatibility wrapper for the strict native Walmart Cash proof function."""

    if current_price is None:
        current_price = _item_current_price(item)
    return _strict_walmart_promotion_proof(item, current_price=current_price, coupon_amount=coupon_amount)


def install_strict_walmart_cash_guard() -> None:
    """No-op compatibility hook.

    The strict Walmart Cash rules now live natively in the provider/services; no
    runtime monkey patch is needed.
    """

    return None


def _item_current_price(item: dict[str, Any]) -> float | None:
    for key in ("salePrice", "currentPrice", "price"):
        value = item.get(key)
        try:
            return float(str(value).replace("$", "").replace(",", ""))
        except (TypeError, ValueError):
            continue
    price_info = item.get("priceInfo") or item.get("price_info")
    if isinstance(price_info, dict):
        for key in ("currentPrice", "price", "salePrice"):
            try:
                return float(str(price_info.get(key)).replace("$", "").replace(",", ""))
            except (TypeError, ValueError):
                continue
    return None
