import discord

from sniperplug.services.embed_delivery import SAFE_EMBED_MESSAGE_LIMIT, embed_text_size
from sniperplug.services.embed_delivery_patch import should_split_embeds


def make_embed(size: int) -> discord.Embed:
    embed = discord.Embed(title="x")
    embed.description = "a" * size
    return embed


def test_should_split_embeds_when_combined_payload_is_too_large():
    embeds = [make_embed(SAFE_EMBED_MESSAGE_LIMIT // 2), make_embed(SAFE_EMBED_MESSAGE_LIMIT // 2 + 100)]

    assert sum(embed_text_size(embed) for embed in embeds) > SAFE_EMBED_MESSAGE_LIMIT
    assert should_split_embeds(embeds) is True


def test_should_not_split_single_large_embed_here():
    # Single-embed oversize should be handled by renderer compaction, not by
    # splitting one invalid embed into another invalid embed.
    assert should_split_embeds([make_embed(SAFE_EMBED_MESSAGE_LIMIT + 100)]) is False


def test_should_not_split_small_batches():
    embeds = [make_embed(50), make_embed(50)]

    assert should_split_embeds(embeds) is False
