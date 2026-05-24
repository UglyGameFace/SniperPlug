import discord

from sniperplug.services.manual_posting_explainer import add_public_posting_field
from sniperplug.services.public_deal_posts import PublicPostResult


def test_manual_posting_field_includes_skip_reason():
    embed = discord.Embed(title="summary")
    result = PublicPostResult(attempted=5, skipped_not_alertable=5, cached_active=5)

    add_public_posting_field(embed, result)

    data = embed.to_dict()
    field = data["fields"][0]
    assert field["name"] == "📣 Public posting"
    assert "Posted: **0**" in field["value"]
    assert "proof was too weak" in field["value"]


def test_manual_posting_field_skips_empty_result():
    embed = discord.Embed(title="summary")

    add_public_posting_field(embed, PublicPostResult())

    assert "fields" not in embed.to_dict()
