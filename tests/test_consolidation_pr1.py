"""Consolidation PR 1 (2026-11-19): copy pass + three functional fixes.

Pins, from the approved consolidation plan:
(a) the /menu hub's Check button uses the same gate as /walmart check
    (Deal Finder role OR Manage Server) with the same error text —
    plan §1.10 / §3.3 (the hub previously demanded Manage Server only);
(b) the menu wishlist modal's optional price/size fields reach the
    shipped /wishlist add handler, empty meaning no guard —
    plan §3.3.4;
(d) the rewritten home screen carries the plan's status line and
    button set — plan §3.3 HOME.
"""

from __future__ import annotations

from types import SimpleNamespace

import discord
import pytest

from sniperplug.cogs.unified_menu import (
    UnifiedCheckButton,
    UnifiedMenuCog,
    UnifiedMenuView,
    _MenuModal,
    build_unified_menu_embed,
)
from sniperplug.cogs.walmart_command import DEAL_FINDER_ROLE_ID, WalmartCommandCog
from sniperplug.services.menu_dashboard_state import MenuDashboardState

# -- fakes ---------------------------------------------------------------

class _FakeResponse:
    def __init__(self) -> None:
        self.modal = None
        self.sent: list[tuple[tuple, dict]] = []
        self._done = False

    def is_done(self) -> bool:
        return self._done

    async def send_modal(self, modal) -> None:
        self.modal = modal

    async def send_message(self, *args, **kwargs) -> None:
        self.sent.append((args, kwargs))


class _FakeFollowup:
    def __init__(self) -> None:
        self.sent: list[tuple[tuple, dict]] = []

    async def send(self, *args, **kwargs) -> None:
        self.sent.append((args, kwargs))


class _FakeInteraction:
    def __init__(self, *, manage_guild: bool = False, roles=()) -> None:
        self.user = SimpleNamespace(
            id=123,
            guild_permissions=SimpleNamespace(manage_guild=manage_guild),
            roles=list(roles),
        )
        self.guild_id = 456
        self.guild = SimpleNamespace(id=456)
        self.channel = None
        self.message = None
        self.response = _FakeResponse()
        self.followup = _FakeFollowup()


def _hub_cog() -> UnifiedMenuCog:
    walmart_cog = WalmartCommandCog(bot=None)
    bot = SimpleNamespace(
        get_cog=lambda name: walmart_cog if name == "WalmartCommandCog" else None
    )
    return UnifiedMenuCog(bot=bot)


# -- (a) the hub check gate ------------------------------------------------

@pytest.mark.asyncio
async def test_hub_check_button_admits_deal_finder_only_user() -> None:
    cog = _hub_cog()
    button = UnifiedCheckButton(cog)
    interaction = _FakeInteraction(
        manage_guild=False,
        roles=[SimpleNamespace(id=DEAL_FINDER_ROLE_ID)],
    )
    await button.callback(interaction)
    assert interaction.response.modal is not None
    assert interaction.response.sent == []


@pytest.mark.asyncio
async def test_hub_check_button_admits_manage_server_user() -> None:
    cog = _hub_cog()
    button = UnifiedCheckButton(cog)
    interaction = _FakeInteraction(manage_guild=True, roles=[])
    await button.callback(interaction)
    assert interaction.response.modal is not None
    assert interaction.response.sent == []


@pytest.mark.asyncio
async def test_hub_check_button_refuses_plain_member_with_check_message() -> None:
    cog = _hub_cog()
    button = UnifiedCheckButton(cog)
    interaction = _FakeInteraction(manage_guild=False, roles=[])
    await button.callback(interaction)
    assert interaction.response.modal is None
    assert len(interaction.response.sent) == 1
    message = str(interaction.response.sent[0])
    # The same words /walmart check and the Walmart menu use.
    assert "You need the 🔍 Deal Finder role or Manage Server to check deals." in message
    # The old wrong message (Manage Server only) is gone.
    assert "to run direct deal checks" not in message


# -- (b) the wishlist modal's price/size fields -----------------------------

def _wishlist_cog(calls: list) -> UnifiedMenuCog:
    async def add(interaction, *args, **kwargs):
        calls.append((args, kwargs))

    bot = SimpleNamespace(
        get_cog=lambda name: SimpleNamespace(add=add) if name == "WishlistCog" else None
    )
    return UnifiedMenuCog(bot=bot)


def test_wishlist_modal_has_item_price_and_size_fields() -> None:
    modal = _MenuModal(_wishlist_cog([]), "wishlist-add")
    labels = [child.label for child in modal.children]
    assert labels == ["Item", "Only tell me at or under this price", "Size"]
    required = [child.required for child in modal.children]
    assert required == [True, False, False]


@pytest.mark.asyncio
async def test_wishlist_modal_passes_price_and_size_to_handler() -> None:
    calls: list = []
    cog = _wishlist_cog(calls)
    modal = _MenuModal(cog, "wishlist-add")
    modal.children[0]._value = "nike shoes"
    modal.children[1]._value = "150"
    modal.children[2]._value = "10.5"
    await modal.on_submit(_FakeInteraction())
    assert len(calls) == 1
    _, kwargs = calls[0]
    assert kwargs["item"] == "nike shoes"
    assert kwargs["target_price"] == 150.0
    assert isinstance(kwargs["target_price"], float)
    assert kwargs["size"] == "10.5"


@pytest.mark.asyncio
async def test_wishlist_modal_parses_dollar_price_and_blank_means_no_guard() -> None:
    calls: list = []
    cog = _wishlist_cog(calls)
    modal = _MenuModal(cog, "wishlist-add")
    modal.children[0]._value = "dyson airwrap"
    modal.children[1]._value = "$1,250.50"
    await modal.on_submit(_FakeInteraction())
    _, kwargs = calls[0]
    assert kwargs["target_price"] == 1250.50
    assert kwargs["size"] is None

    calls.clear()
    modal = _MenuModal(cog, "wishlist-add")
    modal.children[0]._value = "lego"
    await modal.on_submit(_FakeInteraction())
    _, kwargs = calls[0]
    assert kwargs == {"item": "lego", "target_price": None, "size": None}


@pytest.mark.asyncio
async def test_wishlist_modal_rejects_non_numeric_price() -> None:
    calls: list = []
    cog = _wishlist_cog(calls)
    modal = _MenuModal(cog, "wishlist-add")
    modal.children[0]._value = "iphone 11"
    modal.children[1]._value = "cheap"
    interaction = _FakeInteraction()
    await modal.on_submit(interaction)
    assert calls == []
    assert interaction.response.sent
    assert "doesn't look like a number" in str(interaction.response.sent[0])


# -- (d) the home screen ------------------------------------------------------

def _snapshot(**overrides) -> MenuDashboardState:
    values = {
        "state_available": True,
        "provider_status": "READY",
        "provider_message": "Ready.",
        "threshold_percent": 40,
        "extreme_enabled": True,
        "extreme_recent": 3,
        "delivery_ready": True,
        "delivery_summary": "Walmart delivery ready.",
        "dm_enabled": True,
        "dm_threshold": 20,
        "dm_daily_cap": 5,
        "wishlist_count": 7,
        "wishlist_cap": 20,
        "active_deals": [],
        "active_deal_count": 12,
        "deals_today": 4,
    }
    values.update(overrides)
    return MenuDashboardState(**values)


def test_home_screen_status_line_and_buttons_match_the_plan() -> None:
    embed = build_unified_menu_embed(_snapshot())
    assert (embed.title or "") == "SniperPlug"
    assert "Find deals. Check links. Get alerts." in (embed.description or "")
    assert "🟢 Working · 4 deals today · Your alerts: **ON**" in (embed.description or "")
    view = UnifiedMenuView(UnifiedMenuCog(bot=None))
    buttons = [c for c in view.children if isinstance(c, discord.ui.Button)]
    assert [b.label for b in buttons] == [
        "Find deals",
        "Browse",
        "Add to wishlist",
        "Check a link",
    ]
    selects = [c for c in view.children if isinstance(c, discord.ui.Select)]
    assert len(selects) == 1
    assert selects[0].placeholder == "More…"


def test_home_screen_status_line_is_truthful_when_not_posting() -> None:
    embed = build_unified_menu_embed(_snapshot(delivery_ready=False))
    assert "🟠 On — not posting here yet" in (embed.description or "")
    embed = build_unified_menu_embed(_snapshot(provider_status="ERROR"))
    assert "🔴 Not working" in (embed.description or "")
    embed = build_unified_menu_embed(_snapshot(dm_enabled=False, deals_today=None))
    assert "12 active deals · Your alerts: **OFF**" in (embed.description or "")
