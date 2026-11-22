"""Regression tests for the two penny-menu failures of 2026-11-22.

Felix pressed both Home Depot penny options in Discord and both failed
(bot log, 2026-11-22 ~11:42 container time):

1. "My Home Depot hunts" with no saved hunts built ``view=None`` and
   passed it explicitly to ``followup.send`` — discord.py raises
   ``TypeError: expected view parameter to be of type View or
   LayoutView, not NoneType`` (home_depot_search.open_my_hunts).
2. The Home Depot search / penny-hunt / stock modals carried TextInput
   labels longer than Discord's 45-character limit, so Discord rejected
   the modal itself (400, error 50035, "Must be between 1 and 45 in
   length") and the press surfaced as "didn't respond in time".
"""

from __future__ import annotations

from types import SimpleNamespace

import discord
import pytest

from sniperplug.cogs import home_depot_search
from sniperplug.cogs.home_depot_search import HomeDepotSearchCog
from sniperplug.cogs.unified_menu import _MODAL_FIELDS, _MenuModal

DISCORD_LABEL_LIMIT = 45


def test_every_menu_modal_field_label_fits_discord_limit() -> None:
    too_long = [
        f"{key}.{field.key}: {field.label!r} ({len(field.label)} chars)"
        for key, fields in _MODAL_FIELDS.items()
        for field in fields
        if len(field.label) > DISCORD_LABEL_LIMIT
    ]
    assert too_long == []


def test_every_menu_modal_constructs_within_discord_limits() -> None:
    for key in _MODAL_FIELDS:
        modal = _MenuModal(SimpleNamespace(), key)
        assert len(modal.title) <= DISCORD_LABEL_LIMIT
        for child in modal.children:
            assert len(child.label) <= DISCORD_LABEL_LIMIT, (
                f"{key}: label {child.label!r} exceeds {DISCORD_LABEL_LIMIT}"
            )


class _FakeFollowup:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def send(self, **kwargs) -> None:
        self.calls.append(kwargs)


class _FakeResponse:
    async def defer(self, **kwargs) -> None:
        return None


def _fake_interaction() -> SimpleNamespace:
    return SimpleNamespace(
        response=_FakeResponse(),
        followup=_FakeFollowup(),
        guild_id=123,
        user=SimpleNamespace(id=42),
    )


def _fake_self() -> SimpleNamespace:
    return SimpleNamespace(bot=SimpleNamespace(db=object()))


@pytest.mark.asyncio
async def test_my_hunts_with_no_saved_hunts_sends_embed_without_a_view(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_build(db, *, guild_id, user_id):
        return discord.Embed(title="My Home Depot hunts"), []

    monkeypatch.setattr(home_depot_search, "build_my_hunts_embed", fake_build)
    interaction = _fake_interaction()

    await HomeDepotSearchCog.open_my_hunts(_fake_self(), interaction)

    assert len(interaction.followup.calls) == 1
    kwargs = interaction.followup.calls[0]
    assert "embed" in kwargs
    # discord.py raises TypeError when a send is handed view=None
    # explicitly; with nothing to attach the kwarg must be absent.
    assert "view" not in kwargs


@pytest.mark.asyncio
async def test_my_hunts_with_saved_hunts_attaches_the_stop_view(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hunt = SimpleNamespace(hunt_id=7, query="drill")

    async def fake_build(db, *, guild_id, user_id):
        return discord.Embed(title="My Home Depot hunts"), [hunt]

    # Stand-in for the real stop view: what this test pins is the branch
    # (saved hunts -> a stop view is built from them and attached), not
    # discord.py's View machinery. Constructing a real View under a
    # running loop here left loop-bound state behind that made a later,
    # unrelated database test fail in CI (2026-11-22 bisect, PR #376).
    built: list[SimpleNamespace] = []

    class _FakeStopView:
        def __init__(self, db, owner_id, hunts):
            self.db = db
            self.owner_id = owner_id
            self.hunts = hunts
            built.append(self)

    monkeypatch.setattr(home_depot_search, "build_my_hunts_embed", fake_build)
    monkeypatch.setattr(home_depot_search, "HdHuntStopView", _FakeStopView)
    interaction = _fake_interaction()

    await HomeDepotSearchCog.open_my_hunts(_fake_self(), interaction)

    assert len(interaction.followup.calls) == 1
    view = interaction.followup.calls[0].get("view")
    assert isinstance(view, _FakeStopView)
    assert view.hunts == [hunt]
    assert view.owner_id == 42
