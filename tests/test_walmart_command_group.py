"""Tests for the /walmart command group, its menu, and the review-pass fixes.

The 17 folded Walmart commands plus the 2 restored ones keep their handlers
on their original cogs; this file pins the new surface: the group registers
all 20 subcommands, the
old names are retired and pruned from the tree, the menu reaches every
function, and the small behaviour fixes from the review pass hold.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import discord
from discord import app_commands

from sniperplug.cogs.deal_feedback_admin import normalize_reset_scope
from sniperplug.cogs.dm_deal_alerts import DmDealAlertsCog
from sniperplug.cogs.walmart_command import (
    MENU_FUNCTIONS,
    MENU_MEMBER_KEYS,
    MENU_REMOVED_KEYS,
    MENU_STAFF_KEYS,
    WALMART_SUBCOMMANDS,
    WalmartCheckModal,
    WalmartCommandCog,
    WalmartMenuView,
    build_walmart_menu_embed,
)
from sniperplug.services.command_surface import (
    REQUIRED_CANONICAL_COMMANDS,
    RETIRED_COMMAND_NAMES,
    command_surface_issues,
    prune_retired_commands,
    replacement_for,
)


# Old top-level name -> where its retirement message points now.
# Consolidation PR 2 (2026-11-19): staff functions moved under /staff
# and the menu-only functions point at their /menu entries.
OLD_TO_NEW = {
    "check_deal": "/walmart check",
    "deals": "/walmart find",
    "hunt": "/walmart hunt",
    "discover": "/menu → Broad sweep",
    "autoscan_health": "/staff health",
    "deal_threshold": "/staff threshold",
    "deal_categories": "/staff categories",
    "walmart_extreme_alerts": "/staff extreme",
    "walmart_recovery": "/staff recovery",
    "deal_trace": "/menu → Trace an item",
    "dm_deals": "/walmart my-alerts",
    "feedback_learning_status": "/menu → Feedback",
    "feedback_learning_reset": "/menu → Reset feedback",
    "active_deals": "/staff active",
    "active_deal_history": "/menu → History",
    "setup_sniperplug_here": "/staff channel",
    "autoscan_now": "/menu → Scan now",
}


def test_walmart_group_has_all_20_subcommands() -> None:
    cog = WalmartCommandCog(bot=None)
    names = {command.name for command in cog.app_command.commands}
    assert names == set(WALMART_SUBCOMMANDS) | {"menu"}
    assert len(names) == 20


def test_my_alerts_keeps_the_simple_option_set() -> None:
    cog = WalmartCommandCog(bot=None)
    my_alerts = next(
        command for command in cog.app_command.commands if command.name == "my-alerts"
    )
    # The 2026-11-20 simplification removed mode, score/savings floors,
    # favorites, mutes, keywords and the separate flip toggle; the menu is
    # the primary surface and these typed options are the backup.
    assert [param.name for param in my_alerts.parameters] == [
        "action",
        "min_discount",
        "max_price",
        "categories",
        "product_interests",
        "remove_interests",
        "brands",
        "remove_brands",
        "flip_min_profit",
        "daily_cap",
    ]


def test_check_and_find_keep_their_parameters() -> None:
    cog = WalmartCommandCog(bot=None)
    by_name = {command.name: command for command in cog.app_command.commands}
    # Same signatures as the old top-level commands: check takes one link,
    # find takes one search, hunt takes no options (it opens a picker).
    assert [param.name for param in by_name["check"].parameters] == ["link"]
    assert [param.name for param in by_name["find"].parameters] == ["search"]
    assert [param.name for param in by_name["hunt"].parameters] == []


def test_old_walmart_names_are_retired_with_new_paths() -> None:
    for old, replacement in OLD_TO_NEW.items():
        assert old in RETIRED_COMMAND_NAMES
        assert replacement_for(old) == replacement
    # /walmart cash was retired 2026-11-17 (no Cash offer feed exists), so
    # its old top-level name now points at /walmart find instead.
    assert "walmart_cash" in RETIRED_COMMAND_NAMES
    assert replacement_for("walmart_cash") == "/walmart find"
    assert "walmart" not in RETIRED_COMMAND_NAMES


def test_prune_removes_old_walmart_names_and_keeps_the_group() -> None:
    class _FakeTree:
        def __init__(self, names: set[str]):
            self.commands = {name: SimpleNamespace(name=name) for name in names}

        def get_command(self, name: str):
            return self.commands.get(name)

        def remove_command(self, name: str, **_kwargs):
            return self.commands.pop(name, None)

        def get_commands(self):
            return list(self.commands.values())

    tree = _FakeTree(set(OLD_TO_NEW) | set(REQUIRED_CANONICAL_COMMANDS))
    removed = prune_retired_commands(tree)
    remaining = {command.name for command in tree.get_commands()}
    assert {item.name for item in removed} == set(OLD_TO_NEW)
    assert not set(OLD_TO_NEW).intersection(remaining)
    assert set(REQUIRED_CANONICAL_COMMANDS).issubset(remaining)
    assert command_surface_issues(tree.get_commands()) == ()


def test_menu_embed_lists_menu_functions_not_removed_ones() -> None:
    # Staff embed: every menu function is listed. Since consolidation
    # PR 2 the embed prints each function's real home (a /staff command
    # or nothing for menu-only functions) instead of a fake /walmart
    # subcommand, and the five diagnostics that once left the menu are
    # back in the staff sections.
    embed = build_walmart_menu_embed(manage_guild=True, can_check=True)
    text = (embed.description or "") + "\n" + "\n".join(
        (field.name or "") + "\n" + (field.value or "") for field in embed.fields
    )
    for sub in list(MENU_MEMBER_KEYS) + list(MENU_STAFF_KEYS):
        usage = MENU_FUNCTIONS[sub][2]
        if usage:
            assert usage in text
        assert MENU_FUNCTIONS[sub][1] in text
    assert "🧪 Diagnostics" in text
    assert MENU_REMOVED_KEYS == frozenset()


def test_all_walmart_subcommands_still_registered() -> None:
    # The five functions left the MENU only — their slash subcommands
    # still exist and still work.
    cog = WalmartCommandCog(bot=None)
    names = {command.name for command in cog.app_command.commands}
    assert set(WALMART_SUBCOMMANDS).issubset(names)
    assert set(MENU_REMOVED_KEYS).issubset(names)


def test_scan_now_keeps_force_parameter() -> None:
    cog = WalmartCommandCog(bot=None)
    by_name = {command.name: command for command in cog.app_command.commands}
    # Same signature as the old top-level command: one optional force flag
    # that defaults to True (bypass interval/daily gate for a manual test).
    assert [param.name for param in by_name["scan-now"].parameters] == ["force"]


def test_staff_menu_view_has_staff_select_and_quick_buttons() -> None:
    view = WalmartMenuView(
        WalmartCommandCog(bot=None), manage_guild=True, can_check=True
    )
    selects = [child for child in view.children if isinstance(child, discord.ui.Select)]
    assert len(selects) == 1
    assert {option.value for option in selects[0].options} == set(MENU_STAFF_KEYS)
    assert len(selects[0].options) == 15
    buttons = [child for child in view.children if isinstance(child, discord.ui.Button)]
    # Dashboard controls (spec: threshold stepper, extreme toggle, DM toggle,
    # Refresh) sit alongside the four member buttons; with no snapshot the
    # stepper shows the 40% default and both toggles show OFF.
    assert {button.label for button in buttons} == {
        "Check a link",
        "Find deals",
        "Browse",
        "My alerts",
        "−5%",
        "40%",
        "+5%",
        "Extreme: OFF",
        "DM alerts: OFF",
        "Refresh",
    }


class _FakeResponse:
    def __init__(self) -> None:
        self.modal = None
        self.sent_message = None
        self.edited: list[dict] = []

    def is_done(self) -> bool:
        return False

    async def defer(self, *, ephemeral: bool = False) -> None:
        return None

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


class _FakeUser:
    def __init__(self, *, manage_guild: bool = True) -> None:
        self.id = 123
        self.guild_permissions = SimpleNamespace(manage_guild=manage_guild)
        self.dm_sends: list[dict] = []

    async def send(self, *args, **kwargs) -> None:
        self.dm_sends.append(kwargs)


class _FakeInteraction:
    def __init__(self, *, manage_guild: bool = True) -> None:
        self.user = _FakeUser(manage_guild=manage_guild)
        self.guild_id = 456
        self.response = _FakeResponse()
        self.followup = _FakeFollowup()


@pytest.mark.asyncio
async def test_menu_guidance_shows_the_exact_subcommand() -> None:
    interaction = _FakeInteraction()
    await WalmartMenuView(WalmartCommandCog(bot=None)).run_function(interaction, "threshold")
    assert len(interaction.response.edited) == 1
    embed = interaction.response.edited[0]["embed"]
    assert "/staff threshold" in (embed.title or "") + (embed.description or "")


@pytest.mark.asyncio
async def test_menu_check_opens_the_check_modal() -> None:
    interaction = _FakeInteraction()
    await WalmartMenuView(WalmartCommandCog(bot=None)).run_function(interaction, "check")
    assert isinstance(interaction.response.modal, WalmartCheckModal)


@pytest.mark.asyncio
async def test_menu_blocks_admin_functions_without_manage_server() -> None:
    interaction = _FakeInteraction(manage_guild=False)
    await WalmartMenuView(WalmartCommandCog(bot=None)).run_function(interaction, "threshold")
    assert interaction.response.sent_message is not None
    assert "Manage Server" in interaction.response.sent_message


@pytest.mark.asyncio
async def test_delegate_calls_plain_trace_method() -> None:
    calls: list[tuple] = []

    class _FakeSniperPlugCog:
        async def deal_trace(self, interaction, item_id: str) -> None:
            calls.append((interaction, item_id))

    bot = SimpleNamespace(get_cog=lambda name: _FakeSniperPlugCog() if name == "SniperPlugCog" else None)
    cog = WalmartCommandCog(bot=bot)
    interaction = _FakeInteraction()
    await cog._delegate(interaction, "trace", item_id="425031866")
    assert calls == [(interaction, "425031866")]


def test_normalize_reset_scope_rejects_unknown_instead_of_wiping_all() -> None:
    assert normalize_reset_scope("products") == "products"
    assert normalize_reset_scope("all") == "all"
    with pytest.raises(ValueError):
        normalize_reset_scope("bogus")
    with pytest.raises(ValueError):
        normalize_reset_scope("")


class _FakeCursor:
    def __init__(self, row=None) -> None:
        self._row = row

    async def fetchone(self):
        return self._row


class _FakeConn:
    def __init__(self, preference_row=None) -> None:
        self._preference_row = preference_row

    async def execute(self, sql, params=()):
        if "COUNT(*)" in sql:
            return _FakeCursor({"total": 0})
        if "SELECT user_id, enabled" in sql:
            return _FakeCursor(self._preference_row)
        return _FakeCursor(None)

    async def commit(self) -> None:
        return None


class _FakeDb:
    def __init__(self, preference_row=None) -> None:
        self._preference_row = preference_row

    def require_conn(self):
        return _FakeConn(self._preference_row)


@pytest.mark.asyncio
async def test_dm_deals_status_warns_when_typed_options_are_ignored() -> None:
    import sniperplug.services.dm_deal_alerts as dm_service

    saved = dm_service._SCHEMA_READY
    dm_service._SCHEMA_READY = True
    try:
        cog = DmDealAlertsCog(bot=SimpleNamespace(db=_FakeDb()))
        interaction = _FakeInteraction()
        await cog.dm_deals.callback(
            cog,
            interaction,
            action=app_commands.Choice(name="Status", value="status"),
            min_discount=50,
        )
    finally:
        dm_service._SCHEMA_READY = saved
    assert len(interaction.followup.sent) == 1
    _, kwargs = interaction.followup.sent[0]
    embed = kwargs["embed"]
    ignored = [field for field in embed.fields if field.name == "Ignored options"]
    assert len(ignored) == 1
    assert "`min_discount`" in ignored[0].value


@pytest.mark.asyncio
async def test_dm_deals_enable_with_nothing_typed_says_already_enabled() -> None:
    import sniperplug.services.dm_deal_alerts as dm_service

    saved = dm_service._SCHEMA_READY
    dm_service._SCHEMA_READY = True
    try:
        preference_row = {
            "user_id": "123",
            "enabled": 1,
            "mode": "all",
            "min_discount": None,
            "max_price_cents": None,
            "min_score": None,
            "min_savings_cents": None,
            "categories_json": None,
            "keywords_json": None,
            "exclude_keywords_json": None,
            "walmart_cash_only": 0,
            "max_alerts_per_day": None,
            "failure_count": 0,
            "last_error": None,
        }
        cog = DmDealAlertsCog(bot=SimpleNamespace(db=_FakeDb(preference_row)))
        interaction = _FakeInteraction()
        await cog.dm_deals.callback(
            cog,
            interaction,
            action=app_commands.Choice(name="Enable or update alerts", value="enable"),
        )
    finally:
        dm_service._SCHEMA_READY = saved
    assert len(interaction.followup.sent) == 1
    _, kwargs = interaction.followup.sent[0]
    embed = kwargs["embed"]
    assert "already enabled" in (embed.title or "")


def test_every_delegate_target_is_a_loaded_cog_with_its_method() -> None:
    """Regression: check/find delegated to DealScannerCog, which the
    live bot never loads (bot.py only adds VerifiedDealScannerCog), so both
    failed at call time with "isn't loaded right now". Every delegate
    target must be a cog bot.py actually adds, and must carry its method —
    inherited methods count, since check_deal lives on the base class."""

    import ast
    import importlib
    from pathlib import Path

    from sniperplug.cogs.walmart_command import _DELEGATES

    bot_path = Path(__file__).resolve().parents[1] / "sniperplug" / "bot.py"
    tree = ast.parse(bot_path.read_text())
    # bot.py imports the live runner aliased (AutoScanRunnerCog as
    # GlobalAutoScanRunnerCog); the runtime cog name is the real class name.
    aliases = {
        name.asname: name.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for name in node.names
        if name.asname
    }
    loaded: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Await):
            continue
        call = node.value
        if not isinstance(call, ast.Call):
            continue
        func = call.func
        if not (isinstance(func, ast.Attribute) and func.attr == "add_cog"):
            continue
        if not call.args or not isinstance(call.args[0], ast.Call):
            continue
        arg_func = call.args[0].func
        if isinstance(arg_func, ast.Name):
            loaded.add(aliases.get(arg_func.id, arg_func.id))

    cog_modules = {
        "VerifiedDealScannerCog": "sniperplug.cogs.verified_deal_scanner",
        "AutoDiscoveryCog": "sniperplug.cogs.auto_discovery",
        "CanonicalPublicAlertsCog": "sniperplug.cogs.canonical_public_alerts",
        "CanonicalSettingsCog": "sniperplug.cogs.canonical_settings",
        "SniperPlugCog": "sniperplug.cogs.sniperplug",
        "DmDealAlertsCog": "sniperplug.cogs.dm_deal_alerts",
        "DealFeedbackAdminCog": "sniperplug.cogs.deal_feedback_admin",
        "ActiveDealsCog": "sniperplug.cogs.active_deals",
        "ActiveDealHistoryCog": "sniperplug.cogs.active_deal_history",
        "CanonicalWorkflowCog": "sniperplug.cogs.canonical_workflow",
        # The live runner's class is AutoScanRunnerCog (global module);
        # bot.py imports it aliased as GlobalAutoScanRunnerCog.
        "AutoScanRunnerCog": "sniperplug.cogs.global_auto_scan_runner",
    }
    for key, (cog_name, method_name) in _DELEGATES.items():
        assert cog_name in loaded, (
            f"/walmart {key} delegates to {cog_name}, which bot.py never loads"
        )
        module = importlib.import_module(cog_modules[cog_name])
        cls = getattr(module, cog_name)
        assert hasattr(cls, method_name), (
            f"{cog_name} has no {method_name} for /walmart {key}"
        )
