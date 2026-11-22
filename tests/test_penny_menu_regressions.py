"""Bisect variant D3: async my-hunts tests only."""
from __future__ import annotations

from types import SimpleNamespace

import discord
import pytest

from sniperplug.cogs import home_depot_search
from sniperplug.cogs.home_depot_search import HdHuntStopView, HomeDepotSearchCog
from sniperplug.cogs.unified_menu import _MODAL_FIELDS, _MenuModal


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
    assert "view" not in kwargs
