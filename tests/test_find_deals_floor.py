"""Regression: /walmart find must never present normal-price products as deals.

Felix's report (2026-11-18): "when using find deals, it doesn't actually
find deals. just products at normal price."

Root cause pinned here: the simple-mode fallback ladder
BEGINNER_FALLBACK_DISCOUNTS bottomed out at 0, so when a search page held
nothing at the requested discount, find relaxed 40 -> 30 -> 10 -> 0 and
returned full-price products (discount_percent clamps at 0.0, so even a
price increase reads as "0% off") under a summary claiming "best proven
deals". SniperPlug's own verify line is 10% off; below that is not a deal
anywhere in this system, and the hunt presets' ladder already floors at 10.
"""

from __future__ import annotations

from sniperplug.cogs.deal_scanner import (
    BEGINNER_FALLBACK_DISCOUNTS,
    cards_with_fallback,
)
from sniperplug.models.candidate import SourceCandidate
from sniperplug.providers.base import ProviderScanResult


def _candidate(title: str, current: float, typical: float) -> SourceCandidate:
    return SourceCandidate(
        source_key="walmart",
        retailer="walmart",
        title=title,
        product_url=f"https://www.walmart.com/ip/{title.replace(' ', '-')}/12345",
        current_price=current,
        typical_price=typical,
    )


def _result(*candidates: SourceCandidate) -> ProviderScanResult:
    return ProviderScanResult(
        provider_key="walmart",
        candidates=tuple(candidates),
        total_results=len(candidates),
        page=1,
        page_size=len(candidates),
    )


def test_find_never_shows_normal_price_products():
    # A page with only a full-price item (0% off) and a token 5% markdown:
    # neither reaches SniperPlug's 10% verify line, so find must come back
    # empty (the honest "nothing useful found" path), not present these
    # products as deals.
    result = _result(
        _candidate("Full Price Widget", 100.0, 100.0),
        _candidate("Barely Marked Down Gadget", 95.0, 100.0),
    )
    cards, shown_discount = cards_with_fallback(
        result, 40, False, BEGINNER_FALLBACK_DISCOUNTS
    )
    assert cards == []
    assert shown_discount == 40


def test_find_still_relaxes_to_real_deals():
    # The relaxation itself is a feature: a genuine 25% markdown still
    # surfaces when nothing reaches the requested 40%, labelled at the
    # relaxed threshold — while the full-price item on the same page
    # stays out.
    result = _result(
        _candidate("Full Price Widget", 100.0, 100.0),
        _candidate("Real Deal Thing", 75.0, 100.0),
    )
    cards, shown_discount = cards_with_fallback(
        result, 40, False, BEGINNER_FALLBACK_DISCOUNTS
    )
    assert shown_discount == 10
    assert len(cards) == 1
    assert cards[0].discount >= 10


def test_beginner_fallback_ladder_never_reaches_zero():
    assert 0 not in BEGINNER_FALLBACK_DISCOUNTS
    assert min(BEGINNER_FALLBACK_DISCOUNTS) >= 10
