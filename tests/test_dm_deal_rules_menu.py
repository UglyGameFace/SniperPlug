from __future__ import annotations

import asyncio
from types import SimpleNamespace

import discord

import sniperplug.cogs.dm_deal_preferences_view as menu_module
from sniperplug.cogs.dm_deal_preferences_view import (
    SCREEN_COUNT,
    SCREEN_HOME,
    SCREEN_QUALITY,
    SCREEN_WHAT,
    DmDealPreferencesView,
)
from sniperplug.services.dm_deal_alerts import (
    DmDealAlertPreference,
    UNLIMITED_MAX_ALERTS_PER_DAY,
)


def _view(preference: DmDealAlertPreference | None = None) -> DmDealPreferencesView:
    return DmDealPreferencesView(
        bot=SimpleNamespace(db=None),
        user_id=1,
        preference=preference or DmDealAlertPreference(user_id=1, enabled=True),
    )


def _buttons(view: DmDealPreferencesView) -> dict[str, discord.ui.Button]:
    return {
        item.label: item
        for item in view.children
        if isinstance(item, discord.ui.Button) and item.label
    }


class _FakeResponse:
    def __init__(self) -> None:
        self.embed = None
        self.view = None

    async def edit_message(self, *, embed, view) -> None:
        self.embed = embed
        self.view = view


def _interaction() -> SimpleNamespace:
    return SimpleNamespace(response=_FakeResponse())


def test_home_screen_is_three_questions_and_a_summary() -> None:
    view = _view(
        DmDealAlertPreference(
            user_id=1,
            enabled=True,
            min_discount=50,
            max_alerts_per_day=UNLIMITED_MAX_ALERTS_PER_DAY,
            categories=("dm_route:price_glitch", "dm_route:hot_deals"),
        )
    )

    labels = _buttons(view)
    assert "What DMs you" in labels
    assert "How good it has to be" in labels
    assert "How many a day" in labels
    embed = view.build_embed()
    assert embed.description == (
        "You'll get DMs for price mistakes and big markdowns · "
        "50% off or better · no daily limit."
    )


def test_what_screen_has_six_plain_route_toggles() -> None:
    view = _view()
    view.screen = SCREEN_WHAT
    view._rebuild_items()

    labels = _buttons(view)
    for label in (
        "Price mistakes",
        "Big markdowns",
        "Coupon stacks",
        "Business & bulk",
        "Might not work for everyone",
        "Everything else",
    ):
        assert label in labels
    # The profit stepper only appears once price mistakes are picked.
    assert "Worth $25+" not in labels


def test_route_toggle_and_profit_stepper_update_the_draft() -> None:
    view = _view()
    view.screen = SCREEN_WHAT
    view._rebuild_items()

    asyncio.run(_buttons(view)["Price mistakes"].callback(_interaction()))
    assert view.selected_routes == {"price_glitch"}
    assert view.dirty is True

    view._rebuild_items()
    asyncio.run(_buttons(view)["Worth $25+"].callback(_interaction()))
    assert view.flip_floor_cents == 2500


def test_quality_presets_update_discount_and_max_price() -> None:
    view = _view()
    view.screen = SCREEN_QUALITY
    view._rebuild_items()

    asyncio.run(_buttons(view)["70%+"].callback(_interaction()))
    assert view.dm_min_discount == 70
    asyncio.run(_buttons(view)["$250"].callback(_interaction()))
    assert view.dm_max_price_cents == 25_000
    asyncio.run(_buttons(view)["No limit"].callback(_interaction()))
    assert view.dm_max_price_cents is None
    assert view.dirty is True


def test_count_presets_update_the_daily_cap() -> None:
    view = _view()
    view.screen = SCREEN_COUNT
    view._rebuild_items()

    asyncio.run(_buttons(view)["10 a day"].callback(_interaction()))
    assert view.dm_daily_cap == 10
    asyncio.run(_buttons(view)["No limit"].callback(_interaction()))
    assert view.dm_daily_cap == UNLIMITED_MAX_ALERTS_PER_DAY


def test_save_persists_the_simple_draft(monkeypatch) -> None:
    async def fake_save(_db, preference):
        return preference.normalized()

    monkeypatch.setattr(
        menu_module, "save_dm_deal_alert_preference", fake_save
    )
    view = _view()
    view.selected_routes = {"price_glitch"}
    view.flip_floor_cents = 2500
    view.dm_min_discount = 50
    view.dm_daily_cap = 10
    view.dirty = True

    asyncio.run(view._save(_interaction()))

    saved = view.preference
    assert saved.min_discount == 50
    assert saved.max_alerts_per_day == 10
    assert "dm_route:price_glitch" in saved.categories
    assert "flip_profit:2500" in saved.categories
    assert view.screen == SCREEN_HOME
    assert view.dirty is False


def test_alerts_toggle_is_a_draft_until_saved() -> None:
    view = _view(DmDealAlertPreference(user_id=1, enabled=False))
    assert view.enabled is False

    asyncio.run(_buttons(view)["Alerts: OFF"].callback(_interaction()))

    assert view.enabled is True
    assert view.dirty is True
