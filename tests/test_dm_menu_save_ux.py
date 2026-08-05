from __future__ import annotations

import asyncio
from types import SimpleNamespace

import discord

import sniperplug.cogs.dm_deal_preferences_view as menu_module
from sniperplug.cogs.dm_deal_preferences_view import DmDealPreferencesView
from sniperplug.services.dm_deal_alerts import DmDealAlertPreference


def _button(view: DmDealPreferencesView, label: str) -> discord.ui.Button:
    for item in view.children:
        if isinstance(item, discord.ui.Button) and item.label == label:
            return item
    raise AssertionError(f"button not found: {label}")


def test_menu_tracks_saved_and_unsaved_button_states() -> None:
    view = DmDealPreferencesView(
        bot=SimpleNamespace(db=None),
        user_id=1,
        preference=DmDealAlertPreference(user_id=1),
    )

    assert _button(view, "Saved").disabled is True
    assert _button(view, "Close").disabled is False

    view.dirty = True
    view._rebuild_items()

    assert _button(view, "Save changes").disabled is False
    assert _button(view, "Discard & close").disabled is False


def test_save_keeps_menu_open_and_interactive(monkeypatch) -> None:
    async def fake_save(_db, preference):
        return preference

    class FakeResponse:
        def __init__(self) -> None:
            self.embed = None
            self.view = None

        async def edit_message(self, *, embed, view) -> None:
            self.embed = embed
            self.view = view

    monkeypatch.setattr(
        menu_module,
        "save_dm_deal_alert_preference",
        fake_save,
    )

    view = DmDealPreferencesView(
        bot=SimpleNamespace(db=None),
        user_id=1,
        preference=DmDealAlertPreference(user_id=1),
    )
    view.favorite_categories.add("gpus")
    view.dirty = True
    view._rebuild_items()

    response = FakeResponse()
    interaction = SimpleNamespace(response=response)
    asyncio.run(view._save(interaction))

    assert view.saved is True
    assert view.dirty is False
    assert view.is_finished() is False
    assert response.view is view
    assert "still open" in (response.embed.description or "")
    assert _button(view, "Saved").disabled is True
    assert _button(view, "Close").disabled is False
    assert any(
        not item.disabled
        for item in view.children
        if not (isinstance(item, discord.ui.Button) and item.label == "Saved")
    )


def test_only_explicit_close_disables_and_stops_menu() -> None:
    source = menu_module.DmDealPreferencesView._save.__code__.co_names
    assert "stop" not in source

    close_source = menu_module.DmDealPreferencesView._close.__code__.co_names
    assert "stop" in close_source
