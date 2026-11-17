"""Reconciliation tests: leftovers from retiring /walmart cash (audit item 2).

/walmart cash was retired because no Cash offer feed exists in the repo.
Two pieces of dead surface still advertised it afterwards: the
"Walmart Cash Offers" button on both Hunt preset views (its callback ran a
cash search that could only render "feed unavailable") and the
"/walmart cash" entry in the dead SettingsDashboardCog's SPECIALIST_COMMANDS
set. Both are gone; the strict-proof cash machinery underneath
(_send_walmart_cash_search, find_walmart_cash_offer, the pipeline) is
deliberately kept — it still validates Cash proof on the normal deal stream.
"""

from __future__ import annotations

import discord


def test_hunt_views_have_no_walmart_cash_button() -> None:
    from sniperplug.cogs.deal_scanner import HuntPresetMenuView
    from sniperplug.cogs.verified_deal_scanner import VerifiedHuntPresetMenuView

    for view in (HuntPresetMenuView(), VerifiedHuntPresetMenuView()):
        for child in view.children:
            assert not (
                isinstance(child, discord.ui.Button)
                and child.custom_id == "hunt:walmart_cash_offers"
            ), f"{type(view).__name__} still offers a Walmart Cash button"
            assert getattr(child, "label", None) != "Walmart Cash Offers"


def test_settings_dashboard_no_longer_lists_walmart_cash() -> None:
    from sniperplug.cogs.settings_dashboard import SPECIALIST_COMMANDS

    assert "/walmart cash" not in SPECIALIST_COMMANDS
