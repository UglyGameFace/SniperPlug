from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sniperplug.providers.base import ProviderScanRequest


_WALMART_ITEM_PATTERNS = (
    re.compile(r"/ip/(?:[^/?#]+/)?(?P<item_id>\d{6,})(?:[/?#]|$)", re.IGNORECASE),
    re.compile(r"[?&](?:itemId|item_id)=(?P<item_id>\d{6,})(?:&|$)", re.IGNORECASE),
)


@dataclass(frozen=True)
class WalmartRecheckResult:
    status: str
    message: str
    item_id: str | None = None
    old_price: float | None = None
    current_price: float | None = None
    candidate: Any | None = None

    @property
    def cache_status(self) -> str:
        return "stale" if self.status in {"unavailable", "identity_mismatch"} else "active"


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
    item_id = extract_walmart_item_id(row.get("url"), row.get("active_key"))
    if not item_id:
        return WalmartRecheckResult(
            status="identity_missing",
            message="The cached row does not contain a trustworthy Walmart item ID, so SniperPlug refused to guess.",
        )

    detail_fetcher = getattr(provider, "fetch_product_detail_payload", None)
    if not callable(detail_fetcher):
        return WalmartRecheckResult(
            status="provider_unsupported",
            item_id=item_id,
            message="The registered Walmart provider cannot perform item-detail rechecks.",
        )

    try:
        payload = await detail_fetcher(item_id)
    except Exception as exc:
        return WalmartRecheckResult(
            status="error",
            item_id=item_id,
            old_price=_float_or_none(row.get("current_price")),
            message=f"Walmart detail recheck failed: {clean_error(exc)}",
        )

    inner = getattr(provider, "inner", provider)
    candidate_builder = getattr(inner, "_candidate_from_item", None)
    if not callable(candidate_builder):
        return WalmartRecheckResult(
            status="provider_unsupported",
            item_id=item_id,
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
            old_price=_float_or_none(row.get("current_price")),
            message=f"Walmart detail normalization failed: {clean_error(exc)}",
        )

    if candidate is None:
        return WalmartRecheckResult(
            status="unavailable",
            item_id=item_id,
            old_price=_float_or_none(row.get("current_price")),
            message="Walmart returned no usable offer for this exact item. The cached observation should be treated as stale.",
        )

    returned_id = str(getattr(candidate, "product_id", None) or getattr(candidate, "sku", None) or "").strip()
    if returned_id and returned_id != item_id:
        return WalmartRecheckResult(
            status="identity_mismatch",
            item_id=item_id,
            old_price=_float_or_none(row.get("current_price")),
            current_price=_float_or_none(getattr(candidate, "current_price", None)),
            candidate=candidate,
            message=f"Walmart returned item `{returned_id}` instead of cached item `{item_id}`. SniperPlug refused to overwrite the row.",
        )

    old_price = _float_or_none(row.get("current_price"))
    current_price = _float_or_none(getattr(candidate, "current_price", None))
    stock_status = str(getattr(candidate, "stock_status", None) or "").strip().lower()
    can_add = getattr(candidate, "can_add_to_cart", None)
    unavailable = can_add is False or any(token in stock_status for token in ("out of stock", "unavailable", "sold out"))
    if unavailable:
        return WalmartRecheckResult(
            status="unavailable",
            item_id=item_id,
            old_price=old_price,
            current_price=current_price,
            candidate=candidate,
            message="Walmart currently reports this exact item as unavailable or not addable to cart.",
        )

    if old_price is not None and current_price is not None and abs(old_price - current_price) >= 0.01:
        return WalmartRecheckResult(
            status="price_changed",
            item_id=item_id,
            old_price=old_price,
            current_price=current_price,
            candidate=candidate,
            message=f"The exact Walmart item price changed from ${old_price:,.2f} to ${current_price:,.2f}.",
        )

    return WalmartRecheckResult(
        status="unchanged",
        item_id=item_id,
        old_price=old_price,
        current_price=current_price,
        candidate=candidate,
        message="The exact Walmart item still matches the cached observed price. Seller, variant, and stock proof were refreshed from the official detail response.",
    )


async def persist_walmart_recheck(db: Any, guild_id: int, active_key: str, result: WalmartRecheckResult) -> None:
    conn = db.require_conn()
    now = datetime.now(timezone.utc).isoformat()
    candidate = result.candidate
    if result.cache_status == "stale":
        await conn.execute(
            "UPDATE guild_active_deal_cache SET status = 'stale', last_seen_at = ? WHERE guild_id = ? AND active_key = ?",
            (now, guild_id, active_key),
        )
    elif candidate is not None:
        await conn.execute(
            """
            UPDATE guild_active_deal_cache
            SET current_price = ?, status = 'active', last_seen_at = ?
            WHERE guild_id = ? AND active_key = ?
            """,
            (_float_or_none(getattr(candidate, "current_price", None)), now, guild_id, active_key),
        )
    await conn.commit()


def clean_error(exc: Exception) -> str:
    text = " ".join(str(exc).split())
    return text[:220] if text else exc.__class__.__name__


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
