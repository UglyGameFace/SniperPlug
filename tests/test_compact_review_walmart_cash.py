from types import SimpleNamespace

import discord

from sniperplug.services.scout_lane_polish import (
    WALMART_CASH_FIELD,
    polish_public_scout_card,
)


def make_card(*, cash: str, proof: str = "yes", price: float = 20.0):
    return SimpleNamespace(
        embed=discord.Embed(
            title="Walmart Cash review lead",
            description="Internal diagnostic text that should be compacted.",
        ),
        label="Walmart Cash review lead",
        url="https://www.walmart.com/ip/123",
        retailer="Walmart",
        sku="123",
        upc="456",
        selected_offer_id="offer-1",
        current_price=price,
        discount=0,
        score=40,
        variant_attributes={
            "walmartCashSavings": cash,
            "walmartCashApiProof": proof,
            "cashAmountConfirmed": proof,
        },
    )


def test_compact_card_preserves_confirmed_walmart_cash_amount():
    card = polish_public_scout_card(make_card(cash="15.00"), rank=95, min_discount=50, position=1)
    fields = {field.name: field.value for field in card.embed.fields}

    assert WALMART_CASH_FIELD in fields
    assert "$15.00 Walmart Cash" in fields[WALMART_CASH_FIELD]
    assert "Effective after reward: **$5.00**" in fields[WALMART_CASH_FIELD]
    assert card.should_alert is False
    assert "Not a verified markdown deal" in fields["🧭 Review status"]


def test_unconfirmed_cash_wording_never_displays_an_amount():
    card = polish_public_scout_card(make_card(cash="15.00", proof="no"), rank=95, min_discount=50, position=1)
    fields = {field.name: field.value for field in card.embed.fields}

    assert WALMART_CASH_FIELD not in fields
    assert card.should_alert is False


def test_impossible_cash_amount_is_suppressed():
    card = polish_public_scout_card(make_card(cash="9999.00", price=20.0), rank=95, min_discount=50, position=1)
    fields = {field.name: field.value for field in card.embed.fields}

    assert WALMART_CASH_FIELD not in fields
