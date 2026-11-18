"""Find display-time price re-verification (Felix's product bar, 2026-11-18).

/walmart find proves discounts from Walmart's SEARCH data. These tests pin
the upgrade: before Find shows a card, the item's current price is
re-verified through the same exact path /walmart check uses
(verify_single_exact_item), bounded to a pool of 8 cards with the first 5
that hold being shown, using the existing pre-send recheck tolerance
(max($0.25, 1%)). The summary may claim prices were re-checked just now
ONLY when verification actually ran; otherwise Find falls back to the
search-proven cards with no verified claim anywhere.
"""

from __future__ import annotations

from types import SimpleNamespace

import discord
import pytest

from sniperplug.cogs.deal_scanner import (
    DealCard,
    add_find_verification_field,
    verify_find_cards,
)


def _card(item_id: str, current: float, reference: float, discount: float) -> DealCard:
    card = DealCard(
        embed=discord.Embed(title=f"Item {item_id}"),
        url=f"https://www.walmart.com/ip/Item-{item_id}/{item_id}",
        label=f"Item {item_id}",
        discount=discount,
        api_current_price=current,
        api_reference_price=reference,
    )
    card.sku = item_id
    card.current_price = current
    card.typical_price = reference
    return card


def _candidate(current: float | None, reference: float | None):
    return SimpleNamespace(
        api_current_price=current,
        current_price=current,
        api_reference_price=reference,
        typical_price=reference,
    )


def _outcome(current: float | None, reference: float | None, classification: str = "verified_markdown"):
    return SimpleNamespace(
        candidate=_candidate(current, reference),
        classification=classification,
        status="verified",
    )


class FakeVerify:
    """Counting stand-in for verify_single_exact_item."""

    def __init__(self, outcomes: dict[str, object]):
        self.outcomes = outcomes
        self.calls: list[str] = []

    async def __call__(self, item_id: str):
        self.calls.append(item_id)
        outcome = self.outcomes[item_id]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.mark.asyncio
async def test_price_moved_up_on_recheck_drops_the_card():
    # Search proved 40% off ($60 vs $100). The exact re-check finds $90 —
    # only 10% off, far beyond the $0.25/1% tolerance — so the card must
    # not be shown under a 40% threshold.
    card = _card("111", 60.0, 100.0, 40.0)
    fake = FakeVerify({"111": _outcome(90.0, 100.0, classification="verified_under_threshold")})
    shown, ran, dropped = await verify_find_cards(
        [card], provider=None, threshold=40, verify_one=fake
    )
    assert shown == []
    assert ran is True
    assert dropped == 1


@pytest.mark.asyncio
async def test_verified_price_holds_and_card_is_shown():
    card = _card("222", 60.0, 100.0, 40.0)
    fake = FakeVerify({"222": _outcome(60.0, 100.0)})
    shown, ran, dropped = await verify_find_cards(
        [card], provider=None, threshold=40, verify_one=fake
    )
    assert shown == [card]
    assert ran is True
    assert dropped == 0


@pytest.mark.asyncio
async def test_sub_tolerance_move_still_holds():
    # $60.00 -> $60.20: the discount dips to 39.8% (a hair under the 40
    # threshold) but the move is inside the existing recheck tolerance
    # (max($0.25, 1% of $60) = $0.60), so the card holds — same standard
    # as the pre-send recheck in the extreme-price fanout.
    card = _card("333", 60.0, 100.0, 40.0)
    fake = FakeVerify({"333": _outcome(60.20, 100.0)})
    shown, ran, dropped = await verify_find_cards(
        [card], provider=None, threshold=40, verify_one=fake
    )
    assert shown == [card]
    assert ran is True
    assert dropped == 0


@pytest.mark.asyncio
async def test_not_buyable_on_recheck_drops_the_card():
    card = _card("444", 60.0, 100.0, 40.0)
    fake = FakeVerify({"444": _outcome(60.0, 100.0, classification="not_buyable")})
    shown, ran, dropped = await verify_find_cards(
        [card], provider=None, threshold=40, verify_one=fake
    )
    assert shown == []
    assert ran is True
    assert dropped == 1


@pytest.mark.asyncio
async def test_verification_unavailable_falls_back_without_claim():
    cards = [_card(str(1000 + i), 60.0, 100.0, 40.0) for i in range(6)]
    unavailable = SimpleNamespace(candidate=None, classification=None, status="provider_unavailable")
    fake = FakeVerify({card.sku: unavailable for card in cards})
    shown, ran, dropped = await verify_find_cards(
        cards, provider=None, threshold=40, verify_one=fake
    )
    # Search-proven cards are shown as before (first 5), no verified claim
    # is made, and the provider_unavailable status stops the attempts
    # early instead of burning one call per card.
    assert shown == cards[:5]
    assert ran is False
    assert dropped == 0
    assert len(fake.calls) == 1


@pytest.mark.asyncio
async def test_verification_raising_falls_back_without_claim():
    cards = [_card("555", 60.0, 100.0, 40.0), _card("556", 50.0, 100.0, 50.0)]
    fake = FakeVerify({card.sku: RuntimeError("boom") for card in cards})
    shown, ran, dropped = await verify_find_cards(
        cards, provider=None, threshold=40, verify_one=fake
    )
    assert shown == cards
    assert ran is False
    assert dropped == 0


@pytest.mark.asyncio
async def test_verification_cap_is_respected():
    # Pool of 10 cards; the first 8 fail the re-check. Verification must
    # stop at the 8-card cap even though nothing was shown yet.
    cards = [_card(str(2000 + i), 60.0, 100.0, 40.0) for i in range(10)]
    fake = FakeVerify(
        {card.sku: _outcome(95.0, 100.0, classification="verified_under_threshold") for card in cards}
    )
    shown, ran, dropped = await verify_find_cards(
        cards, provider=None, threshold=40, verify_one=fake
    )
    assert len(fake.calls) == 8
    assert shown == []
    assert ran is True
    assert dropped == 8


@pytest.mark.asyncio
async def test_verification_stops_once_five_hold():
    cards = [_card(str(3000 + i), 60.0, 100.0, 40.0) for i in range(8)]
    fake = FakeVerify({card.sku: _outcome(60.0, 100.0) for card in cards})
    shown, ran, dropped = await verify_find_cards(
        cards, provider=None, threshold=40, verify_one=fake
    )
    assert shown == cards[:5]
    assert len(fake.calls) == 5
    assert ran is True
    assert dropped == 0


def test_summary_claim_only_when_verification_ran():
    summary = discord.Embed(title="t")
    add_find_verification_field(summary, verification_ran=True, dropped=2, shown_count=3)
    fields = {field.name: field.value for field in summary.fields}
    assert "✅ Prices re-checked just now" in fields
    assert "2" in fields["✅ Prices re-checked just now"]

    fallback = discord.Embed(title="t")
    add_find_verification_field(fallback, verification_ran=False, dropped=0, shown_count=5)
    fallback_fields = {field.name: field.value for field in fallback.fields}
    assert "✅ Prices re-checked just now" not in fallback_fields
    assert all("re-checked just now" not in value for value in fallback_fields.values())
