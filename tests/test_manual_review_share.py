import discord

from sniperplug.cogs.deal_scanner import DealCard
from sniperplug.services.manual_review_share import ManualReviewShareView


def test_manual_review_share_view_adds_staff_select_for_cards():
    card = DealCard(
        embed=discord.Embed(title="Raw price lead"),
        url="https://www.walmart.com/ip/1",
        label="Dolce Gabbana",
    )

    view = ManualReviewShareView([card])

    assert len(view.children) == 1
    assert "manually post" in view.children[0].placeholder.lower()
