from __future__ import annotations

"""Source-backed product engagement is a proxy, never evidence of sold volume."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import math
from typing import Any


@dataclass(frozen=True)
class ProductDemandEvidence:
    status: str = "unverified"
    review_count: int = 0
    rating: float | None = None
    rank_points: int = 0
    bestseller_search_position: int | None = None
    reason: str = "No item-level interest evidence available"

    @property
    def has_engagement_proxy(self) -> bool:
        return self.status == "retailer_engagement"


def assess_product_demand(record: Any) -> ProductDemandEvidence:
    """Read exact Walmart item fields, never subjective category or embed text.

    A rating/review count is not sales, does not prove recent market demand,
    and is not public price proof. Unknown or untrusted evidence scores zero.
    """
    retailer = str(
        getattr(record, "retailer", None)
        or getattr(getattr(record, "candidate", None), "retailer", None)
        or getattr(getattr(record, "deal", None), "retailer", None)
        or ""
    ).strip().lower()
    if retailer != "walmart":
        return ProductDemandEvidence()

    attrs = _attributes(record)
    position = _bestseller_position(attrs)
    rating = _number(attrs.get("rating") or attrs.get("customerRating"))
    reviews_value = _number(attrs.get("reviews") or attrs.get("numReviews"))
    if rating is None or not 0 < rating <= 5 or reviews_value is None:
        return ProductDemandEvidence()
    if reviews_value < 0 or reviews_value > 10_000_000 or not reviews_value.is_integer():
        return ProductDemandEvidence()
    reviews = int(reviews_value)
    if reviews < 100 or rating < 4.0:
        return ProductDemandEvidence(
            status="insufficient_engagement",
            review_count=reviews,
            rating=rating,
            bestseller_search_position=position,
            reason="Retailer reviews below quality floor; no sales claim",
        )
    points = 12 if reviews < 500 else 20 if reviews < 2000 else 26
    if rating >= 4.5:
        points += 4
    # A top-five place in a requested bestseller sort is a weak secondary
    # signal ONLY when independent rating/review metadata is credible. It
    # never elevates an unknown or poor-quality product by itself.
    if position is not None and position <= 5:
        points += 4
    position_note = (
        f"; bestseller-sort request position {position} (API ordering unverified)"
        if position is not None else ""
    )
    return ProductDemandEvidence(
        status="retailer_engagement",
        review_count=reviews,
        rating=rating,
        rank_points=points,
        bestseller_search_position=position,
        reason=f"Walmart item metadata: {reviews:,} reviews, {rating:.1f}/5 (engagement proxy only){position_note}",
    )


def _attributes(record: Any) -> dict[str, Any]:
    # Deliberately never parse rendered Discord embeds into evidence.
    attrs: dict[str, Any] = {}
    for obj in (getattr(record, "deal", None), getattr(record, "candidate", None), record):
        item = getattr(obj, "variant_attributes", None)
        if isinstance(item, dict):
            attrs.update(item)
    return attrs


def _number(value: Any) -> float | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        number = float(str(value).replace(",", "").strip())
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None



def _bestseller_position(attrs: dict[str, Any]) -> int | None:
    if str(attrs.get("walmartBestsellerSortRequested") or "").lower() != "yes":
        return None
    if not str(attrs.get("walmartBestsellerSearchQuery") or "").strip():
        return None
    observed_at = str(attrs.get("walmartBestsellerObservedAt") or "").strip()
    try:
        observed = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
    except (TypeError, ValueError, OverflowError):
        return None
    if observed.tzinfo is None:
        return None
    age = datetime.now(timezone.utc) - observed.astimezone(timezone.utc)
    if not timedelta(minutes=-5) <= age <= timedelta(hours=48):
        return None
    raw = str(attrs.get("walmartBestsellerSearchPosition") or "").strip()
    if not raw.isdecimal():
        return None
    position = int(raw)
    return position if 1 <= position <= 25 else None
