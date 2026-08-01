from __future__ import annotations

import asyncio
from types import SimpleNamespace

import discord

from sniperplug.services.manual_review_share import ManualReviewPageButton, ManualReviewShareView, ManualShareButton
from sniperplug.services.review_card_enrichment import (
    DISCOVERY_TAG_FIELD,
    GLOBAL_OBSERVED_REFERENCE_SOURCE,
    PRICE_HISTORY_FIELD,
    enrich_review_card,
)


def _card(
    *,
    title: str = "Clearance test",
    current_price: float = 10.0,
    finder_query: str = "toy clearance",
    attrs: dict | None = None,
):
    embed = discord.Embed(title=title)
    embed.add_field(name="🧾 API fields", value=f"• Finder query: **{finder_query}**", inline=False)
    card = SimpleNamespace(
        embed=embed,
        current_price=current_price,
        label=title,
        variant_attributes=dict(attrs or {}),
    )
    return card


def test_review_card_gets_clearance_tag_and_explicit_missing_history() -> None:
    card = _card(
        attrs={
            "exactDetailPriceProof": "yes",
            "exactDetailItemId": "123",
            "exactDetailReferenceStatus": "missing",
        }
    )
    enrich_review_card(card)
    fields = {field.name: field.value for field in card.embed.fields}
    assert "`Clearance`" in fields[DISCOVERY_TAG_FIELD]
    assert "Walmart was price" in fields[PRICE_HISTORY_FIELD]
    assert "Not returned" in fields[PRICE_HISTORY_FIELD]
    assert "Learning" in fields[PRICE_HISTORY_FIELD]
    assert "never assumed to be the original price" in fields[PRICE_HISTORY_FIELD]
    assert "Current price" in fields[PRICE_HISTORY_FIELD]


def test_review_card_uses_trusted_walmart_was_price_when_present() -> None:
    card = _card(
        current_price=10.0,
        attrs={
            "exactDetailPriceProof": "yes",
            "exactDetailReferenceStatus": "trusted",
        },
    )
    card.api_reference_price = 25.0
    card.api_reference_path = "wasPrice"
    enrich_review_card(card)
    fields = {field.name: field.value for field in card.embed.fields}
    assert "Walmart was price" in fields[PRICE_HISTORY_FIELD]
    assert "$25.00" in fields[PRICE_HISTORY_FIELD]
    assert "exact item-detail response" in fields[PRICE_HISTORY_FIELD]


def test_observed_baseline_is_never_called_walmart_original_price() -> None:
    card = _card(
        current_price=10.0,
        attrs={
            "exactDetailPriceProof": "yes",
            "priceMemoryIdentity": "walmart-offer:v1:" + "a" * 64,
            "trustedReferenceSource": GLOBAL_OBSERVED_REFERENCE_SOURCE,
        },
    )
    card.api_reference_price = 25.0
    card.api_reference_path = GLOBAL_OBSERVED_REFERENCE_SOURCE
    enrich_review_card(card)
    value = {field.name: field.value for field in card.embed.fields}[PRICE_HISTORY_FIELD]
    assert "Walmart was price:** Not returned" in value
    assert "Previously observed by SniperPlug" in value
    assert "$25.00" in value
    assert "not Walmart's official original or was price" in value


def test_review_view_enriches_every_page_card() -> None:
    view = ManualReviewShareView([_card(title=f"Item {index}") for index in range(4)], page_size=3)
    assert len(view.page_embeds()) == 3
    assert all(any(field.name == DISCOVERY_TAG_FIELD for field in embed.fields) for embed in view.page_embeds())


class _FakeResponse:
    def __init__(self):
        self.deferred = False
        self.messages: list[str] = []

    async def defer(self, *args, **kwargs):
        self.deferred = True

    async def send_message(self, message: str, *args, **kwargs):
        self.messages.append(message)


class _FakeFollowup:
    def __init__(self):
        self.messages: list[str] = []

    async def send(self, message: str, *args, **kwargs):
        self.messages.append(message)


class _FakeInteraction:
    def __init__(self):
        self.response = _FakeResponse()
        self.followup = _FakeFollowup()
        self.guild_id = 1
        self.edited: dict | None = None

    async def edit_original_response(self, **kwargs):
        self.edited = kwargs


def test_next_button_retains_parent_view_after_controls_are_rebuilt() -> None:
    view = ManualReviewShareView([_card(title=f"Item {index}") for index in range(4)], page_size=3)
    next_button = next(
        item
        for item in view.children
        if isinstance(item, ManualReviewPageButton) and item.direction == 1
    )
    interaction = _FakeInteraction()

    asyncio.run(next_button.callback(interaction))

    assert interaction.response.deferred is True
    assert interaction.followup.messages == []
    assert interaction.edited is not None
    assert view.page == 1
    assert len(interaction.edited["embeds"]) == 1
    assert interaction.edited["view"] is view
    assert "Page **2/2**" in interaction.edited["content"]


def test_interaction_buttons_defer_before_slow_work() -> None:
    page_names = ManualReviewPageButton.callback.__code__.co_names
    share_names = ManualShareButton.callback.__code__.co_names
    assert "defer" in page_names
    assert "edit_original_response" in page_names
    assert "defer" in share_names
    assert "followup" in share_names
