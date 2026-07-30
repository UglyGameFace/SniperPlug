from pathlib import Path

import discord

from sniperplug.services.embed_delivery import (
    SAFE_EMBED_MESSAGE_LIMIT,
    batch_embeds_for_limit,
    embed_text_size,
    sanitize_embed,
)


def _rich_embed(index: int, size: int = 1800) -> discord.Embed:
    embed = discord.Embed(title=f"Deal {index}", description="x" * size)
    embed.add_field(name="Proof", value="y" * 900, inline=False)
    return embed


def test_rich_embeds_are_split_below_combined_message_limit():
    batches = batch_embeds_for_limit([_rich_embed(i) for i in range(5)])
    assert len(batches) >= 3
    assert all(sum(embed_text_size(embed) for embed in batch) <= SAFE_EMBED_MESSAGE_LIMIT for batch in batches)
    assert all(len(batch) <= 10 for batch in batches)


def test_oversized_single_embed_is_sanitized_before_batching():
    embed = discord.Embed(title="t" * 400, description="d" * 9000)
    for index in range(30):
        embed.add_field(name=f"field-{index}" * 40, value="v" * 3000, inline=False)
    safe = sanitize_embed(embed)
    assert embed_text_size(safe) <= 5600
    assert len(safe.fields) <= 25
    assert len(safe.title or "") <= 256
    assert len(safe.description or "") <= 4096


def test_all_rich_scan_routes_use_shared_batch_delivery():
    deal_scanner = Path("sniperplug/cogs/deal_scanner.py").read_text()
    home_depot = Path("sniperplug/cogs/home_depot_search.py").read_text()
    open_box = Path("sniperplug/cogs/open_box_deals.py").read_text()

    assert "send_summary_and_card_batches" in deal_scanner
    assert deal_scanner.count("send_summary_and_card_batches(") >= 6
    assert "embeds=[summary] + [card.embed for card in shown_cards]" not in deal_scanner
    assert "embeds=[summary] + batch.embeds[:5]" not in home_depot
    assert "send_summary_and_embeds(" in home_depot
    assert "embeds=[summary] + [card.embed for card in cards[:5]]" not in open_box
    assert "send_summary_and_card_batches(" in open_box
