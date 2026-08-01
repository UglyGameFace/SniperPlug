from __future__ import annotations

import asyncio
from types import SimpleNamespace

import discord
from discord import app_commands

from sniperplug.bot import SniperPlugBot, app_command_schema_issues
from sniperplug.cogs.registered_multi_source_movies import MovieTicketsCog


class _FakeCommand:
    def __init__(
        self,
        *,
        name: str,
        description: str,
        parameters=(),
        commands=(),
    ) -> None:
        self.name = name
        self.qualified_name = name
        self.description = description
        self.parameters = parameters
        self.commands = commands


class _FakeTree:
    def __init__(self, *, commands=(), sync_error: BaseException | None = None) -> None:
        self._commands = list(commands)
        self.sync_error = sync_error
        self.sync_calls = 0

    def get_commands(self, *, guild=None):
        return list(self._commands)

    async def sync(self, *, guild=None):
        self.sync_calls += 1
        if self.sync_error is not None:
            raise self.sync_error
        return []


class _FakeBot:
    _sync_commands = SniperPlugBot._sync_commands
    _sync_tree_safely = SniperPlugBot._sync_tree_safely

    def __init__(self, tree: _FakeTree) -> None:
        self.tree = tree
        self.settings = SimpleNamespace(
            sync_commands_on_boot=True,
            sync_global_commands=True,
            dev_guild_ids=(),
        )


def test_registered_movies_group_description_is_discord_safe() -> None:
    group = MovieTicketsCog.__cog_app_commands_group__
    assert group.name == "movies"
    assert 1 <= len(str(group.description)) <= 100


def test_schema_preflight_detects_oversized_description() -> None:
    issues = app_command_schema_issues(
        [_FakeCommand(name="movies", description="x" * 101)]
    )
    assert len(issues) == 1
    assert "description length 101" in issues[0]


def test_invalid_local_schema_skips_http_sync_without_crashing(monkeypatch) -> None:
    monkeypatch.delenv("CLEAR_STALE_GLOBAL_COMMANDS_ON_BOOT", raising=False)
    tree = _FakeTree(
        commands=[_FakeCommand(name="movies", description="x" * 101)]
    )
    bot = _FakeBot(tree)

    asyncio.run(bot._sync_commands())

    assert tree.sync_calls == 0


def test_discord_command_sync_rejection_does_not_crash_startup(monkeypatch) -> None:
    monkeypatch.delenv("CLEAR_STALE_GLOBAL_COMMANDS_ON_BOOT", raising=False)

    class _Response:
        status = 400
        reason = "Bad Request"
        headers = {}

    http_error = discord.HTTPException(
        _Response(),
        {"code": 50035, "message": "Invalid Form Body"},
    )
    sync_error = app_commands.CommandSyncFailure(http_error, [])
    tree = _FakeTree(sync_error=sync_error)
    bot = _FakeBot(tree)

    asyncio.run(bot._sync_commands())

    assert tree.sync_calls == 1
