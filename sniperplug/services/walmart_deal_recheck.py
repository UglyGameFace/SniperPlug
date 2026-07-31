from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sniperplug.providers.base import ProviderScanRequest
from sniperplug.services.walmart_recheck_guard import (
    WALMART_RECHECK_PROVIDER_TIMEOUT_SECONDS,
    guarded_walmart_recheck,
)


_WALMART_ITEM_PATTERNS = (
    re.compile(r"/ip/(?:[^/?#]+/)?(?P<item_id>\d{6,})(?:[/?#]|$)", re.IGNORECASE),
    re.compile(r"[?&](?:itemId|item_id)=(?P<item_id>\d{6,})(?:&|$)", re.IGNORECASE),
)
_PROMOTION_LANE_TOKENS = ("walmart_cash", "onepay", "cart_promo", "checkout")


@dataclass(frozen=True)
class WalmartRecheckResult:
    status: str
    message: str
    item_id: str | None = None
    old_price: float | None = None
    current_price: float | None = None
    old_discount: float | None = None
    current_discount: float | None = None
    reference_price: float | None = None
    candidate: Any | None = None
    cache_state: str | None = None
    reused: bool = False

    @property
    def cache_status(self) -> str:
        if self.cache_state in {"active", "stale"}:
            return self.cache_state
        return "stale" if self.status in {"unavailable", "identity_mismatch", "discount_gone", "discount_unproven"} else "active"


def extract_walmart_item_id(url: str | None, active_key: str | None = None) -> str | None:
    text = str(url or "").strip()
    for pattern in _WALMART_ITEM_PATTERNS:
        match = pattern.search(text)
        if match:
            return match.group("item_id")

    key = str(active_key or "").strip()
    numeric_parts = re.findall(r"(?<!\d)(\d{6,})(?!\d)", key)
    return numeric_parts[-1] if numeric_parts else None


async def recheck_walmart_observation(provider: Any, row: dict[str, Any]) -> WalmartRecheckResult:
    """Run one exact-item recheck through the shared anti-spam coordinator."""

    item_id = extract_walmart_item_id(row.get("url"), row.get("active_key"))
    if not item_id:
        return await _perform_walmart_recheck(provider, row)

    result = await guarded_walmart_recheck(
        item_id,
        lambda: _perform_walmart_recheck(provider, row),
        timeout_seconds=WALMART_RECHECK_PROVIDER_TIMEOUT_SECONDS,
    )
    if result is not None:
        return result
    return WalmartRecheckResult(
        status="timeout",
        item_id=item_id,
        old_price=_float_or_none(row.get("current_price")),
        old_discount=_float_or_none(row.get("discount")),
        message=(
            f"Walmart detail recheck exceeded {WALMART_RECHECK_PROVIDER_TIMEOUT_SECONDS}s. "
            "The cached row was left unchanged."
        ),
    )


async def _perform_walmart_recheck(provider: Any, row: dict[str, Any]) -> WalmartRecheckResult:
    item_id = extract_walmart_item_id(row.get("url"), row.get("active_key"))
    old_price = _float_or_none(row.get("current_price"))
    old_discount = _float_or_none(row.get("discount"))
    if not item_id:
        return WalmartRecheckResult(
            status="identity_missing",
            old_price=old_price,
            old_discount=old_discount,
            message="The cached row does not contain a trustworthy Walmart item ID, so SniperPlug refused to guess.",
        )

    detail_fetcher = getattr(provider, "fetch_product_detail_payload", None)
    if not callable(detail_fetcher):
        return WalmartRecheckResult(
            status="provider_unsupported",
            item_id=item_id,
            old_price=old_price,
            old_discount=old_discount,
            message="The registered Walmart provider cannot perform item-detail rechecks.",
        )

    try:
        payload = await detail_fetcher(item_id)
    except Exception as exc:
        return WalmartRecheckResult(
            status="error",
            item_id=item_id,
            old_price=old_price,
            old_discount=old_discount,
            message=f"Walmart detail recheck failed: {clean_error(exc)}",
        )

    inner = getattr(provider, "inner", provider)
    candidate_builder = getattr(inner, "_candidate_from_item", None)
    if not callable(candidate_builder):
        return WalmartRecheckResult(
            status="provider_unsupported",
            item_id=item_id,
            old_price=old_price,
            old_discount=old_discount,
            message="The Walmart provider returned detail data but cannot normalize it safely.",
        )

    try:
        candidate = candidate_builder(
            payload,
            request=ProviderScanRequest(source_key="walmart_recheck", query=item_id, max_results=1),
        )
    except Exception as exc:
        return WalmartRecheckResult(
            status="error",
            item_id=item_id,
            old_price=old_price,
            old_discount=old_discount,
            message=f"Walmart detail normalization failed: {clean_error(exc)}",
        )

    if candidate is None:
        return WalmartRecheckResult(
            status="unavailable",
            item_id=item_id,
            old_price=old_price,
            old_discount=old_discount,
            message="Walmart returned no usable offer for this exact item. The cached observation should be treated as stale.",
        )

    current_price = _float_or_none(getattr(candidate, "current_price", None))
    returned_id = str(getattr(candidate, "product_id", None) or getattr(candidate, "sku", None) or "").strip()
    if returned_id and returned_id != item_id:
        return WalmartRecheckResult(
            status="identity_mismatch",
            item_id=item_id,
            old_price=old_price,
            current_price=current_price,
            old_discount=old_discount,
            candidate=candidate,
            message=f"Walmart returned item `{returned_id}` instead of cached item `{item_id}`. SniperPlug refused to overwrite the row.",
        )

    stock_status = str(getattr(candidate, "stock_status", None) or "").strip().lower()
    can_add = getattr(candidate, "can_add_to_cart", None)
    unavailable = can_add is False or any(token in stock_status for token in ("out of stock", "unavailable", "sold out"))
    if unavailable:
        return WalmartRecheckResult(
            status="unavailable",
            item_id=item_id,
            old_price=old_price,
            current_price=current_price,
            old_discount=old_discount,
            candidate=candidate,
            message="Walmart currently reports this exact item as unavailable or not addable to cart.",
        )

    reference_price = _reference_price(candidate)
    current_discount = calculate_discount(current_price, reference_price)
    explicit_promotion = _has_explicit_promotion(candidate, row)
    price_changed = old_price is not None and current_price is not None and abs(old_price - current_price) >= 0.01

    if reference_price is None:
        if explicit_promotion:
            return WalmartRecheckResult(
                status="promotion_verified",
                item_id=item_id,
                old_price=old_price,
                current_price=current_price,
                old_discount=old_discount,
                current_discount=None,
                candidate=candidate,
                cache_state="active",
                message=(
                    "The exact Walmart promotion is still explicitly present, but Walmart did not return ordinary reference-price proof. "
                    "SniperPlug cleared the old markdown percentage instead of guessing."
                ),
            )
        if old_discount is not None and old_discount > 0:
            return WalmartRecheckResult(
                status="discount_unproven",
                item_id=item_id,
                old_price=old_price,
                current_price=current_price,
                old_discount=old_discount,
                current_discount=None,
                candidate=candidate,
                cache_state="stale",
                message=(
                    f"Walmart no longer returned a trustworthy reference price for the cached {old_discount:.0f}% markdown. "
                    "SniperPlug cleared that claim and removed the row from active deal results."
                ),
            )
        return WalmartRecheckResult(
            status="price_changed" if price_changed else "unchanged",
            item_id=item_id,
            old_price=old_price,
            current_price=current_price,
            old_discount=old_discount,
            current_discount=None,
            candidate=candidate,
            cache_state="active",
            message=(
                f"The exact Walmart item price changed from ${old_price:,.2f} to ${current_price:,.2f}, but no markdown percentage was claimed."
                if price_changed and old_price is not None and current_price is not None
                else "The exact Walmart item still matches the cached observed price. No unproven markdown percentage was added."
            ),
        )

    if current_discount is None or current_discount < 1:
        return WalmartRecheckResult(
            status="discount_gone",
            item_id=item_id,
            old_price=old_price,
            current_price=current_price,
            old_discount=old_discount,
            current_discount=0.0,
            reference_price=reference_price,
            candidate=candidate,
            cache_state="stale",
            message=(
                f"Walmart now reports ${current_price:,.2f} against a ${reference_price:,.2f} reference price, so a meaningful markdown is no longer proven. "
                "The cached row was removed from active deal results."
            ),
        )

    if old_discount is not None and current_discount > old_discount + 0.5:
        status = "deal_improved"
        message = f"The verified markdown improved from {old_discount:.0f}% to {current_discount:.0f}%."
    elif old_discount is not None and current_discount < old_discount - 0.5:
        status = "deal_weakened"
        message = f"The verified markdown weakened from {old_discount:.0f}% to {current_discount:.0f}%."
    elif price_changed:
        status = "price_changed"
        message = f"The exact Walmart item price changed from ${old_price:,.2f} to ${current_price:,.2f}; the verified markdown is now {current_discount:.0f}%."
    else:
        status = "unchanged"
        message = f"The exact Walmart item still matches the cached observed price with a verified {current_discount:.0f}% markdown."

    return WalmartRecheckResult(
        status=status,
        item_id=item_id,
        old_price=old_price,
        current_price=current_price,
        old_discount=old_discount,
        current_discount=current_discount,
        reference_price=reference_price,
        candidate=candidate,
        cache_state="active",
        message=message,
    )


async def persist_walmart_recheck(db: Any, guild_id: int, active_key: str, result: WalmartRecheckResult) -> None:
    conn = db.require_conn()
    now = datetime.now(timezone.utc).isoformat()
    candidate = result.candidate
    if result.status == "identity_mismatch":
        await conn.execute(
            "UPDATE guild_active_deal_cache SET status = 'stale', last_seen_at = ? WHERE guild_id = ? AND active_key = ?",
            (now, guild_id, active_key),
        )
    elif candidate is not None:
        await conn.execute(
            """
            UPDATE guild_active_deal_cache
            SET current_price = ?, discount = ?, status = ?, last_seen_at = ?
            WHERE guild_id = ? AND active_key = ?
            """,
            (result.current_price, result.current_discount, result.cache_status, now, guild_id, active_key),
        )
    elif result.cache_status == "stale":
        await conn.execute(
            "UPDATE guild_active_deal_cache SET status = 'stale', last_seen_at = ? WHERE guild_id = ? AND active_key = ?",
            (now, guild_id, active_key),
        )
    await conn.commit()


def calculate_discount(current_price: float | None, reference_price: float | None) -> float | None:
    if current_price is None or reference_price is None or reference_price <= 0 or current_price < 0:
        return None
    if current_price >= reference_price:
        return 0.0
    return round(((reference_price - current_price) / reference_price) * 100, 2)


def _reference_price(candidate: Any) -> float | None:
    for value in (
        getattr(candidate, "api_reference_price", None),
        getattr(candidate, "typical_price", None),
    ):
        parsed = _float_or_none(value)
        if parsed is not None and parsed > 0:
            return parsed
    attrs = getattr(candidate, "variant_attributes", None)
    if isinstance(attrs, dict):
        for key in ("apiReferencePrice", "referencePrice", "wasPrice", "listPrice"):
            parsed = _float_or_none(attrs.get(key))
            if parsed is not None and parsed > 0:
                return parsed
    return None


def _has_explicit_promotion(candidate: Any, row: dict[str, Any]) -> bool:
    lane = str(getattr(candidate, "deal_lane", None) or "").strip().lower()
    if any(token in lane for token in _PROMOTION_LANE_TOKENS):
        return True
    attrs = getattr(candidate, "variant_attributes", None)
    if isinstance(attrs, dict):
        for key in ("walmartCashSavings", "apiPromotionSavingsCap", "couponSavings"):
            value = _float_or_none(attrs.get(key))
            if value is not None and value > 0:
                return True
        if str(attrs.get("apiPromotionText") or "").strip():
            return True
    source = str(row.get("source_label") or "").lower()
    title = str(row.get("title") or "").lower()
    return "walmart_cash" in source or "walmart cash" in source or "walmart cash" in title


def clean_error(exc: Exception) -> str:
    text = " ".join(str(exc).split())
    return text[:220] if text else exc.__class__.__name__


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
