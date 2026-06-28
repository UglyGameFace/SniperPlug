from __future__ import annotations

import discord

from sniperplug.cogs import deal_scanner
from sniperplug.cogs.deal_scanner import DealCard
from sniperplug.services.autoscan_no_post_report import build_autoscan_no_post_report
from sniperplug.services.autoscan_route_policy import (
    PRIVATE_WALMART_CASH_ROUTES,
    is_private_promo_route,
    public_autoscan_hunt_presets,
    public_autoscan_queries,
)
from sniperplug.services.open_box_autoscan_routes import OPEN_BOX_AUTOSCAN_KEY
from sniperplug.services.public_deal_quality import LANE_WALMART_CASH, select_public_deal_candidates
from sniperplug.services.walmart_cash_offers import DEFAULT_CASH_QUERIES


def test_public_autoscan_filters_private_cash_routes():
    filtered = public_autoscan_queries((
        "household rollback",
        "walmart cash eligible",
        "walmart cash offers",
        "onepay cash rewards",
        "cash back walmart",
        "trash bags rollback",
    ))

    assert "household rollback" in filtered
    assert "trash bags rollback" in filtered
    assert "walmart cash eligible" not in filtered
    assert "walmart cash offers" not in filtered
    assert "onepay cash rewards" not in filtered
    assert "cash back walmart" not in filtered


def test_public_hunt_presets_do_not_keep_walmart_cash_routes():
    public_presets = public_autoscan_hunt_presets()
    combined = "\n".join(query.lower() for preset in public_presets.values() for query in preset.queries)

    assert "walmart cash eligible" not in combined
    assert "walmart cash offers" not in combined
    assert "onepay cash rewards" not in combined
    assert "cash back walmart" not in combined


def test_public_autoscan_route_policy_does_not_mutate_legacy_hunt_buttons():
    before = dict(deal_scanner.HUNT_PRESETS)
    public_autoscan_hunt_presets()

    assert dict(deal_scanner.HUNT_PRESETS) == before
    assert "glitch" in deal_scanner.HUNT_PRESETS
    assert "tech" in deal_scanner.HUNT_PRESETS
    assert "essentials" in deal_scanner.HUNT_PRESETS


def test_cash_finder_keeps_private_walmart_cash_routes():
    cash_queries = "\n".join(DEFAULT_CASH_QUERIES).lower()
    policy_queries = "\n".join(PRIVATE_WALMART_CASH_ROUTES).lower()

    assert "walmart cash eligible" in cash_queries
    assert "detergent walmart cash" in cash_queries
    assert "walmart cash eligible" in policy_queries
    assert is_private_promo_route("walmart cash eligible")


def test_open_box_route_is_native_public_autoscan_preset():
    public_presets = public_autoscan_hunt_presets()

    assert OPEN_BOX_AUTOSCAN_KEY in public_presets
    routes = "\n".join(public_presets[OPEN_BOX_AUTOSCAN_KEY].queries).lower()
    assert "open box" in routes
    assert "restored" in routes
    assert "refurbished" in routes


def test_autoscan_no_post_report_shows_private_diagnostics():
    text = build_autoscan_no_post_report(
        products_checked=1979,
        verified_cards_found=0,
        public_ready_cards=0,
        weak_reference_ignored=961,
        missing_trusted_reference=238,
        review_scout_leads=25,
        exact_match_rescues=503,
        strongest_review_candidates_kept=3,
    )

    assert "products checked: **1979**" in text
    assert "verified cards found: **0**" in text
    assert "public-ready cards found: **0**" in text
    assert "weak reference ignored: **961**" in text
    assert "missing trusted reference: **238**" in text
    assert "review/scout leads: **25**" in text
    assert "exact-match rescues: **503**" in text
    assert "strongest review candidates kept: **3**" in text


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
