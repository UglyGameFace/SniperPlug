import discord

from sniperplug.cogs.auto_discovery import SAFE_EMBED_MESSAGE_LIMIT, batch_cards_for_embed_limit, embed_text_size
from sniperplug.cogs.deal_scanner import DealCard


def make_card(label: str, field_size: int) -> DealCard:
    embed = discord.Embed(title=label, description="x" * field_size)
    return DealCard(embed=embed, url=f"https://example.com/{label}", label=label)


def test_embed_text_size_counts_title_description_footer_and_fields():
    embed = discord.Embed(title="Title", description="Description")
    embed.add_field(name="Field", value="Value", inline=False)
    embed.set_footer(text="Footer")

    assert embed_text_size(embed) == len("TitleDescriptionFieldValueFooter")


def test_batch_cards_for_embed_limit_splits_large_auto_discovery_payloads():
    cards = [make_card(f"card-{idx}", SAFE_EMBED_MESSAGE_LIMIT // 2) for idx in range(3)]

    batches = batch_cards_for_embed_limit(cards, limit=SAFE_EMBED_MESSAGE_LIMIT)

    assert len(batches) == 3
    assert all(len(batch) == 1 for batch in batches)


def test_batch_cards_keeps_small_cards_together():
    cards = [make_card(f"card-{idx}", 100) for idx in range(3)]

    batches = batch_cards_for_embed_limit(cards, limit=SAFE_EMBED_MESSAGE_LIMIT)

    assert len(batches) == 1
    assert len(batches[0]) == 3
