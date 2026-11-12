from __future__ import annotations

"""Conservative exact-item comparison for independently sourced sold records.

This module does not fetch sale records. It validates *identities*, not the
authenticity or authorization of a remote provider. A seller's asking price,
brand match, model-family keyword or "identity matched=yes" flag is never
sufficient proof that two listings represent the same buyable variant.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import re
from typing import Any


AUTHORIZED_SOLD_COMP_SOURCE = "ebay_marketplace_insights"
SOLD_COMP_MAX_OBSERVATION_AGE_HOURS = 48


@dataclass(frozen=True)
class ProductIdentityMatch:
    matched: bool
    reason: str
    canonical_gtin: str = ""


def canonical_gtin(value: Any) -> str:
    """Normalize valid GTIN-8/12/13/14 to GTIN-14 with a checked digit.

    Leading zeros let UPC-A and EAN-13 representations of the same product
    match. Invalid checksums or arbitrary identifiers must not match.
    """
    raw = str(value or "").strip()
    if not re.fullmatch(r"[0-9]{8}|[0-9]{12}|[0-9]{13}|[0-9]{14}", raw):
        return ""
    digits = [int(c) for c in raw]
    weighted_sum = sum(
        digit * (3 if index % 2 == 0 else 1)
        for index, digit in enumerate(reversed(digits[:-1]))
    )
    expected = (10 - weighted_sum % 10) % 10
    if digits[-1] != expected:
        return ""
    return raw.zfill(14)


def _normalize_condition(value: Any) -> str:
    text = " ".join(str(value or "").lower().replace("-", " ").split())
    aliases = {
        "new": "new", "brand new": "new", "new in box": "new",
        "new with tags": "new", "new without tags": "new",
        "open box": "open_box", "open box like new": "open_box",
        "refurbished": "refurbished", "manufacturer refurbished": "refurbished",
        "seller refurbished": "refurbished", "restored": "refurbished",
        "renewed": "refurbished", "used": "used", "pre owned": "used",
        "like new": "like_new", "used like new": "like_new",
        "for parts": "parts", "parts only": "parts",
    }
    return aliases.get(text, "")


def _normalize_variant(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def match_exact_product_identity(
    *,
    retailer_gtin: Any,
    sold_gtin: Any,
    retailer_condition: Any,
    sold_condition: Any,
    retailer_pack: Any = None,
    sold_pack: Any = None,
    retailer_model: Any = None,
    sold_model: Any = None,
    retailer_variant: Any = None,
    sold_variant: Any = None,
) -> ProductIdentityMatch:
    """Fail closed on missing barcode, mismatched condition or known variant.

    A validated matching GTIN is a strong item identity; a condition match is
    still mandatory for comparing prices across new/open-box/refurbished goods.
    Optional explicit pack/model/variant values must agree if either is known.
    """
    left, right = canonical_gtin(retailer_gtin), canonical_gtin(sold_gtin)
    if not left or not right:
        return ProductIdentityMatch(False, "missing or invalid GTIN on either side")
    if left != right:
        return ProductIdentityMatch(False, "GTIN mismatch")
    condition_a, condition_b = _normalize_condition(retailer_condition), _normalize_condition(sold_condition)
    if not condition_a or not condition_b:
        return ProductIdentityMatch(False, "condition missing or unrecognized", left)
    if condition_a != condition_b or condition_a == "parts":
        return ProductIdentityMatch(False, "condition mismatch or parts-only listing", left)

    for name, first, second in (
        ("pack quantity", retailer_pack, sold_pack),
        ("model", retailer_model, sold_model),
        ("variant", retailer_variant, sold_variant),
    ):
        left_value, right_value = _normalize_variant(first), _normalize_variant(second)
        if (left_value or right_value) and left_value != right_value:
            return ProductIdentityMatch(False, f"{name} mismatch or unspecified counterpart", left)
    return ProductIdentityMatch(True, "matched valid GTIN, condition and supplied variants", left)


def authorized_sold_comp_observation(attrs: dict[str, Any], *, now: datetime | None = None) -> bool:
    """Require explicit approved source and a fresh source observation timestamp.

    It is not a network signature: the upstream adapter must still authenticate
    the API, enforce access rights and produce correct completed-sale records.
    """
    if str(attrs.get("ebaySoldCompSource") or "").strip().lower() != AUTHORIZED_SOLD_COMP_SOURCE:
        return False
    raw = str(attrs.get("ebaySoldCompFetchedAt") or "").strip()
    try:
        observed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (TypeError, ValueError, OverflowError):
        return False
    if observed.tzinfo is None:
        return False
    current = now or datetime.now(timezone.utc)
    age = current.astimezone(timezone.utc) - observed.astimezone(timezone.utc)
    return timedelta(minutes=-5) <= age <= timedelta(hours=SOLD_COMP_MAX_OBSERVATION_AGE_HOURS)
