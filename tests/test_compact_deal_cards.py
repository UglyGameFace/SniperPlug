"""User-facing deal cards stay short: no staff diagnostics on the card.

Felix (2026-11-20): the DM/public cards looked like grocery lists — Sniper
Read scores, routes, Offer ID hashes, Product Proof dumps, fulfillment
tables, "Why it showed up". Those are staff diagnostics; the card everyone
sees carries price, the priced option, positive stock info, shopper flags,
and a short footer. Nothing else.
"""
from __future__ import annotations

import discord

from sniperplug.cogs.deal_scanner import build_deal_card_embed
from sniperplug.models.candidate import SourceCandidate
from sniperplug.services.candidate_pipeline import evaluate_candidate
from sniperplug.services.hp_deal_cards import build_hp_deal_card

BANNED = (
    "Sniper Read",
    "Product Proof",
    "Why it showed up",
    "Offer ID",
    "Fulfillment",
    "Liveness",
    "Would alert",
    "Exact identity",
    "Verification",
    "SKU:",
    "UPC:",
)


def _walmart_embed() -> discord.Embed:
    candidate = SourceCandidate(
        source_key="walmart",
        retailer="Walmart",
        title="Madden NYC Men's Noryx Chelsea Boots",
        product_url="https://www.walmart.com/ip/20066263528",
        current_price=22.49,
        typical_price=65.0,
        product_id="20066263528",
        selected_offer_id="D0A33F0E900F322A93E2AD039801A5EA",
        seller_name="Walmart",
        stock_status="Available",
        can_add_to_cart=True,
        variant_attributes={
            "category": "Home Page/Clothing/Shoes/Mens Shoes/Mens Boots/Ankle Boots",
            "size": "8W",
            "color": "Black Gum",
            "brand": "Madden NYC",
            "modelNumber": "MNMN46AM110WE",
            "maxOrderQty": "99",
            "offerType": "ONLINE_AND_STORE",
            "availableOnline": "yes",
        },
    )
    decision = evaluate_candidate(candidate)
    return build_deal_card_embed(candidate, decision.deal, decision, 65.0)


def test_walmart_card_is_compact():
    embed = _walmart_embed()
    rendered = str(embed.to_dict())
    for banned in BANNED:
        assert banned not in rendered, banned
    names = [field.name for field in embed.fields]
    assert names[0] == "💰 Price"
    assert "🔗 Product links" in names
    # The priced option is shopper information and stays — without the
    # raw offer hash.
    assert "🎯 Selected option" in names
    option_field = next(f for f in embed.fields if f.name == "🎯 Selected option")
    assert "8W" in option_field.value
    assert embed.footer.text == "SniperPlug · prices can change — recheck before buying"


def test_hp_card_is_compact():
    candidate = SourceCandidate(
        source_key="hp",
        retailer="HP",
        title="HP Pavilion Laptop 15",
        product_url="https://www.hp.com/us-en/shop/pdp/hp-pavilion-15",
        current_price=299.99,
        typical_price=649.99,
        product_id="8XYZ123",
        sku="8XYZ123",
        seller_name="HP.com",
        stock_status="In stock",
        can_add_to_cart=True,
        api_current_price=299.99,
        api_reference_price=649.99,
        api_discount_percent=54.0,
    )
    card = build_hp_deal_card(candidate)
    rendered = str(card.embed.to_dict())
    for banned in BANNED:
        assert banned not in rendered, banned
    names = [field.name for field in card.embed.fields]
    assert "💵 HP.com price" in names
    assert "📉 Savings" in names
