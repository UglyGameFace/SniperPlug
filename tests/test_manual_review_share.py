import discord

from sniperplug.cogs.deal_scanner import DealCard
from sniperplug.services.manual_review_share import ManualReviewPageButton, ManualReviewShareView, ManualShareButton


def test_manual_review_share_view_adds_staff_post_button_for_cards():
    card = DealCard(
        embed=discord.Embed(title="Raw price lead"),
        url="https://www.walmart.com/ip/1",
        label="Dolce Gabbana",
    )

    view = ManualReviewShareView([card])

    assert len(view.children) == 1
    assert isinstance(view.children[0], ManualShareButton)
    assert view.children[0].label == "Post 1"


def test_manual_review_share_view_paginates_more_than_one_page():
    cards = [
        DealCard(
            embed=discord.Embed(title=f"Raw price lead {index}"),
            url=f"https://www.walmart.com/ip/{index}",
            label=f"Lead {index}",
        )
        for index in range(5)
    ]

    view = ManualReviewShareView(cards)

    assert view.page_count == 2
    assert len(view.page_cards()) == 3
    assert any(isinstance(child, ManualReviewPageButton) for child in view.children)
    assert "Page **1/2**" in view.content()
