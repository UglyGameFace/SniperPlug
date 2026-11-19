"""Find/Hunt cards rebuilt from fresh verification (trust fix 3, 2026-11-18).

#339/#340 made every display re-verify prices at display time, but a card
that held was shown with its search-time numbers, so the displayed price
and % could be stale by up to the recheck tolerance (max($0.25, 1%)).
These tests pin the rebuild: when the fresh price differs from the
search price, the card the user sees carries the fresh price and a
discount computed from it against the same reference the verification
used. Holds/fails semantics are unchanged — a card that holds still
holds, a card that fails still drops, and the identical-price fast path
returns the original card object.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from sniperplug.cogs.deal_scanner import (
    build_walmart_cards,
    verify_find_cards,
)
from sniperplug.models.candidate import SourceCandidate
from sniperplug.providers.base import ProviderScanResult


def _scan_candidate(item_id: str, current: float, reference: float) -> SourceCandidate:
    return SourceCandidate(
        source_key="walmart",
        retailer="Walmart",
        title=f"Gadget {item_id}",
        product_url=f"https://www.walmart.com/ip/{item_id}",
        current_price=current,
        typical_price=reference,
        api_current_price=current,
        api_reference_price=reference,
        product_id=item_id,
        selected_offer_id=item_id,
        seller_name="Walmart",
    )


def _search_card(item_id: str, current: float, reference: float):
    result = ProviderScanResult(
        provider_key="walmart",
        candidates=(_scan_candidate(item_id, current, reference),),
        page=1,
        page_size=1,
        start_index=1,
        has_next_page=False,
    )
    cards = build_walmart_cards(result, min_discount=10, alerts_only=False)
    assert len(cards) == 1
    return cards[0]


def _outcome(candidate: SourceCandidate, classification: str = "verified_markdown"):
    return SimpleNamespace(
        candidate=candidate,
        classification=classification,
        status="verified",
    )


class FakeVerify:
    def __init__(self, outcomes: dict[str, object]):
        self.outcomes = outcomes
        self.calls: list[str] = []

    async def __call__(self, item_id: str):
        self.calls.append(item_id)
        return self.outcomes[item_id]


def _price_field(embed) -> str:
    for field in embed.fields:
        if str(field.name) == "💰 Price":
            return str(field.value)
    raise AssertionError("no price field on embed")


@pytest.mark.asyncio
async def test_card_shows_fresh_price_when_it_moves_within_tolerance():
    # Search proved $60.00 vs $100 (40% off). The re-check finds $60.20 —
    # inside the max($0.25, 1%) tolerance, so the card still holds under
    # a 40 threshold only via the tolerance rule — and the user must see
    # the FRESH $60.20 and its real 39.8% discount, not the stale $60/40%.
    card = _search_card("777", 60.0, 100.0)
    fresh = _scan_candidate("777", 60.20, 100.0)
    fake = FakeVerify({"777": _outcome(fresh)})
    shown, ran, dropped = await verify_find_cards(
        [card], provider=None, threshold=40, verify_one=fake
    )
    assert ran is True
    assert dropped == 0
    assert len(shown) == 1
    rebuilt = shown[0]
    assert rebuilt is not card
    assert rebuilt.current_price == pytest.approx(60.20)
    assert rebuilt.api_current_price == pytest.approx(60.20)
    assert rebuilt.api_reference_price == pytest.approx(100.0)
    assert rebuilt.discount == pytest.approx(39.8)
    assert rebuilt.api_discount_percent == pytest.approx(39.8)
    assert "$60.20" in _price_field(rebuilt.embed)
    assert "$60.00" not in _price_field(rebuilt.embed)


@pytest.mark.asyncio
async def test_card_shows_fresh_price_when_it_drops_further():
    # The price fell after the search: the shown discount must grow to
    # the fresh one, not stay at the search-time figure.
    card = _search_card("888", 60.0, 100.0)
    fresh = _scan_candidate("888", 50.0, 100.0)
    fake = FakeVerify({"888": _outcome(fresh)})
    shown, ran, dropped = await verify_find_cards(
        [card], provider=None, threshold=40, verify_one=fake
    )
    assert ran is True
    assert dropped == 0
    rebuilt = shown[0]
    assert rebuilt is not card
    assert rebuilt.current_price == pytest.approx(50.0)
    assert rebuilt.discount == pytest.approx(50.0)
    assert "$50.00" in _price_field(rebuilt.embed)


@pytest.mark.asyncio
async def test_identical_fresh_price_returns_the_original_card():
    card = _search_card("999", 60.0, 100.0)
    fresh = _scan_candidate("999", 60.0, 100.0)
    fake = FakeVerify({"999": _outcome(fresh)})
    shown, ran, dropped = await verify_find_cards(
        [card], provider=None, threshold=40, verify_one=fake
    )
    assert ran is True
    assert dropped == 0
    assert shown == [card]
    assert shown[0] is card
