from __future__ import annotations

from typing import Any

import discord


DISCORD_EMBED_MESSAGE_LIMIT = 6000
SAFE_EMBED_MESSAGE_LIMIT = 5200


def embed_text_size(embed: discord.Embed) -> int:
    data = embed.to_dict()
    total = 0
    total += len(str(data.get("title") or ""))
    total += len(str(data.get("description") or ""))
    footer = data.get("footer") or {}
    total += len(str(footer.get("text") or ""))
    author = data.get("author") or {}
    total += len(str(author.get("name") or ""))
    for field in data.get("fields") or []:
        total += len(str(field.get("name") or ""))
        total += len(str(field.get("value") or ""))
    return total


def batch_embeds_for_limit(embeds: list[discord.Embed], *, limit: int = SAFE_EMBED_MESSAGE_LIMIT) -> list[list[discord.Embed]]:
    batches: list[list[discord.Embed]] = []
    current: list[discord.Embed] = []
    current_size = 0

    for embed in embeds:
        size = embed_text_size(embed)
        if current and current_size + size > limit:
            batches.append(current)
            current = []
            current_size = 0
        current.append(embed)
        current_size += size

    if current:
        batches.append(current)
    return batches


def batch_cards_for_limit(cards: list[Any], *, limit: int = SAFE_EMBED_MESSAGE_LIMIT) -> list[list[Any]]:
    batches: list[list[Any]] = []
    current: list[Any] = []
    current_size = 0

    for card in cards:
        embed = getattr(card, "embed", None)
        size = embed_text_size(embed) if isinstance(embed, discord.Embed) else 0
        if current and current_size + size > limit:
            batches.append(current)
            current = []
            current_size = 0
        current.append(card)
        current_size += size

    if current:
        batches.append(current)
    return batches


async def send_summary_and_card_batches(
    interaction: discord.Interaction,
    *,
    summary: discord.Embed,
    cards: list[Any],
    view_factory=None,
    ephemeral: bool = True,
) -> None:
    """Send rich deal results without Discord's combined 6000-char embed error.

    Discord counts all embed text in a single message. SniperPlug cards can be
    proof-heavy, so the safe default is summary first, then cards in batches.
    """
    await interaction.followup.send(embed=summary, ephemeral=ephemeral)
    for batch in batch_cards_for_limit(cards):
        embeds = [card.embed for card in batch]
        view = view_factory(batch) if view_factory else None
        await interaction.followup.send(embeds=embeds, view=view, ephemeral=ephemeral)


async def send_summary_and_embeds(
    interaction: discord.Interaction,
    *,
    summary: discord.Embed,
    embeds: list[discord.Embed],
    view: discord.ui.View | None = None,
    ephemeral: bool = True,
) -> None:
    await interaction.followup.send(embed=summary, ephemeral=ephemeral)
    batches = batch_embeds_for_limit(embeds)
    for index, batch in enumerate(batches):
        await interaction.followup.send(embeds=batch, view=view if index == len(batches) - 1 else None, ephemeral=ephemeral)
