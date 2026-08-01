from __future__ import annotations

from types import SimpleNamespace

import discord

from sniperplug.services.manual_review_share import ManualReviewPageButton, ManualReviewShareView, ManualShareButton
from sniperplug.services.review_card_enrichment import DISCOVERY_TAG_FIELD, PRICE_HISTORY_FIELD, enrich_review_card


def _card(*, title: str = "Clearance test", current_price: float = 10.0, finder_query: str = "toy clearance"):
    embed = discord.Embed(title=title)
    embed.add_field(name="🧾 API fields", value=f"• Finder query: **{finder_query}**", inline=False)
    card = SimpleNamespace(embed=embed, current_price=current_price, label=title, variant_attributes={})
    return card


def test_review_card_gets_clearance_tag_and_explicit_missing_history() -> None:
    card = _card()
    enrich_review_card(card)
    fields = {field.name: field.value for field in card.embed.fields}
    assert "`Clearance`" in fields[DISCOVERY_TAG_FIELD]
    assert "Previous trustworthy price" in fields[PRICE_HISTORY_FIELD]
    assert "Not available yet" in fields[PRICE_HISTORY_FIELD]
    assert "Current price" in fields[PRICE_HISTORY_FIELD]


def test_review_card_uses_trusted_walmart_was_price_when_present() -> None:
    card = _card(current_price=10.0)
    card.api_reference_price = 25.0
    enrich_review_card(card)
    fields = {field.name: field.value for field in card.embed.fields}
    assert "Walmart was/reference" in fields[PRICE_HISTORY_FIELD]
    assert "$25.00" in fields[PRICE_HISTORY_FIELD]


def test_review_view_enriches_every_page_card() -> None:
    view = ManualReviewShareView([_card(title=f"Item {index}") for index in range(4)], page_size=3)
    assert len(view.page_embeds()) == 3
    assert all(any(field.name == DISCOVERY_TAG_FIELD for field in embed.fields) for embed in view.page_embeds())


def test_interaction_buttons_defer_before_slow_work() -> None:
    page_names = ManualReviewPageButton.callback.__code__.co_names
    share_names = ManualShareButton.callback.__code__.co_names
    assert "defer" in page_names
    assert "edit_original_response" in page_names
    assert "defer" in share_names
    assert "followup" in share_names
