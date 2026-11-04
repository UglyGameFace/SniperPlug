from __future__ import annotations

"""Ratio price-error signals used for exact-verification *priority*.

Items far below a trusted reference are classified as ``verified_markdown``
and delivered by the normal markdown fanout, so the ratio rule only decides
how quickly the exact-detail queue verifies them.
"""

from dataclasses import replace

from sniperplug.services.walmart_exact_verification_queue import (
    _classify_exact_candidate,
)
from sniperplug.services.walmart_extreme_price_signals import (
    is_price_error_ratio,
    is_priority_extreme_discovery,
)
from tests.test_walmart_extreme_price_alerts_295 import candidate


TV = 'onn. 65" Class 4K UHD LED Roku Smart Television'


def priced(*, title: str, price: float, reference: float | None):
    base = candidate(title=title, price=price)
    attrs = dict(base.variant_attributes)
    if reference is not None:
        attrs["referencePriceTrusted"] = "yes"
        attrs["trustedReferencePrice"] = f"{reference:.2f}"
    return replace(
        base, api_reference_price=reference, typical_price=reference,
        variant_attributes=attrs,
    )


def test_ratio_helper_boundaries() -> None:
    assert is_price_error_ratio(49.0, 600.0)
    assert is_price_error_ratio(20.0, 100.0)  # exactly 20%
    assert not is_price_error_ratio(20.01, 100.0)
    assert not is_price_error_ratio(5.0, 49.99)  # reference too small
    assert not is_price_error_ratio(0, 100.0)
    assert not is_price_error_ratio(10.0, None)


def test_discovery_priority_for_ratio_errors_skips_accessories() -> None:
    tv = priced(title=TV, price=49.0, reference=598.0)
    assert is_priority_extreme_discovery(tv, current_price=49.0, trusted_reference=598.0)
    assert not is_priority_extreme_discovery(
        replace(tv, title="TV Wall Mount"), current_price=49.0, trusted_reference=598.0,
    )
    assert not is_priority_extreme_discovery(tv, current_price=299.0, trusted_reference=598.0)


def test_ratio_price_errors_reach_the_normal_markdown_lane() -> None:
    """Guards against re-adding a separate lane that can never see these rows."""
    tv = priced(title=TV, price=49.0, reference=598.0)
    assert _classify_exact_candidate(tv, min_discount=10) == "verified_markdown"
