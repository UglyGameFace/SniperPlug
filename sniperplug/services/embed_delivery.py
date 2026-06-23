from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import discord


DISCORD_EMBED_MESSAGE_LIMIT = 6000
SAFE_EMBED_MESSAGE_LIMIT = 5200
SAFE_SINGLE_EMBED_LIMIT = 5600
MAX_EMBEDS_PER_MESSAGE = 10
MAX_FIELDS_PER_EMBED = 25
MAX_TITLE = 256
MAX_DESCRIPTION = 4096
MAX_FIELD_NAME = 256
MAX_FIELD_VALUE = 1024
MAX_FOOTER = 2048
MAX_AUTHOR = 256
TRUNCATION_MARK = "…"


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


def sanitize_embed(embed: discord.Embed, *, total_limit: int = SAFE_SINGLE_EMBED_LIMIT) -> discord.Embed:
    """Return a Discord-safe copy of an embed.

    This is the hard guardrail for Discord error 50035. It enforces field,
    title, description, footer, field-count, and total embed text limits before
    anything is sent.
    """
    data = embed.to_dict()

    if data.get("title") is not None:
        data["title"] = trim_text(data.get("title"), MAX_TITLE)
    if data.get("description") is not None:
        data["description"] = trim_text(data.get("description"), MAX_DESCRIPTION)

    footer = data.get("footer")
    if isinstance(footer, dict) and footer.get("text") is not None:
        footer["text"] = trim_text(footer.get("text"), MAX_FOOTER)

    author = data.get("author")
    if isinstance(author, dict) and author.get("name") is not None:
        author["name"] = trim_text(author.get("name"), MAX_AUTHOR)

    fields = data.get("fields") or []
    safe_fields: list[dict[str, Any]] = []
    for field in fields[:MAX_FIELDS_PER_EMBED]:
        if not isinstance(field, dict):
            continue
        name = trim_text(field.get("name") or "Field", MAX_FIELD_NAME)
        value = trim_text(field.get("value") or "—", MAX_FIELD_VALUE)
        safe_fields.append({"name": name or "Field", "value": value or "—", "inline": bool(field.get("inline", False))})
    data["fields"] = safe_fields

    safe = discord.Embed.from_dict(data)
    while embed_text_size(safe) > total_limit:
        data = safe.to_dict()
        if _shrink_last_field(data, embed_text_size(safe) - total_limit + 32):
            safe = discord.Embed.from_dict(data)
            continue
        if data.get("description"):
            excess = embed_text_size(safe) - total_limit + 32
            data["description"] = trim_text(data.get("description"), max(0, len(str(data.get("description"))) - excess))
            safe = discord.Embed.from_dict(data)
            continue
        if data.get("fields"):
            data["fields"] = data["fields"][:-1]
            safe = discord.Embed.from_dict(data)
            continue
        break
    return safe


def sanitize_embeds(embeds: Any) -> Any:
    if embeds is None:
        return None
    if isinstance(embeds, discord.Embed):
        return sanitize_embed(embeds)
    if isinstance(embeds, list | tuple):
        return [sanitize_embed(embed) if isinstance(embed, discord.Embed) else embed for embed in embeds[:MAX_EMBEDS_PER_MESSAGE]]
    return embeds


def trim_text(value: Any, limit: int) -> str:
    text = str(value or "")
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    if limit <= len(TRUNCATION_MARK):
        return TRUNCATION_MARK[:limit]
    return text[: limit - len(TRUNCATION_MARK)].rstrip() + TRUNCATION_MARK


def _shrink_last_field(data: dict[str, Any], shrink_by: int) -> bool:
    fields = data.get("fields") or []
    for field in reversed(fields):
        value = str(field.get("value") or "")
        if len(value) > 80:
            field["value"] = trim_text(value, max(32, len(value) - shrink_by))
            return True
    return False



def should_split_embeds(embeds: Any) -> bool:
    if not embeds or not isinstance(embeds, Sequence):
        return False
    if len(embeds) <= 1:
        return False
    if not all(isinstance(embed, discord.Embed) for embed in embeds):
        return False
    total = sum(embed_text_size(embed) for embed in embeds)
    return total > SAFE_EMBED_MESSAGE_LIMIT

def batch_embeds_for_limit(embeds: list[discord.Embed], *, limit: int = SAFE_EMBED_MESSAGE_LIMIT) -> list[list[discord.Embed]]:
    safe_embeds = [sanitize_embed(embed) for embed in embeds if isinstance(embed, discord.Embed)]
    batches: list[list[discord.Embed]] = []
    current: list[discord.Embed] = []
    current_size = 0

    for embed in safe_embeds[:MAX_EMBEDS_PER_MESSAGE * 10]:
        size = embed_text_size(embed)
        if current and (current_size + size > limit or len(current) >= MAX_EMBEDS_PER_MESSAGE):
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
        if isinstance(embed, discord.Embed):
            card.embed = sanitize_embed(embed)
            size = embed_text_size(card.embed)
        else:
            size = 0
        if current and (current_size + size > limit or len(current) >= MAX_EMBEDS_PER_MESSAGE):
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
    """Send rich deal results without Discord's combined/field-limit embed errors."""
    await interaction.followup.send(embed=sanitize_embed(summary), ephemeral=ephemeral)
    batches = batch_cards_for_limit(cards)
    for index, batch in enumerate(batches):
        embeds = [sanitize_embed(card.embed) for card in batch]
        view = view_factory(batch) if view_factory and index == len(batches) - 1 else None
        await interaction.followup.send(embeds=embeds, view=view, ephemeral=ephemeral)


async def send_summary_and_embeds(
    interaction: discord.Interaction,
    *,
    summary: discord.Embed,
    embeds: list[discord.Embed],
    view: discord.ui.View | None = None,
    ephemeral: bool = True,
) -> None:
    await interaction.followup.send(embed=sanitize_embed(summary), ephemeral=ephemeral)
    batches = batch_embeds_for_limit(embeds)
    for index, batch in enumerate(batches):
        await interaction.followup.send(embeds=batch, view=view if index == len(batches) - 1 else None, ephemeral=ephemeral)
