from __future__ import annotations

import discord

from sniperplug.services.embed_delivery import embed_text_size, sanitize_embed


def test_sanitize_embed_trims_field_values_under_discord_limit() -> None:
    embed = discord.Embed(title="x" * 400, description="d" * 5000)
    embed.add_field(name="n" * 400, value="v" * 2000, inline=False)

    safe = sanitize_embed(embed)
    data = safe.to_dict()

    assert len(data["title"]) <= 256
    assert len(data["description"]) <= 4096
    assert len(data["fields"][0]["name"]) <= 256
    assert len(data["fields"][0]["value"]) <= 1024
    assert embed_text_size(safe) <= 5600


def test_sanitize_embed_limits_field_count() -> None:
    embed = discord.Embed(title="many fields")
    for index in range(40):
        embed.add_field(name=f"field {index}", value="ok", inline=False)

    safe = sanitize_embed(embed)
    assert len(safe.to_dict().get("fields", [])) <= 25
