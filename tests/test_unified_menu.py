"""Tests for the unified /menu: one menu for the whole bot.

The hub covers every live top-level command: Walmart reuses the existing
WalmartMenuView, and the other 49 functions are entries in MENU_ENTRIES
that delegate to the handler that owns the slash command. These tests pin
the wiring: every delegate target is a cog bot.py actually loads with its
method, every top-level command is reachable, every category fits
Discord's 25-option select limit, admin gates hold in the menu, and the
modal/confirm paths call the real handler.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import discord
from discord import app_commands

from sniperplug.cogs.unified_menu import (
    ENTRIES_BY_CATEGORY,
    MENU_CATEGORIES,
    MENU_DELEGATES,
    MENU_ENTRIES,
    UnifiedCategoryView,
    UnifiedMenuCog,
    UnifiedMenuView,
    _MenuModal,
    build_category_embed,
    build_unified_menu_embed,
)
from sniperplug.cogs.walmart_command import (
    MENU_FUNCTIONS,
    WalmartCommandCog,
    build_walmart_menu_embed,
)
from sniperplug.services.command_surface import REQUIRED_CANONICAL_COMMANDS


# The 21 top-level commands on the live bot (startup log on 7a6308c).
LIVE_TOP_LEVEL = {
    "clearance_bank",
    "ebay",
    "hd_stock",
    "home_depot_penny_hunt",
    "home_depot_search",
    "local_check",
    "movies",
    "movies-help",
    "movies-panel",
    "seed_clearance",
    "sniperplug",
    "sniperplug_dashboard",
    "storage_health",
    "storage_maintenance_now",
    "target_dm_location",
    "target_dm_location_clear",
    "target_location",
    "target_location_clear",
    "verizon",
    "walmart",
    "wishlist",
}


def test_menu_command_registers() -> None:
    # Plain Cog (not a GroupCog): commands live in __cog_app_commands__.
    names = {command.name for command in UnifiedMenuCog.__cog_app_commands__}
    assert names == {"menu"}


def test_menu_entries_are_well_formed() -> None:
    keys = [entry.key for entry in MENU_ENTRIES]
    assert len(keys) == len(set(keys)) == 49
    assert set(MENU_DELEGATES) == set(keys)
    for entry in MENU_ENTRIES:
        assert entry.mode in {"run", "modal", "choice", "channel", "special"}
        assert entry.label and entry.description and entry.usage
        assert len(entry.label) <= 100
        assert len(entry.description) <= 100
        if entry.mode == "modal":
            from sniperplug.cogs.unified_menu import _MODAL_FIELDS

            assert entry.key in _MODAL_FIELDS
        if entry.mode == "choice":
            assert entry.choice is not None
        if entry.mode == "channel":
            assert entry.channel_param is not None


def test_menu_covers_every_top_level_command() -> None:
    covered: set[str] = {"walmart"}  # Walmart category reuses the Walmart menu.
    for entry in MENU_ENTRIES:
        covered.add(entry.usage[1:].split()[0].strip())
    assert covered == LIVE_TOP_LEVEL


def test_every_category_fits_discord_select_limit() -> None:
    assert len(MENU_CATEGORIES) <= 25
    for key, _label, _desc in MENU_CATEGORIES:
        if key == "walmart":
            assert len(MENU_FUNCTIONS) <= 25
        else:
            assert 1 <= len(ENTRIES_BY_CATEGORY[key]) <= 25


def test_hub_view_has_category_select_and_quick_buttons() -> None:
    view = UnifiedMenuView(UnifiedMenuCog(bot=None))
    selects = [child for child in view.children if isinstance(child, discord.ui.Select)]
    assert len(selects) == 1
    assert {option.value for option in selects[0].options} == {key for key, _l, _d in MENU_CATEGORIES}
    buttons = [child for child in view.children if isinstance(child, discord.ui.Button)]
    assert {button.label for button in buttons} == {"Find deals", "Check a deal", "Add to wishlist"}


def test_menu_embeds_list_every_function() -> None:
    hub = build_unified_menu_embed()
    hub_text = (hub.title or "") + "\n" + "\n".join(
        (field.name or "") + "\n" + (field.value or "") for field in hub.fields
    )
    for _key, label, _desc in MENU_CATEGORIES:
        assert label in hub_text
    for key, entries in ENTRIES_BY_CATEGORY.items():
        embed = build_category_embed(key)
        text = (embed.title or "") + "\n" + "\n".join(
            (field.value or "") for field in embed.fields
        )
        for entry in entries:
            assert entry.usage in text


def test_every_menu_delegate_target_is_a_loaded_cog_with_its_method() -> None:
    """Same trap as /walmart check/find/cash: a delegate target must be a
    cog bot.py actually adds, carrying its method. Inherited methods count."""

    import ast
    import importlib
    from pathlib import Path

    bot_path = Path(__file__).resolve().parents[1] / "sniperplug" / "bot.py"
    tree = ast.parse(bot_path.read_text())
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

    assert "UnifiedMenuCog" in loaded
    assert "WalmartCommandCog" in loaded

    cog_modules = {
        "WishlistCog": "sniperplug.cogs.wishlist",
        "DmDealAlertsCog": "sniperplug.cogs.dm_deal_alerts",
        "CanonicalWorkflowCog": "sniperplug.cogs.canonical_workflow",
        "EbayResearchCog": "sniperplug.cogs.ebay_research",
        "HomeDepotSearchCog": "sniperplug.cogs.home_depot_search",
        "HomeDepotLocalCog": "sniperplug.cogs.home_depot_local",
        "LocalInventoryCog": "sniperplug.cogs.local_inventory",
        "ClearanceBankCog": "sniperplug.cogs.clearance_bank",
        "MovieTicketsCog": "sniperplug.cogs.registered_multi_source_movies",
        "MovieCommandGuideCog": "sniperplug.cogs.movie_command_guide",
        "VerizonShineCog": "sniperplug.cogs.verizon_shine",
        "CanonicalSettingsCog": "sniperplug.cogs.canonical_settings",
        "StorageAdminCog": "sniperplug.cogs.storage_admin",
        "SniperPlugCog": "sniperplug.cogs.sniperplug",
    }
    for key, (cog_name, method_name) in MENU_DELEGATES.items():
        assert cog_name in loaded, (
            f"menu {key} delegates to {cog_name}, which bot.py never loads"
        )
        module = importlib.import_module(cog_modules[cog_name])
        cls = getattr(module, cog_name)
        assert hasattr(cls, method_name), (
            f"{cog_name} has no {method_name} for menu {key}"
        )


def test_menu_is_a_required_canonical_command() -> None:
    assert "menu" in REQUIRED_CANONICAL_COMMANDS


# -- fakes ---------------------------------------------------------------

class _FakeResponse:
    def __init__(self) -> None:
        self.modal = None
        self.sent: list[tuple[tuple, dict]] = []
        self.edited: list[dict] = []

    def is_done(self) -> bool:
        return False

    async def defer(self, *, ephemeral: bool = False) -> None:
        return None

    async def send_modal(self, modal) -> None:
        self.modal = modal

    async def send_message(self, *args, **kwargs) -> None:
        self.sent.append((args, kwargs))

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


class _FakeInteraction:
    def __init__(self, *, manage_guild: bool = True) -> None:
        self.user = _FakeUser(manage_guild=manage_guild)
        self.guild_id = 456
        self.guild = SimpleNamespace(id=456)
        self.channel = None
        self.message = None
        self.response = _FakeResponse()
        self.followup = _FakeFollowup()


def _fake_cog_with_plain_method(calls: list, method_name: str):
    async def method(interaction, *args, **kwargs):
        calls.append((args, kwargs))

    return SimpleNamespace(**{method_name: method})


def _fake_cog_with_callback(calls: list, method_name: str):
    async def callback(cog, interaction, *args, **kwargs):
        calls.append((args, kwargs))

    return SimpleNamespace(**{method_name: SimpleNamespace(callback=callback)})


@pytest.mark.asyncio
async def test_run_entry_blocks_admin_functions_without_manage_server() -> None:
    calls: list = []
    bot = SimpleNamespace(
        get_cog=lambda name: _fake_cog_with_plain_method(calls, "storage_health")
        if name == "StorageAdminCog"
        else None
    )
    cog = UnifiedMenuCog(bot=bot)
    interaction = _FakeInteraction(manage_guild=False)
    await cog.run_entry(interaction, "storage-health")
    assert calls == []
    assert interaction.response.sent
    assert "Manage Server" in str(interaction.response.sent[0])


@pytest.mark.asyncio
async def test_run_entry_delegates_plain_method() -> None:
    calls: list = []
    bot = SimpleNamespace(
        get_cog=lambda name: _fake_cog_with_plain_method(calls, "digest")
        if name == "VerizonShineCog"
        else None
    )
    cog = UnifiedMenuCog(bot=bot)
    interaction = _FakeInteraction()
    await cog.run_entry(interaction, "verizon-digest")
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_run_entry_delegates_command_callback() -> None:
    calls: list = []
    bot = SimpleNamespace(
        get_cog=lambda name: _fake_cog_with_callback(calls, "status")
        if name == "MovieTicketsCog"
        else None
    )
    cog = UnifiedMenuCog(bot=bot)
    interaction = _FakeInteraction()
    await cog.run_entry(interaction, "movies-status")
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_run_entry_missing_cog_sends_loaded_error() -> None:
    bot = SimpleNamespace(get_cog=lambda name: None)
    cog = UnifiedMenuCog(bot=bot)
    interaction = _FakeInteraction()
    await cog.run_entry(interaction, "verizon-digest")
    assert interaction.response.sent
    assert "isn't loaded" in str(interaction.response.sent[0])


@pytest.mark.asyncio
async def test_my_alerts_delegates_with_menu_action() -> None:
    calls: list = []
    bot = SimpleNamespace(
        get_cog=lambda name: _fake_cog_with_callback(calls, "dm_deals")
        if name == "DmDealAlertsCog"
        else None
    )
    cog = UnifiedMenuCog(bot=bot)
    interaction = _FakeInteraction()
    await cog.run_entry(interaction, "my-alerts")
    assert len(calls) == 1
    _, kwargs = calls[0]
    assert kwargs["action"].value == "menu"


@pytest.mark.asyncio
async def test_channel_entry_needs_a_server_text_channel() -> None:
    bot = SimpleNamespace(get_cog=lambda name: None)
    cog = UnifiedMenuCog(bot=bot)
    interaction = _FakeInteraction()
    interaction.channel = None
    await cog.run_entry(interaction, "movies-setup")
    assert interaction.response.sent
    assert "server text channel" in str(interaction.response.sent[0])


@pytest.mark.asyncio
async def test_modal_submit_calls_handler_with_values() -> None:
    calls: list = []
    bot = SimpleNamespace(
        get_cog=lambda name: _fake_cog_with_plain_method(calls, "add")
        if name == "WishlistCog"
        else None
    )
    cog = UnifiedMenuCog(bot=bot)
    modal = _MenuModal(cog, "wishlist-add")
    modal.children[0]._value = "iphone 11"
    interaction = _FakeInteraction()
    await modal.on_submit(interaction)
    assert calls == [((), {"item": "iphone 11"})]


@pytest.mark.asyncio
async def test_modal_rejects_bad_price() -> None:
    calls: list = []
    bot = SimpleNamespace(
        get_cog=lambda name: _fake_cog_with_plain_method(calls, "local_check")
        if name == "LocalInventoryCog"
        else None
    )
    cog = UnifiedMenuCog(bot=bot)
    modal = _MenuModal(cog, "local-check")
    # Fields are added in _MODAL_FIELDS order: retailer, sku, zip, price.
    modal.children[0]._value = "walmart"
    modal.children[3]._value = "abc"
    interaction = _FakeInteraction()
    await modal.on_submit(interaction)
    assert calls == []
    assert interaction.response.sent
    assert "doesn't look like a number" in str(interaction.response.sent[0])


@pytest.mark.asyncio
async def test_modal_parses_price_and_passes_optional_fields() -> None:
    calls: list = []
    bot = SimpleNamespace(
        get_cog=lambda name: _fake_cog_with_plain_method(calls, "local_check")
        if name == "LocalInventoryCog"
        else None
    )
    cog = UnifiedMenuCog(bot=bot)
    modal = _MenuModal(cog, "local-check")
    # Fields are added in _MODAL_FIELDS order: retailer, sku, zip, price.
    modal.children[0]._value = "walmart"
    modal.children[1]._value = "123"
    modal.children[3]._value = "49.00"
    interaction = _FakeInteraction()
    await modal.on_submit(interaction)
    assert len(calls) == 1
    _, kwargs = calls[0]
    assert kwargs["retailer"] == "walmart"
    assert kwargs["sku"] == "123"
    assert kwargs["observed_price"] == 49.0


def test_ebay_cooldown_allows_two_then_blocks() -> None:
    cog = UnifiedMenuCog(bot=None)
    assert cog._check_cooldown("ebay-research", 123)[0] is True
    assert cog._check_cooldown("ebay-research", 123)[0] is True
    allowed, retry_after = cog._check_cooldown("ebay-research", 123)
    assert allowed is False
    assert retry_after is not None and retry_after > 0
    assert cog._check_cooldown("ebay-research", 999)[0] is True


@pytest.mark.asyncio
async def test_walmart_category_opens_existing_menu() -> None:
    walmart_cog = WalmartCommandCog(bot=None)
    bot = SimpleNamespace(get_cog=lambda name: walmart_cog if name == "WalmartCommandCog" else None)
    cog = UnifiedMenuCog(bot=bot)
    interaction = _FakeInteraction()
    await cog.open_category(interaction, "walmart")
    assert len(interaction.response.edited) == 1
    embed = interaction.response.edited[0]["embed"]
    assert "Walmart" in (embed.title or "")
    view = interaction.response.edited[0]["view"]
    labels = {child.label for child in view.children if isinstance(child, discord.ui.Button)}
    assert "← Back to menu" in labels


@pytest.mark.asyncio
async def test_back_button_returns_to_hub() -> None:
    cog = UnifiedMenuCog(bot=None)
    view = UnifiedCategoryView(cog, "my-deals")
    back = next(
        child for child in view.children
        if isinstance(child, discord.ui.Button) and child.label == "← Back to menu"
    )
    interaction = _FakeInteraction()
    await back.callback(interaction)
    assert len(interaction.response.edited) == 1
    embed = interaction.response.edited[0]["embed"]
    assert (embed.title or "") == "📋 SniperPlug menu"


@pytest.mark.asyncio
async def test_menu_command_sends_hub() -> None:
    cog = UnifiedMenuCog(bot=None)
    interaction = _FakeInteraction()
    await cog.menu.callback(cog, interaction)
    assert len(interaction.response.sent) == 1
    _args, kwargs = interaction.response.sent[0]
    assert (kwargs["embed"].title or "") == "📋 SniperPlug menu"
    assert isinstance(kwargs["view"], UnifiedMenuView)


@pytest.mark.asyncio
async def test_confirm_no_keeps_everything() -> None:
    calls: list = []
    bot = SimpleNamespace(
        get_cog=lambda name: _fake_cog_with_plain_method(calls, "clear")
        if name == "WishlistCog"
        else None
    )
    cog = UnifiedMenuCog(bot=bot)
    interaction = _FakeInteraction()
    await cog.run_entry(interaction, "wishlist-clear")
    assert len(interaction.response.edited) == 1
    view = interaction.response.edited[0]["view"]
    no_button = next(
        child for child in view.children
        if isinstance(child, discord.ui.Button) and child.label == "Keep it"
    )
    await no_button.callback(interaction)
    assert calls == []
    assert len(interaction.response.edited) == 2
    assert "nothing changed" in (interaction.response.edited[1]["embed"].description or "")
