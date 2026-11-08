from __future__ import annotations

import discord

from sniperplug.cogs.deal_scanner import DealCard
from sniperplug.services.public_deal_quality import LANE_WALMART_CASH, select_public_deal_candidates

# NOTE (C6): the route-policy / no-post-report tests that lived here were
# deleted with their modules (sniperplug/services/autoscan_route_policy.py
# and autoscan_no_post_report.py — zero runtime importers). Only the live
# public-quality lane test remains.


def test_walmart_cash_lane_stays_out_of_public_posting():
    card = DealCard(
        embed=discord.Embed(title="Cash-only detergent"),
        url="https://www.walmart.com/ip/123",
        label="Cash-only detergent",
        score=100,
        discount=90,
        deal_lane=LANE_WALMART_CASH,
        api_current_price=9.99,
        api_reference_price=99.99,
        direct_product_url="https://www.walmart.com/ip/123",
    )

    assert select_public_deal_candidates([card], source_label="autoscan:walmart", min_discount=50) == []
