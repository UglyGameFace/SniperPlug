"""Tests for the reseller Walmart menu rebuild (2026-11-17).

Felix's verdict on the live menu: the 19-option select blows up into a
full-screen wall on mobile, staff commands are visible to everyone, and
the mechanics are hard to understand. Target audience is resellers:
quick, simple, to the point.

The new split:

* Everyone: Find deals, Hunt deals, My alerts buttons + DM toggle +
  Refresh. Check a deal appears only for openers who can check.
* Deal Finder role (The 420 Lobby role 1517181991412302025) OR Manage
  Server can check a deal — the slash command's gate and the menu's
  gate both use ``_can_check_deal``.
* Manage Server only: staff select (Setup, Threshold, Categories,
  Extreme leads, Health, Feedback, Discover, Scan now, Recheck,
  Active deals) + threshold stepper + extreme toggle.
* Out of the menu entirely (slash subcommands still registered and
  working): Recovery, History, Trace an item, API probe, Reset
  feedback.
"""

from __future__ import annotations

from types import SimpleNamespace

import discord
import pytest
from discord import app_commands

from sniperplug.cogs.unified_menu import UnifiedMenuCog
from sniperplug.cogs.walmart_command import (
    DEAL_FINDER_ROLE_ID,
    MENU_MEMBER_KEYS,
    MENU_REMOVED_KEYS,
    MENU_STAFF_KEYS,
    WALMART_SUBCOMMANDS,
    WalmartCheckModal,
    WalmartCommandCog,
    WalmartMenuView,
    _can_check_deal,
    build_walmart_menu_embed,
)


class _FakeRole:
    def __init__(self, role_id: int) -> None:
        self.id = role_id


class _FakeUser:
    def __init__(self, *, manage_guild: bool = False, role_ids=()) -> None:
        self.id = 123
        self.guild_permissions = SimpleNamespace(manage_guild=manage_guild)
        self.roles = [_FakeRole(role_id) for role_id in role_ids]


class _FakeResponse:
    def __init__(self) -> None:
        self.modal = None
        self.sent_message = None
        self.edited: list[dict] = []
        self.calls: list[str] = []
        self._done = False

    def is_done(self) -> bool:
        return self._done

    async def defer(self, *, ephemeral: bool = False) -> None:
        self.calls.append("defer")
        self._done = True

    async def send_modal(self, modal) -> None:
        self.modal = modal

    async def send_message(self, message: str, *, ephemeral: bool = False) -> None:
        self.sent_message = message

    async def edit_message(self, **kwargs) -> None:
        self.edited.append(kwargs)


class _FakeFollowup:
    def __init__(self) -> None:
        self.sent: list[tuple[tuple, dict]] = []

    async def send(self, *args, **kwargs) -> None:
        self.sent.append((args, kwargs))


class _FakeInteraction:
    def __init__(self, *, manage_guild: bool = False, role_ids=()) -> None:
        self.user = _FakeUser(manage_guild=manage_guild, role_ids=role_ids)
        self.guild_id = 456
        self.response = _FakeResponse()
        self.followup = _FakeFollowup()

    async def edit_original_response(self, **kwargs) -> None:
        self.response.edited.append(kwargs)


def _buttons(view: WalmartMenuView) -> dict[str, discord.ui.Button]:
    return {
        button.label: button
        for button in view.children
        if isinstance(button, discord.ui.Button)
    }


def _selects(view: WalmartMenuView) -> list[discord.ui.Select]:
    return [child for child in view.children if isinstance(child, discord.ui.Select)]


def _embed_text(embed: discord.Embed) -> str:
    parts = [embed.title or "", embed.description or ""]
    parts.extend((field.name or "") + "\n" + (field.value or "") for field in embed.fields)
    parts.append(embed.footer.text or "")
    return "\n".join(parts)


# -- the role gate -------------------------------------------------------


def test_deal_finder_role_id_is_the_live_role() -> None:
    assert DEAL_FINDER_ROLE_ID == 1517181991412302025


def test_check_gate_allows_role_holder() -> None:
    interaction = _FakeInteraction(role_ids=[DEAL_FINDER_ROLE_ID])
    assert _can_check_deal(interaction) is True


def test_check_gate_denies_plain_member() -> None:
    assert _can_check_deal(_FakeInteraction()) is False


def test_check_gate_allows_manage_server() -> None:
    assert _can_check_deal(_FakeInteraction(manage_guild=True)) is True


def test_check_gate_denies_an_unrelated_role() -> None:
    assert _can_check_deal(_FakeInteraction(role_ids=[111222333444555666])) is False


def test_check_gate_guards_dm_and_missing_roles() -> None:
    # A DM user has no roles attribute at all; a bare namespace has none.
    no_roles = SimpleNamespace(user=SimpleNamespace(id=1))
    assert _can_check_deal(no_roles) is False
    assert _can_check_deal(SimpleNamespace(user=None)) is False
    assert _can_check_deal(SimpleNamespace()) is False


def test_check_slash_gate_uses_the_role_predicate() -> None:
    cog = WalmartCommandCog(bot=None)
    assert len(cog.check.checks) == 1
    predicate = cog.check.checks[0]
    assert predicate(_FakeInteraction(role_ids=[DEAL_FINDER_ROLE_ID])) is True
    assert predicate(_FakeInteraction()) is False
    assert predicate(_FakeInteraction(manage_guild=True)) is True


@pytest.mark.asyncio
async def test_check_slash_denies_plain_member_with_role_message() -> None:
    cog = WalmartCommandCog(bot=None)
    interaction = _FakeInteraction()
    await cog.check_error(interaction, app_commands.CheckFailure("nope"))
    assert interaction.response.sent_message is not None
    assert "Deal Finder" in interaction.response.sent_message
    assert "Manage Server" in interaction.response.sent_message
    assert "use `/walmart find`" not in interaction.response.sent_message


# -- the split: member view ----------------------------------------------


def test_member_view_has_find_hunt_my_alerts_dm_refresh_and_no_staff() -> None:
    view = WalmartMenuView(WalmartCommandCog(bot=None))
    labels = _buttons(view)
    assert set(labels) == {
        "Find deals",
        "Hunt deals",
        "My alerts",
        "DM alerts: OFF",
        "Refresh",
    }
    assert _selects(view) == []
    assert "Check a deal" not in labels
    assert "−5%" not in labels
    assert "Extreme: OFF" not in labels
    assert view.timeout == 300


def test_role_holder_view_shows_check_button_but_no_staff() -> None:
    view = WalmartMenuView(WalmartCommandCog(bot=None), can_check=True)
    labels = _buttons(view)
    assert "Check a deal" in labels
    assert {"Find deals", "Hunt deals", "My alerts"} <= set(labels)
    assert _selects(view) == []
    assert "−5%" not in labels
    assert "Extreme: OFF" not in labels


def test_staff_view_shows_staff_select_stepper_and_extreme() -> None:
    view = WalmartMenuView(
        WalmartCommandCog(bot=None), manage_guild=True, can_check=True
    )
    selects = _selects(view)
    assert len(selects) == 1
    assert {option.value for option in selects[0].options} == set(MENU_STAFF_KEYS)
    assert len(selects[0].options) == 10
    labels = _buttons(view)
    assert {"Check a deal", "Find deals", "Hunt deals", "My alerts"} <= set(labels)
    assert {"−5%", "40%", "+5%", "Extreme: OFF", "DM alerts: OFF", "Refresh"} <= set(labels)


def test_staff_select_options_are_staff_only() -> None:
    view = WalmartMenuView(
        WalmartCommandCog(bot=None), manage_guild=True, can_check=True
    )
    options = {option.value for option in _selects(view)[0].options}
    assert options.isdisjoint(MENU_REMOVED_KEYS)
    assert options.isdisjoint({"check", "find", "hunt", "my-alerts"})


# -- the split: menu behavior --------------------------------------------


@pytest.mark.asyncio
async def test_menu_check_blocked_for_plain_member() -> None:
    interaction = _FakeInteraction()
    await WalmartMenuView(WalmartCommandCog(bot=None)).run_function(
        interaction, "check"
    )
    assert interaction.response.modal is None
    assert interaction.response.sent_message is not None
    assert "Deal Finder" in interaction.response.sent_message


@pytest.mark.asyncio
async def test_menu_check_opens_modal_for_role_holder() -> None:
    interaction = _FakeInteraction(role_ids=[DEAL_FINDER_ROLE_ID])
    view = WalmartMenuView(WalmartCommandCog(bot=None), can_check=True)
    await view.run_function(interaction, "check")
    assert isinstance(interaction.response.modal, WalmartCheckModal)


@pytest.mark.asyncio
async def test_my_alerts_runs_in_place_via_delegate() -> None:
    calls: list[dict] = []

    class _FakeDmCog:
        async def dm_deals(self, interaction, action, **kwargs) -> None:
            calls.append({"action": action, "kwargs": kwargs})

    bot = SimpleNamespace(
        get_cog=lambda name: _FakeDmCog() if name == "DmDealAlertsCog" else None
    )
    cog = WalmartCommandCog(bot=bot)
    interaction = _FakeInteraction()
    await WalmartMenuView(cog).run_function(interaction, "my-alerts")
    assert len(calls) == 1
    assert calls[0]["action"].value == "menu"
    assert interaction.response.sent_message is None
    assert interaction.response.edited == []


@pytest.mark.asyncio
async def test_staff_select_routes_to_run_function() -> None:
    calls: list[dict] = []

    class _FakeHealthCog:
        async def autoscan_health(self, interaction) -> None:
            calls.append({"interaction": interaction})

    bot = SimpleNamespace(
        get_cog=lambda name: (
            _FakeHealthCog() if name == "CanonicalPublicAlertsCog" else None
        )
    )
    cog = WalmartCommandCog(bot=bot)
    view = WalmartMenuView(cog, manage_guild=True, can_check=True)
    select = _selects(view)[0]
    select._values = ["health"]
    interaction = _FakeInteraction(manage_guild=True)
    await select.callback(interaction)
    assert len(calls) == 1
    assert calls[0]["interaction"] is interaction


# -- the split: embed ------------------------------------------------------


def test_staff_embed_lists_menu_functions_and_not_removed_ones() -> None:
    embed = build_walmart_menu_embed(manage_guild=True, can_check=True)
    text = _embed_text(embed)
    for key in list(MENU_MEMBER_KEYS) + list(MENU_STAFF_KEYS):
        assert f"/walmart {key}" in text
    for key in MENU_REMOVED_KEYS:
        assert f"/walmart {key}" not in text
    assert "Deal checks need" not in text


def test_member_embed_lists_only_member_functions_and_role_line() -> None:
    embed = build_walmart_menu_embed(manage_guild=False, can_check=False)
    text = _embed_text(embed)
    assert "/walmart find" in text
    assert "/walmart hunt" in text
    assert "/walmart my-alerts" in text
    assert "/walmart check" not in text
    for key in list(MENU_STAFF_KEYS) + list(MENU_REMOVED_KEYS):
        assert f"/walmart {key}" not in text
    assert "Deal checks need the 🔍 Deal Finder role — ask staff." in text


def test_role_holder_embed_lists_check_but_no_staff_and_no_role_line() -> None:
    embed = build_walmart_menu_embed(manage_guild=False, can_check=True)
    text = _embed_text(embed)
    assert "/walmart check" in text
    for key in list(MENU_STAFF_KEYS) + list(MENU_REMOVED_KEYS):
        assert f"/walmart {key}" not in text
    assert "Deal checks need" not in text


def test_removed_keys_still_registered_as_slash_subcommands() -> None:
    cog = WalmartCommandCog(bot=None)
    names = {command.name for command in cog.app_command.commands}
    assert set(MENU_REMOVED_KEYS).issubset(names)
    assert set(WALMART_SUBCOMMANDS).issubset(names)


# -- threading: /walmart menu and /menu -----------------------------------


@pytest.mark.asyncio
async def test_walmart_menu_passes_flags_to_view(monkeypatch) -> None:
    import sniperplug.cogs.walmart_command as wc
    from sniperplug.services.menu_dashboard_state import MenuDashboardState

    async def _fake_load(bot, guild_id, user_id):
        return MenuDashboardState()

    monkeypatch.setattr(wc, "load_menu_dashboard_state", _fake_load)

    cog = WalmartCommandCog(bot=None)
    member = _FakeInteraction()
    await cog.menu.callback(cog, member)
    view = member.followup.sent[0][1]["view"]
    assert "Check a deal" not in _buttons(view)
    assert _selects(view) == []
    embed = member.followup.sent[0][1]["embed"]
    assert "Deal checks need the 🔍 Deal Finder role" in _embed_text(embed)

    staff = _FakeInteraction(manage_guild=True)
    await cog.menu.callback(cog, staff)
    view = staff.followup.sent[0][1]["view"]
    assert "Check a deal" in _buttons(view)
    assert len(_selects(view)) == 1


@pytest.mark.asyncio
async def test_unified_menu_walmart_category_passes_flags() -> None:
    walmart_cog = WalmartCommandCog(bot=None)
    bot = SimpleNamespace(
        get_cog=lambda name: walmart_cog if name == "WalmartCommandCog" else None
    )
    cog = UnifiedMenuCog(bot=bot)

    member = _FakeInteraction()
    await cog.open_category(member, "walmart")
    view = member.response.edited[0]["view"]
    assert "Check a deal" not in _buttons(view)
    assert _selects(view) == []
    embed = member.response.edited[0]["embed"]
    assert "Deal checks need the 🔍 Deal Finder role" in _embed_text(embed)

    staff = _FakeInteraction(manage_guild=True)
    await cog.open_category(staff, "walmart")
    view = staff.response.edited[0]["view"]
    assert "Check a deal" in _buttons(view)
    assert len(_selects(view)) == 1
