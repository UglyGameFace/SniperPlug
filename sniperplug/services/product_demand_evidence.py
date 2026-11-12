from __future__ import annotations

"""Source-backed product engagement is a proxy, never evidence of sold volume."""

from dataclasses import dataclass
import math
from typing import Any


@dataclass(frozen=True)
class ProductDemandEvidence:
    status: str = "unverified"
    review_count: int = 0
    rating: float | None = None
    rank_points: int = 0
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
            reason="Retailer reviews below quality floor; no sales claim",
        )
    points = 12 if reviews < 500 else 20 if reviews < 2000 else 26
    if rating >= 4.5:
        points += 4
    return ProductDemandEvidence(
        status="retailer_engagement",
        review_count=reviews,
        rating=rating,
        rank_points=points,
        reason=f"Walmart item metadata: {reviews:,} reviews, {rating:.1f}/5 (engagement proxy only)",
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
