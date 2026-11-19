"""Consolidation PR 2 (2026-11-19): the command tree itself.

Pins the shipped shape Felix approved in the consolidation plan:

- the live tree is exactly 7 top-levels / 30 slash commands;
- everything that left the tree is retired (not deleted) and still
  reachable through the menus, except the three demo commands, which
  Felix approved deleting outright;
- /staff refuses members without Manage Server, and the Walmart
  menu's Broad sweep (discover) carries the same staff gate;
- /movies status carries the old /movies sources list;
- the one-time announcement posts + pins exactly once per configured
  server and can never repeat.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import discord
import pytest
from discord import app_commands

from sniperplug.bot import SniperPlugBot
from sniperplug.cogs.canonical_settings import CanonicalSettingsCog
from sniperplug.cogs.registered_multi_source_movies import MovieTicketsCog
from sniperplug.cogs.sniperplug import SniperPlugCog
from sniperplug.cogs.staff_command import _DELEGATES as STAFF_DELEGATES
from sniperplug.cogs.staff_command import STAFF_SUBCOMMANDS, StaffCommandCog
from sniperplug.cogs.storage_admin import StorageAdminCog
from sniperplug.cogs.unified_menu import MENU_DELEGATES, MENU_ENTRIES
from sniperplug.cogs.walmart_command import (
    _DELEGATES as _WALMART_DELEGATES,
)
from sniperplug.cogs.walmart_command import (
    _LOCAL_DELEGATES as _WALMART_LOCAL_DELEGATES,
)
from sniperplug.cogs.walmart_command import (
    MENU_FUNCTIONS,
    WalmartCommandCog,
    WalmartMenuView,
)

WALMART_MENU_DELEGATES = {**_WALMART_DELEGATES, **_WALMART_LOCAL_DELEGATES}
from sniperplug.config import Settings
from sniperplug.services.command_surface import (
    RETIRED_COMMAND_NAMES,
    command_surface_issues,
)
from sniperplug.services.consolidation_announcement import (
    ANNOUNCEMENT_TEXT,
    maybe_post_consolidation_announcement,
)
from sniperplug.services.public_alert_config import set_public_alert_config
from sniperplug.storage.db import Database

EXPECTED_TOP_LEVELS = {"menu", "walmart", "wishlist", "movies", "verizon", "ebay", "staff"}
EXPECTED_GROUPS = {
    "walmart": {"check", "find", "hunt", "my-alerts"},
    "wishlist": {"add", "list", "remove", "clear"},
    "movies": {"latest", "status"},
    "verizon": {"digest", "status", "watch", "keywords"},
    "ebay": {"research", "status", "preview"},
    "staff": {
        "channel",
        "threshold",
        "categories",
        "extreme",
        "health",
        "active",
        "recovery",
        "dashboard",
        "hd-search",
        "hd-penny",
        "hd-stock",
        "test-alert",
    },
}


def _resolves(attr) -> bool:
    """A delegate target resolves if it is a plain method or an
    app_commands Command (the runtime calls it via .callback)."""

    return callable(attr) or hasattr(attr, "callback")


def _count_leaves(commands) -> int:
    total = 0
    for command in commands:
        children = getattr(command, "commands", None)
        if children:
            total += _count_leaves(children)
        else:
            total += 1
    return total


async def _load_bot(tmp_path: Path) -> SniperPlugBot:
    settings = Settings(
        discord_token="not-a-real-token",
        database_path=str(tmp_path / "sniperplug.sqlite3"),
        sync_commands_on_boot=False,
        sync_global_commands=True,
        dev_guild_ids=(),
    )
    bot = SniperPlugBot(settings)

    async def fake_sync_tree_safely(*, guild=None, scope: str):
        return []

    bot._sync_tree_safely = fake_sync_tree_safely  # type: ignore[method-assign]
    await bot.setup_hook()
    return bot


def test_live_tree_is_exactly_the_consolidated_surface(tmp_path: Path) -> None:
    async def run() -> None:
        bot = await _load_bot(tmp_path)
        try:
            top = {command.name: command for command in bot.tree.get_commands()}
            assert set(top) == EXPECTED_TOP_LEVELS
            for group_name, expected_subs in EXPECTED_GROUPS.items():
                group = top[group_name]
                assert {sub.name for sub in group.commands} == expected_subs, group_name
            assert _count_leaves(bot.tree.get_commands()) == 30
            assert command_surface_issues(bot.tree.get_commands()) == ()

            # Every menu delegate — hub, Walmart menu, and /staff —
            # resolves to a method on a cog the bot actually loaded,
            # so nothing that left the tree is stranded.
            by_class = {type(cog).__name__: cog for cog in bot.cogs.values()}
            for mapping in (MENU_DELEGATES, _WALMART_DELEGATES, STAFF_DELEGATES):
                for key, (cog_name, method_name) in mapping.items():
                    if key == "scan-now":
                        continue  # resolved by module, checked below
                    assert cog_name in by_class, (key, cog_name)
                    assert _resolves(getattr(by_class[cog_name], method_name)), (
                        key,
                        cog_name,
                        method_name,
                    )
            # scan-now resolves to the loaded global scan runner by
            # module, because three runner classes share one name.
            runners = [
                cog
                for cog in bot.cogs.values()
                if type(cog).__module__ == "sniperplug.cogs.global_auto_scan_runner"
            ]
            assert runners and _resolves(runners[0].autoscan_now)
            # The Walmart menu's one local delegate resolves through
            # the instance the group cog holds.
            walmart_cog = by_class["WalmartCommandCog"]
            for key, (attr_name, method_name) in _WALMART_LOCAL_DELEGATES.items():
                held = getattr(walmart_cog, attr_name)
                assert _resolves(getattr(held, method_name)), key
        finally:
            await bot.close()

    asyncio.run(run())


def test_bot_close_releases_the_database_connection(tmp_path: Path) -> None:
    """Regression pin for the CI hang this file exposed: loading the
    real bot opens an aiosqlite connection whose worker thread is
    non-daemon, so bot.close() must release it — otherwise the test
    process (and CI's pytest step) can never exit after passing."""

    async def run() -> None:
        bot = await _load_bot(tmp_path)
        assert bot.db.conn is not None
        await bot.close()
        assert bot.db.conn is None

    asyncio.run(run())


def test_retired_names_are_absent_but_registered_for_pruning() -> None:
    for name in (
        "sniperplug",
        "sniperplug_dashboard",
        "storage_health",
        "home_depot_search",
        "home_depot_penny_hunt",
        "hd_stock",
        "target_location",
        "walmart menu",
        "walmart discover",
        "walmart setup",
        "movies sources",
        "verizon list-keywords",
        "ebay review_sample",
    ):
        assert name in RETIRED_COMMAND_NAMES


def test_retired_features_stay_reachable_through_the_menus() -> None:
    # The Walmart menu covers every function again, including the five
    # diagnostics that once left it, and every one has a delegate.
    assert set(MENU_FUNCTIONS) == set(WALMART_MENU_DELEGATES)
    for key in ("trace", "probe", "history", "feedback", "feedback-reset", "recovery"):
        assert key in WALMART_MENU_DELEGATES
    # Handlers that left the tree still exist on their cogs.
    assert _resolves(CanonicalSettingsCog.sniperplug_dashboard)
    assert _resolves(SniperPlugCog.routes)
    assert _resolves(StorageAdminCog.storage_health)
    walmart_subs = {command.name for command in WalmartCommandCog(bot=None).app_command.commands}
    assert "discover" in walmart_subs  # pruned from the tree, alive in the menu
    # The movies sources handler also survives in code; only its slash
    # registration is retired (its content moved into /movies status).
    assert _resolves(MovieTicketsCog.sources)


def test_demo_commands_are_deleted_outright() -> None:
    for name in ("snipe_plan", "monitor_plan", "scan_test"):
        assert not hasattr(SniperPlugCog, name)
        assert f"sniperplug {name}" in RETIRED_COMMAND_NAMES
    hub_keys = {entry.key for entry in MENU_ENTRIES}
    assert not {"sniperplug-snipe-plan", "sniperplug-monitor-plan", "sniperplug-scan-test"} & hub_keys


def test_staff_group_shape_and_delegates() -> None:
    cog = StaffCommandCog(bot=None)
    subs = {command.name: command for command in cog.app_command.commands}
    assert set(subs) == set(STAFF_SUBCOMMANDS) == EXPECTED_GROUPS["staff"]
    for name, command in subs.items():
        assert callable(command.callback), name
        assert command.checks, name  # every staff subcommand is gated


@pytest.mark.asyncio
async def test_staff_checks_refuse_members_without_manage_server() -> None:
    cog = StaffCommandCog(bot=None)
    member = SimpleNamespace(
        permissions=discord.Permissions(manage_guild=False),
        guild_permissions=discord.Permissions(manage_guild=False),
    )
    staff = SimpleNamespace(
        permissions=discord.Permissions(manage_guild=True),
        guild_permissions=discord.Permissions(manage_guild=True),
    )
    import inspect

    async def run_check(check, interaction):
        outcome = check(interaction)
        if inspect.isawaitable(outcome):
            outcome = await outcome
        return outcome

    for command in cog.app_command.commands:
        assert command.checks, command.name
        for check in command.checks:
            with pytest.raises(app_commands.MissingPermissions):
                await run_check(check, member)
            assert await run_check(check, staff) is True


class _MenuResponse:
    def __init__(self) -> None:
        self.sent: list[tuple] = []
        self.edited: list[dict] = []
        self.modal = None

    def is_done(self) -> bool:
        return False

    async def send_message(self, content=None, **kwargs) -> None:
        self.sent.append((content, kwargs))

    async def edit_message(self, **kwargs) -> None:
        self.edited.append(kwargs)

    async def send_modal(self, modal) -> None:
        self.modal = modal


def _menu_interaction(*, manage_guild: bool) -> SimpleNamespace:
    return SimpleNamespace(
        guild_id=123,
        user=SimpleNamespace(
            id=42,
            guild_permissions=SimpleNamespace(manage_guild=manage_guild),
            roles=[],
        ),
        response=_MenuResponse(),
        followup=SimpleNamespace(send=_noop_send),
    )


async def _noop_send(*args, **kwargs) -> None:
    return None


@pytest.mark.asyncio
async def test_walmart_menu_discover_refuses_members(monkeypatch) -> None:
    delegated: list[str] = []

    async def recorder(self, interaction, key, *args, **kwargs):
        delegated.append(key)

    monkeypatch.setattr(WalmartCommandCog, "_delegate", recorder)
    view = WalmartMenuView(WalmartCommandCog(bot=None))

    member = _menu_interaction(manage_guild=False)
    await view.run_function(member, "discover")
    assert delegated == []
    assert member.response.sent
    content = member.response.sent[0][0] or ""
    assert "Manage Server" in content

    staff = _menu_interaction(manage_guild=True)
    await view.run_function(staff, "discover")
    assert delegated == ["discover"]


class _Followup:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send(self, *args, **kwargs) -> None:
        self.sent.append(kwargs)


@pytest.mark.asyncio
async def test_movies_status_carries_the_sources_list(tmp_path: Path) -> None:
    db = Database(str(tmp_path / "movies.sqlite3"))
    await db.connect()
    await db.init()
    try:
        cog = MovieTicketsCog(SimpleNamespace(db=db))
        interaction = SimpleNamespace(
            guild_id=1514374173517152418,
            response=SimpleNamespace(defer=_noop_send),
            followup=_Followup(),
        )
        await MovieTicketsCog.status.callback(cog, interaction)
        assert interaction.followup.sent
        embed = interaction.followup.sent[0]["embed"]
        fields = {field.name: field.value for field in embed.fields}
        assert "Sources" in fields
        assert "SeeItEarly" in fields["Sources"]
        assert "Gofobo" in fields["Sources"]
        assert "never scraped" in fields["Sources"]
    finally:
        await db.close()


# -- announcement ----------------------------------------------------------


class _FakeMessage:
    def __init__(self, *, pin_fails: bool = False) -> None:
        self.pinned = False
        self._pin_fails = pin_fails

    async def pin(self) -> None:
        if self._pin_fails:
            raise RuntimeError("no pin permission")
        self.pinned = True


class _FakeTextChannel:
    def __init__(self, channel_id: int, *, pin_fails: bool = False) -> None:
        self.id = channel_id
        self.sent: list[str] = []
        self.messages: list[_FakeMessage] = []
        self._pin_fails = pin_fails

    async def send(self, content: str) -> _FakeMessage:
        self.sent.append(content)
        message = _FakeMessage(pin_fails=self._pin_fails)
        self.messages.append(message)
        return message


class _FakeGuild:
    def __init__(self, guild_id: int, channels: dict[int, _FakeTextChannel]) -> None:
        self.id = guild_id
        self._channels = channels

    def get_channel(self, channel_id: int):
        return self._channels.get(channel_id)

    async def fetch_channel(self, channel_id: int):
        channel = self._channels.get(channel_id)
        if channel is None:
            raise RuntimeError("unknown channel")
        return channel


async def _announcement_bot(tmp_path: Path, *, pin_fails: bool = False):
    db = Database(str(tmp_path / "announce.sqlite3"))
    await db.connect()
    await db.init()
    await set_public_alert_config(
        db,
        guild_id=111,
        enabled=True,
        retailers=("walmart",),
        channel_id=555,
    )
    channel = _FakeTextChannel(555, pin_fails=pin_fails)
    configured = _FakeGuild(111, {555: channel})
    unconfigured = _FakeGuild(222, {})
    bot = SimpleNamespace(db=db, guilds=[configured, unconfigured])
    return db, bot, channel


def test_announcement_text_is_the_plans_three_lines() -> None:
    assert ANNOUNCEMENT_TEXT == (
        "SniperPlug's commands got simpler.\n"
        "Everything is under /menu now.\n"
        "/walmart find, /walmart hunt and /wishlist work exactly as before."
    )


@pytest.mark.asyncio
async def test_announcement_posts_and_pins_exactly_once(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(discord, "TextChannel", _FakeTextChannel)
    db, bot, channel = await _announcement_bot(tmp_path)
    try:
        assert await maybe_post_consolidation_announcement(bot) == 1
        assert channel.sent == [ANNOUNCEMENT_TEXT]
        assert channel.messages[0].pinned is True
        # Second boot: the persisted marker makes it a no-op.
        assert await maybe_post_consolidation_announcement(bot) == 0
        assert channel.sent == [ANNOUNCEMENT_TEXT]
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_announcement_survives_a_failed_pin(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(discord, "TextChannel", _FakeTextChannel)
    db, bot, channel = await _announcement_bot(tmp_path, pin_fails=True)
    try:
        assert await maybe_post_consolidation_announcement(bot) == 1
        assert channel.sent == [ANNOUNCEMENT_TEXT]
        assert channel.messages[0].pinned is False
        assert await maybe_post_consolidation_announcement(bot) == 0
    finally:
        await db.close()
