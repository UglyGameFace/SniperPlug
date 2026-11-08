"""B5: hash-change command sync.

Covers the fingerprint (surface changes change it, cosmetic changes do not),
the boot decision (first boot syncs, unchanged second boot skips, changed
surface syncs once and persists, sync failure does not persist and retries,
forced sync still works, invalid schema still blocks), and that /check_deal
is in the loaded tree when all 20 canonical cogs load against a scratch
SQLite DB (no live Discord connection).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

import discord
from discord import app_commands

from sniperplug.bot import SniperPlugBot
from sniperplug.config import Settings
from sniperplug.services.command_surface import REQUIRED_CANONICAL_COMMANDS
from sniperplug.services.command_surface_hash import (
    CommandSurfaceHashStore,
    command_surface_fingerprint,
    default_hash_store_path,
)


class _FakeChoice:
    def __init__(self, *, name: str, value: str) -> None:
        self.name = name
        self.value = value


class _FakeParameter:
    def __init__(
        self,
        *,
        name: str,
        description: str = "A parameter",
        type_name: str = "string",
        required: bool = True,
        choices=(),
        min_value=None,
        max_value=None,
        autocomplete: bool = False,
    ) -> None:
        self.name = name
        self.description = description
        self.type = SimpleNamespace(name=type_name)
        self.required = required
        self.choices = list(choices)
        self.min_value = min_value
        self.max_value = max_value
        self.autocomplete = autocomplete


class _FakeCommand:
    def __init__(
        self,
        *,
        name: str,
        description: str = "A command",
        parameters=(),
        commands=(),
        **extras,
    ) -> None:
        self.name = name
        self.qualified_name = name
        self.description = description
        self.parameters = list(parameters)
        self.commands = list(commands)
        self.type = SimpleNamespace(name="chat_input")
        for key, value in extras.items():
            setattr(self, key, value)


class _FakeTree:
    def __init__(self, *, commands=(), sync_error: BaseException | None = None) -> None:
        self._commands = list(commands)
        self._guild_commands: dict[int, list] = {}
        self.sync_error = sync_error
        self.sync_calls: list[object] = []

    def get_commands(self, *, guild=None):
        if guild is None:
            return list(self._commands)
        return list(self._guild_commands.get(guild.id, []))

    def get_command(self, name: str):
        for command in self._commands:
            if command.name == name:
                return command
        return None

    def clear_commands(self, *, guild=None):
        if guild is None:
            self._commands = []
        else:
            self._guild_commands[guild.id] = []

    def copy_global_to(self, *, guild=None):
        self._guild_commands[guild.id] = list(self._commands)

    async def sync(self, *, guild=None):
        self.sync_calls.append(guild.id if guild is not None else None)
        if self.sync_error is not None:
            raise self.sync_error
        return []


class _FakeBot:
    _sync_commands = SniperPlugBot._sync_commands
    _sync_tree_safely = SniperPlugBot._sync_tree_safely
    _persist_surface_hash = staticmethod(SniperPlugBot._persist_surface_hash)

    def __init__(
        self,
        tree: _FakeTree,
        store: CommandSurfaceHashStore,
        *,
        sync_on_boot: bool = False,
        sync_global: bool = True,
        dev_guild_ids: tuple[int, ...] = (),
    ) -> None:
        self.tree = tree
        self.command_surface_hash_store = store
        self.settings = SimpleNamespace(
            sync_commands_on_boot=sync_on_boot,
            sync_global_commands=sync_global,
            dev_guild_ids=dev_guild_ids,
        )


def _run(bot: _FakeBot) -> None:
    asyncio.run(bot._sync_commands())


def _base_commands() -> list[_FakeCommand]:
    commands: list[_FakeCommand] = []
    for name in sorted(REQUIRED_CANONICAL_COMMANDS):
        if name == "deals":
            commands.append(
                _FakeCommand(
                    name="deals",
                    description="Find deals",
                    parameters=[
                        _FakeParameter(name="query", description="Search text")
                    ],
                )
            )
        elif name == "sniperplug":
            commands.append(
                _FakeCommand(
                    name="sniperplug",
                    description="Owner tools",
                    commands=[
                        _FakeCommand(name="provider_scan", description="Scan providers"),
                    ],
                )
            )
        elif name == "hunt":
            commands.append(_FakeCommand(name="hunt", description="Hunt verified deals"))
        else:
            commands.append(
                _FakeCommand(name=name, description=f"Canonical {name} command")
            )
    return commands


def _discord_sync_error() -> app_commands.CommandSyncFailure:
    class _Response:
        status = 400
        reason = "Bad Request"
        headers = {}

    http_error = discord.HTTPException(
        _Response(),
        {"code": 50035, "message": "Invalid Form Body"},
    )
    return app_commands.CommandSyncFailure(http_error, [])


def _replace(
    commands: list[_FakeCommand], name: str, replacement: _FakeCommand
) -> list[_FakeCommand]:
    return [replacement if command.name == name else command for command in commands]


# --- Fingerprint behaviour -------------------------------------------------


def test_fingerprint_changes_when_command_added_removed_or_renamed() -> None:
    base = command_surface_fingerprint(_base_commands())

    added = _base_commands()
    added.append(_FakeCommand(name="check_deal", description="Check one product"))
    assert command_surface_fingerprint(added) != base

    removed = [command for command in _base_commands() if command.name != "hunt"]
    assert command_surface_fingerprint(removed) != base

    renamed = _replace(
        _base_commands(), "hunt", _FakeCommand(name="hunt2", description="Hunt verified deals")
    )
    assert command_surface_fingerprint(renamed) != base


def test_fingerprint_changes_when_subcommand_changes() -> None:
    base = command_surface_fingerprint(_base_commands())

    changed = _replace(
        _base_commands(),
        "sniperplug",
        _FakeCommand(
            name="sniperplug",
            description="Owner tools",
            commands=[
                _FakeCommand(name="provider_probe", description="Scan providers")
            ],
        ),
    )
    assert command_surface_fingerprint(changed) != base


def test_fingerprint_changes_when_parameter_changes() -> None:
    base = command_surface_fingerprint(_base_commands())

    def with_deals_parameter(parameter: _FakeParameter) -> list[_FakeCommand]:
        return _replace(
            _base_commands(),
            "deals",
            _FakeCommand(
                name="deals",
                description="Find deals",
                parameters=[parameter],
            ),
        )

    assert command_surface_fingerprint(
        with_deals_parameter(_FakeParameter(name="query", description="Search text", required=False))
    ) != base
    assert command_surface_fingerprint(
        with_deals_parameter(_FakeParameter(name="terms", description="Search text"))
    ) != base
    assert command_surface_fingerprint(
        with_deals_parameter(_FakeParameter(name="query", description="Different text"))
    ) != base
    assert command_surface_fingerprint(
        with_deals_parameter(
            _FakeParameter(
                name="query",
                description="Search text",
                choices=[_FakeChoice(name="Best", value="best")],
            )
        )
    ) != base
    assert command_surface_fingerprint(
        with_deals_parameter(
            _FakeParameter(name="query", description="Search text", min_value=1, max_value=10)
        )
    ) != base


def test_fingerprint_ignores_cosmetic_only_changes() -> None:
    base = command_surface_fingerprint(_base_commands())

    # Same surface rebuilt from fresh objects, in a different load order, with
    # different handler callables / docstrings / cog names attached.
    def _handler_a() -> None:
        pass

    def _handler_b() -> None:
        pass

    rebuilt: list[_FakeCommand] = []
    for command in reversed(_base_commands()):
        rebuilt.append(
            _FakeCommand(
                name=command.name,
                description=command.description,
                parameters=list(command.parameters),
                commands=list(command.commands),
                callback=_handler_b if command.name != "deals" else _handler_a,
                __doc__="A completely rewritten docstring",
            )
        )
    assert command_surface_fingerprint(rebuilt) == base

    # A description-only change is NOT cosmetic: Discord registers it.
    changed_description = _replace(
        _base_commands(),
        "hunt",
        _FakeCommand(name="hunt", description="Hunt deals now"),
    )
    assert command_surface_fingerprint(changed_description) != base


def test_hash_store_round_trip_and_missing_file(tmp_path: Path) -> None:
    store = CommandSurfaceHashStore(tmp_path / "nested" / "command_surface_hash.txt")
    assert store.read() is None
    store.write("abc123")
    assert store.read() == "abc123"
    assert default_hash_store_path(tmp_path / "data" / "sniperplug.sqlite3") == (
        tmp_path / "data" / "command_surface_hash.txt"
    )


# --- Boot decisions ----------------------------------------------------------


def test_first_boot_with_no_stored_hash_syncs_and_persists(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("CLEAR_STALE_GLOBAL_COMMANDS_ON_BOOT", raising=False)
    store = CommandSurfaceHashStore(tmp_path / "command_surface_hash.txt")
    tree = _FakeTree(commands=_base_commands())
    bot = _FakeBot(tree, store)

    _run(bot)

    assert tree.sync_calls == [None]
    assert store.read() == command_surface_fingerprint(_base_commands())


def test_second_boot_with_matching_hash_does_not_sync(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("CLEAR_STALE_GLOBAL_COMMANDS_ON_BOOT", raising=False)
    store = CommandSurfaceHashStore(tmp_path / "command_surface_hash.txt")
    tree = _FakeTree(commands=_base_commands())
    bot = _FakeBot(tree, store)

    _run(bot)
    assert tree.sync_calls == [None]
    _run(bot)

    assert tree.sync_calls == [None]
    assert store.read() == command_surface_fingerprint(_base_commands())


def test_changed_hash_syncs_once_and_persists_new_hash(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("CLEAR_STALE_GLOBAL_COMMANDS_ON_BOOT", raising=False)
    store = CommandSurfaceHashStore(tmp_path / "command_surface_hash.txt")
    tree = _FakeTree(commands=_base_commands())
    bot = _FakeBot(tree, store)
    _run(bot)
    assert tree.sync_calls == [None]

    tree._commands.append(
        _FakeCommand(name="check_deal", description="Check one product")
    )
    _run(bot)

    assert tree.sync_calls == [None, None]
    assert store.read() == command_surface_fingerprint(tree.get_commands())

    _run(bot)
    assert tree.sync_calls == [None, None]


def test_sync_failure_does_not_persist_and_next_boot_retries(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("CLEAR_STALE_GLOBAL_COMMANDS_ON_BOOT", raising=False)
    store = CommandSurfaceHashStore(tmp_path / "command_surface_hash.txt")
    tree = _FakeTree(commands=_base_commands(), sync_error=_discord_sync_error())
    bot = _FakeBot(tree, store)

    # Bot still starts: _sync_commands swallows the sync failure.
    _run(bot)

    assert tree.sync_calls == [None]
    assert store.read() is None

    tree.sync_error = None
    _run(bot)

    assert tree.sync_calls == [None, None]
    assert store.read() == command_surface_fingerprint(_base_commands())


def test_explicit_sync_on_boot_still_forces_sync_and_updates_hash(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("CLEAR_STALE_GLOBAL_COMMANDS_ON_BOOT", raising=False)
    store = CommandSurfaceHashStore(tmp_path / "command_surface_hash.txt")
    store.write("stale-hash")
    tree = _FakeTree(commands=_base_commands())
    bot = _FakeBot(tree, store, sync_on_boot=True)

    _run(bot)

    assert tree.sync_calls == [None]
    assert store.read() == command_surface_fingerprint(_base_commands())


def test_invalid_schema_still_blocks_sync_and_does_not_persist(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("CLEAR_STALE_GLOBAL_COMMANDS_ON_BOOT", raising=False)
    store = CommandSurfaceHashStore(tmp_path / "command_surface_hash.txt")
    tree = _FakeTree(
        commands=[
            *_base_commands(),
            _FakeCommand(name="broken", description="x" * 101),
        ]
    )
    bot = _FakeBot(tree, store)

    _run(bot)

    assert tree.sync_calls == []
    assert store.read() is None


def test_guild_sync_failure_does_not_persist(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("CLEAR_STALE_GLOBAL_COMMANDS_ON_BOOT", raising=False)
    store = CommandSurfaceHashStore(tmp_path / "command_surface_hash.txt")
    tree = _FakeTree(commands=_base_commands(), sync_error=_discord_sync_error())
    bot = _FakeBot(tree, store, sync_global=False, dev_guild_ids=(123,))

    _run(bot)

    assert tree.sync_calls == [123]
    assert store.read() is None


# --- Real loaded tree --------------------------------------------------------


@pytest.mark.asyncio
async def test_check_deal_present_in_loaded_command_tree_and_hash_syncs_once(
    tmp_path: Path, monkeypatch
) -> None:
    """Load all 20 canonical cogs against a scratch SQLite DB, no Discord.

    Background @tasks.loop cogs log wait_until_ready errors offline; those
    are expected noise here, not setup failures.
    """

    monkeypatch.delenv("CLEAR_STALE_GLOBAL_COMMANDS_ON_BOOT", raising=False)
    settings = Settings(
        discord_token="test-token",
        database_path=str(tmp_path / "sniperplug.sqlite3"),
        sync_commands_on_boot=False,
        sync_global_commands=True,
        dev_guild_ids=(),
    )
    sync_scopes: list[str] = []

    async def fake_sync_tree_safely(*, guild=None, scope: str):
        sync_scopes.append(scope)
        return []

    bot = SniperPlugBot(settings)
    bot._sync_tree_safely = fake_sync_tree_safely  # type: ignore[method-assign]
    try:
        await bot.setup_hook()

        names = {command.name for command in bot.tree.get_commands()}
        assert "check_deal" in names
        assert "deals" in names
        assert "hunt" in names
        assert len(names) >= 20

        # First boot with no stored hash syncs once and persists the hash.
        assert sync_scopes == ["global commands"]
        store = CommandSurfaceHashStore(
            default_hash_store_path(settings.database_path)
        )
        assert store.read() == command_surface_fingerprint(bot.tree.get_commands())
    finally:
        pending = [
            task for task in asyncio.all_tasks() if task is not asyncio.current_task()
        ]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        await bot.db.close()
        await bot.close()

    # Second boot with the same surface must not sync.
    bot2 = SniperPlugBot(settings)
    sync_scopes2: list[str] = []

    async def fake_sync_tree_safely2(*, guild=None, scope: str):
        sync_scopes2.append(scope)
        return []

    bot2._sync_tree_safely = fake_sync_tree_safely2  # type: ignore[method-assign]
    try:
        await bot2.setup_hook()
        assert sync_scopes2 == []
        assert "check_deal" in {
            command.name for command in bot2.tree.get_commands()
        }
    finally:
        pending = [
            task for task in asyncio.all_tasks() if task is not asyncio.current_task()
        ]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        await bot2.db.close()
        await bot2.close()
