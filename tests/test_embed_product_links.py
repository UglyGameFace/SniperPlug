import discord

from sniperplug.cogs.deal_scanner import product_link_block
from sniperplug.cogs.home_depot_search import home_depot_link_block
from sniperplug.models.candidate import SourceCandidate
from sniperplug.services.safe_links import LinkChoice


def test_product_link_block_renders_inline_links():
    rendered = product_link_block(
        (
            LinkChoice("App/Web", "https://www.walmart.com/ip/123"),
            LinkChoice("Browser Search", "https://www.google.com/search?q=walmart+123"),
        ),
        fallback_url="https://www.walmart.com/ip/123",
    )

    assert "[App/Web](https://www.walmart.com/ip/123)" in rendered
    assert "[Browser Search](https://www.google.com/search?q=walmart+123)" in rendered
    assert " • " in rendered


def test_product_link_block_has_fallback():
    assert product_link_block((), fallback_url="https://example.com") == "[App/Web](https://example.com)"


def test_home_depot_link_block_renders_product_links():
    candidate = SourceCandidate(
        source_key="home_depot_serpapi",
        retailer="Home Depot",
        title="Milwaukee Drill",
        product_url="https://www.homedepot.com/p/123",
        product_id="123",
        sku="123",
    )

    rendered = home_depot_link_block(candidate)

    assert "[" in rendered
    assert "](https://" in rendered
