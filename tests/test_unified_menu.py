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


# Consolidation PR 2 (2026-11-19): the live tree is exactly 7
# top-levels. The /menu hub itself is the entry point, so coverage is
# asserted for the other six: every one of them must be reachable from
# the menu (directly, or — for Walmart — through the Walmart menu).
LIVE_TOP_LEVEL = {
    "ebay",
    "movies",
    "staff",
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
        if entry.usage.startswith("/"):
            covered.add(entry.usage[1:].split()[0].strip())
    assert covered == LIVE_TOP_LEVEL


def test_every_category_fits_discord_select_limit() -> None:
    assert len(MENU_CATEGORIES) <= 25
    for key, _label, _desc in MENU_CATEGORIES:
        if key == "walmart":
            assert len(MENU_FUNCTIONS) <= 25
        elif key == "hp-store":
            # Dedicated panel like Walmart: no function entries at all.
            assert key not in ENTRIES_BY_CATEGORY
        else:
            assert 1 <= len(ENTRIES_BY_CATEGORY[key]) <= 25


def test_hub_view_has_category_select_and_quick_buttons() -> None:
    view = UnifiedMenuView(UnifiedMenuCog(bot=None))
    selects = [child for child in view.children if isinstance(child, discord.ui.Select)]
    assert len(selects) == 1
    assert selects[0].placeholder == "More…"
    assert {option.value for option in selects[0].options} == {key for key, _l, _d in MENU_CATEGORIES}
    buttons = [child for child in view.children if isinstance(child, discord.ui.Button)]
    assert {button.label for button in buttons} == {"Find deals", "Browse", "Add to wishlist", "Check a link"}


def test_menu_embeds_list_every_function() -> None:
    # The home screen no longer lists categories as embed fields; the
    # categories live in the view's More… select options.
    view = UnifiedMenuView(UnifiedMenuCog(bot=None))
    select = next(child for child in view.children if isinstance(child, discord.ui.Select))
    option_labels = {option.label for option in select.options}
    for _key, label, _desc in MENU_CATEGORIES:
        assert label in option_labels
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
        "DmListIntakeCog": "sniperplug.cogs.dm_list_intake",
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
        self.calls: list[str] = []
        self._done = False

    def is_done(self) -> bool:
        return self._done

    async def defer(self, *, ephemeral: bool = False) -> None:
        self.calls.append("defer")
        self._done = True

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

    async def edit_original_response(self, **kwargs) -> None:
        self.response.edited.append(kwargs)


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
    # The modal now also carries the optional price/size fields; left
    # blank they reach the handler as None (no target, no size).
    assert calls == [((), {"item": "iphone 11", "target_price": None, "size": None})]


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
    assert (embed.title or "") == "SniperPlug"


@pytest.mark.asyncio
async def test_menu_command_sends_hub() -> None:
    cog = UnifiedMenuCog(bot=None)
    interaction = _FakeInteraction()
    await cog.menu.callback(cog, interaction)
    # Defer-first: the dashboard snapshot loads from Turso, which can take
    # longer than Discord's 3s interaction window (10062 live 2026-11-17).
    assert interaction.response.calls == ["defer"]
    assert len(interaction.followup.sent) == 1
    _args, kwargs = interaction.followup.sent[0]
    assert (kwargs["embed"].title or "") == "SniperPlug"
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


# -- menu dashboard (redesign chunk 2, fail-first) ------------------------

from sniperplug.services.menu_dashboard_state import MenuDashboardState


def _fake_snapshot(**overrides):
    values = dict(
        state_available=True,
        provider_status="READY",
        provider_message="Ready: Walmart Affiliate API credentials are configured.",
        threshold_percent=40,
        extreme_enabled=True,
        extreme_recent=3,
        delivery_ready=True,
        delivery_summary="Walmart delivery ready.",
        dm_enabled=True,
        dm_threshold=20,
        dm_daily_cap=5,
        wishlist_count=7,
        wishlist_cap=20,
        active_deals=[
            {"title": "Dyson V15 vacuum", "price": 299.0, "discount": 50},
            {"title": "LEGO Star Wars set", "price": 49.99, "discount": 44},
        ],
        active_deal_count=12,
        deals_today=4,
    )
    values.update(overrides)
    return MenuDashboardState(**values)


def _embed_text(embed) -> str:
    parts = [embed.title or "", embed.description or ""]
    parts += [(f.name or "") + "\n" + (f.value or "") for f in embed.fields]
    parts.append(embed.footer.text or "")
    return "\n".join(parts)


def test_hub_embed_status_line_shows_live_values() -> None:
    hub = build_unified_menu_embed(_fake_snapshot())
    text = _embed_text(hub)
    assert hub.color == discord.Color.orange()
    # The plan's one status line: working, deals today, your alerts.
    assert "🟢 Working · 4 deals today · Your alerts: **ON**" in text
    # The old strip's mechanism copy and data fields are gone from home.
    assert "Provider" not in text
    assert "Extreme" not in text
    assert "7/20" not in text
    assert "Dyson V15 vacuum" not in text


def test_hub_embed_deals_today_none_falls_back_to_active_count() -> None:
    hub = build_unified_menu_embed(_fake_snapshot(deals_today=None))
    text = _embed_text(hub)
    assert "12 active deals" in text
    assert "showing active deals instead" not in text


def test_hub_embed_has_no_category_fields() -> None:
    hub = build_unified_menu_embed(_fake_snapshot())
    assert hub.fields == []
    assert (hub.title or "") == "SniperPlug"
    assert "Find deals. Check links. Get alerts." in (hub.description or "")


def test_category_embed_uses_accent_color_and_live_summary() -> None:
    # "walmart" has no ENTRIES_BY_CATEGORY entry — it reuses the Walmart
    # menu — so its summary is checked via _category_summary, not the
    # category embed (build_category_embed("walmart") is a KeyError on
    # main too, by design).
    from sniperplug.cogs.unified_menu import CATEGORY_COLORS, _category_summary

    assert CATEGORY_COLORS["walmart"] == 0x0071DC  # Walmart blue
    walmart_summary = _category_summary(_fake_snapshot(), "walmart")
    assert "40% off or better" in walmart_summary
    assert "12 active deals" in walmart_summary
    assert "delivery ready" in walmart_summary
    embed = build_category_embed("my-deals", _fake_snapshot())
    assert embed.color != discord.Color.orange()
    assert embed.color.value == 0x9B59B6  # my-deals purple
    text = _embed_text(embed)
    assert "7/20" in text
    for entry in ENTRIES_BY_CATEGORY["my-deals"]:
        assert entry.usage in text
    other = build_category_embed("stores", _fake_snapshot())
    assert other.color != discord.Color.orange()
    assert other.color.value == 0x1ABC9C  # stores teal
    # The HP/Target watcher sentence is deleted from member copy.
    assert "watchers not running" not in _embed_text(other)


def test_state_unavailable_renders_fallback_with_function_list_intact() -> None:
    unavailable = MenuDashboardState(state_available=False)
    hub_text = _embed_text(build_unified_menu_embed(unavailable))
    assert "Live state unavailable — the function list still works." in hub_text
    # Categories moved to the view's More… select; they survive a
    # failed state read exactly like the function list does.
    view = UnifiedMenuView(UnifiedMenuCog(bot=None))
    select = next(child for child in view.children if isinstance(child, discord.ui.Select))
    option_labels = {option.label for option in select.options}
    for _key, label, _desc in MENU_CATEGORIES:
        assert label in option_labels
    embed = build_category_embed("my-deals", unavailable)
    text = _embed_text(embed)
    assert "Live state unavailable — the function list still works." in text
    for entry in ENTRIES_BY_CATEGORY["my-deals"]:
        assert entry.usage in text


def test_category_view_has_refresh_button() -> None:
    cog = UnifiedMenuCog(bot=None)
    view = UnifiedCategoryView(cog, "my-deals", _fake_snapshot())
    buttons = [c for c in view.children if isinstance(c, discord.ui.Button)]
    assert {b.label for b in buttons} == {"← Back to menu", "🔄 Refresh"}
    selects = [c for c in view.children if isinstance(c, discord.ui.Select)]
    assert len(selects) == 1


@pytest.mark.asyncio
async def test_refresh_button_re_reads_state(monkeypatch) -> None:
    import sniperplug.cogs.unified_menu as um

    fresh = _fake_snapshot(wishlist_count=19, dm_enabled=False, dm_threshold=None)

    async def _fake_load(bot, guild_id, user_id):
        return fresh

    monkeypatch.setattr(um, "load_menu_dashboard_state", _fake_load)
    cog = UnifiedMenuCog(bot=None)
    view = UnifiedCategoryView(cog, "my-deals", _fake_snapshot())
    refresh = next(
        c for c in view.children
        if isinstance(c, discord.ui.Button) and c.label == "🔄 Refresh"
    )
    interaction = _FakeInteraction()
    await refresh.callback(interaction)
    assert len(interaction.response.edited) == 1
    embed = interaction.response.edited[0]["embed"]
    assert "19/20" in _embed_text(embed)
    assert "Your alerts: OFF" in _embed_text(embed)
    assert isinstance(interaction.response.edited[0]["view"], UnifiedCategoryView)


# -- defer-first (Unknown interaction 10062 fix, live 2026-11-17) --------

# Every menu path that loads the dashboard snapshot must answer Discord
# BEFORE the load: the snapshot reads Turso, and prod lock waits of 2.5-7.5s
# blow past the 3s interaction window, so /menu died with 10062 twice and
# /walmart menu once. These tests pin the order: defer, then load.


@pytest.mark.asyncio
async def test_menu_command_defers_before_loading_state(monkeypatch) -> None:
    import sniperplug.cogs.unified_menu as um

    interaction = _FakeInteraction()

    async def _fake_load(bot, guild_id, user_id):
        interaction.response.calls.append("load")
        return _fake_snapshot()

    monkeypatch.setattr(um, "load_menu_dashboard_state", _fake_load)
    cog = UnifiedMenuCog(bot=None)
    await cog.menu.callback(cog, interaction)
    assert interaction.response.calls == ["defer", "load"]
    assert len(interaction.followup.sent) == 1


@pytest.mark.asyncio
async def test_open_category_defers_before_loading_state(monkeypatch) -> None:
    import sniperplug.cogs.unified_menu as um

    interaction = _FakeInteraction()

    async def _fake_load(bot, guild_id, user_id):
        interaction.response.calls.append("load")
        return _fake_snapshot()

    monkeypatch.setattr(um, "load_menu_dashboard_state", _fake_load)
    cog = UnifiedMenuCog(bot=None)
    await cog.open_category(interaction, "my-deals")
    assert interaction.response.calls == ["defer", "load"]
    assert len(interaction.response.edited) == 1


@pytest.mark.asyncio
async def test_back_button_defers_before_loading_state(monkeypatch) -> None:
    import sniperplug.cogs.unified_menu as um

    interaction = _FakeInteraction()

    async def _fake_load(bot, guild_id, user_id):
        interaction.response.calls.append("load")
        return _fake_snapshot()

    monkeypatch.setattr(um, "load_menu_dashboard_state", _fake_load)
    cog = UnifiedMenuCog(bot=None)
    view = UnifiedCategoryView(cog, "my-deals")
    back = next(
        child for child in view.children
        if isinstance(child, discord.ui.Button) and child.label == "← Back to menu"
    )
    await back.callback(interaction)
    assert interaction.response.calls == ["defer", "load"]
    assert len(interaction.response.edited) == 1


@pytest.mark.asyncio
async def test_refresh_button_defers_before_loading_state(monkeypatch) -> None:
    import sniperplug.cogs.unified_menu as um

    interaction = _FakeInteraction()

    async def _fake_load(bot, guild_id, user_id):
        interaction.response.calls.append("load")
        return _fake_snapshot()

    monkeypatch.setattr(um, "load_menu_dashboard_state", _fake_load)
    cog = UnifiedMenuCog(bot=None)
    view = UnifiedCategoryView(cog, "my-deals", _fake_snapshot())
    refresh = next(
        c for c in view.children
        if isinstance(c, discord.ui.Button) and c.label == "🔄 Refresh"
    )
    await refresh.callback(interaction)
    assert interaction.response.calls == ["defer", "load"]
    assert len(interaction.response.edited) == 1


def test_every_new_control_resolves_to_a_real_handler() -> None:
    cog = UnifiedMenuCog(bot=None)
    for view in (
        UnifiedMenuView(cog),
        UnifiedCategoryView(cog, "my-deals", _fake_snapshot()),
    ):
        for child in view.children:
            assert callable(getattr(child, "callback", None))
            # Must be a real override, not the base discord.ui no-op.
            assert type(child).callback is not discord.ui.Button.callback
            assert type(child).callback is not discord.ui.Select.callback


# -- HP Store panel (dedicated category, like Walmart) --------------------

from sniperplug.cogs.unified_menu import (
    CATEGORY_COLORS,
    DEDICATED_PANEL_CATEGORIES,
    HPMenuView,
    HPPanelState,
    build_hp_panel_embed,
)
from sniperplug.services.hp_watcher_health import HPWatcherHealth


def _fake_hp_health(**overrides) -> HPWatcherHealth:
    values = dict(
        status="healthy",
        last_successful_cycle_at="2026-11-20T20:55:00+00:00",
        products=4563,
        identified_products=4278,
        active_markdowns=3,
        due_product_pages=45,
        due_offers=12,
        pending_events=1,
        sitemap_failures=0,
        product_failures=0,
        stale=False,
        last_error="",
    )
    values.update(overrides)
    return HPWatcherHealth(**values)


def _fake_hp_config(**overrides) -> dict:
    values = {
        "enabled": True,
        "retailers": ("walmart", "hp"),
        "channel_id": 1518804442718146560,
    }
    values.update(overrides)
    return values


def _ready_hp_state(**overrides) -> HPPanelState:
    values = dict(
        health=_fake_hp_health(),
        config=_fake_hp_config(),
        channel_status="✅ <#1518804442718146560> is saved and sendable.",
        setup_needs_attention=False,
    )
    values.update(overrides)
    return HPPanelState(**values)


def test_hp_store_category_is_registered_as_a_dedicated_panel() -> None:
    keys = [key for key, _label, _desc in MENU_CATEGORIES]
    assert "hp-store" in keys
    label = dict((key, label) for key, label, _d in MENU_CATEGORIES)["hp-store"]
    assert label == "💻 HP Store"
    assert "hp-store" in DEDICATED_PANEL_CATEGORIES
    assert "hp-store" not in ENTRIES_BY_CATEGORY
    assert CATEGORY_COLORS["hp-store"] == 0x0096D6  # HP blue
    # The hub's More… select offers it like every other category.
    view = UnifiedMenuView(UnifiedMenuCog(bot=None))
    select = next(child for child in view.children if isinstance(child, discord.ui.Select))
    assert "hp-store" in {option.value for option in select.options}


def test_hp_panel_embed_healthy_shows_coverage_and_delivery() -> None:
    embed = build_hp_panel_embed(_ready_hp_state())
    assert (embed.title or "") == "💻 HP Store"
    assert embed.color.value == 0x0096D6
    text = _embed_text(embed)
    assert "🟢 **Working**" in text
    assert "4,563" in text and "4,278" in text  # catalog / identified
    assert "**12** price checks due" in text
    assert "**3** marked down right now" in text
    assert "**1** deals waiting to post" in text
    assert "HP deals will post to <#1518804442718146560>" in text
    assert "get a DM too" in text
    # No internal vocabulary leaks into member copy.
    for jargon in ("fanout", "retailer_verified", "1795025712460"):
        assert jargon not in text


def test_hp_panel_embed_stale_watcher_says_gone_quiet() -> None:
    state = _ready_hp_state(health=_fake_hp_health(stale=True))
    text = _embed_text(build_hp_panel_embed(state))
    assert "🟠 **Gone quiet**" in text
    assert "Last successful scan:" in text


def test_hp_panel_embed_watcher_missing_states() -> None:
    not_started = _ready_hp_state(health=_fake_hp_health(status="not_started", stale=True))
    text = _embed_text(build_hp_panel_embed(not_started))
    assert "⏸️ **Not running**" in text

    unreadable = HPPanelState()
    text = _embed_text(build_hp_panel_embed(unreadable))
    assert "Couldn't read the watcher's status" in text
    assert "Couldn't read this server's alert settings" in text


def test_hp_panel_embed_delivery_reflects_config() -> None:
    off = _ready_hp_state(config=_fake_hp_config(enabled=False))
    text = _embed_text(build_hp_panel_embed(off))
    assert "Public deal alerts are **off**" in text
    assert "HP deals will post" not in text

    not_picked = _ready_hp_state(config=_fake_hp_config(retailers=("walmart",)))
    text = _embed_text(build_hp_panel_embed(not_picked))
    assert "HP Store isn't one of the stores picked" in text
    assert "HP deals will post" not in text

    broken_channel = _ready_hp_state(
        channel_status="⛔ <#1518804442718146560> is saved, but bot is missing: Send Messages."
    )
    text = _embed_text(build_hp_panel_embed(broken_channel))
    assert "posting isn't ready yet" in text
    assert "bot is missing: Send Messages" in text
    # The settings pointer is always present.
    assert "/staff channel" in text
    assert "/autoscan_health" in text


def test_hp_panel_view_has_refresh_and_back_with_real_handlers() -> None:
    cog = UnifiedMenuCog(bot=None)
    view = HPMenuView(cog, _ready_hp_state())
    buttons = [c for c in view.children if isinstance(c, discord.ui.Button)]
    assert {b.label for b in buttons} == {"← Back to menu", "🔄 Refresh"}
    for child in view.children:
        assert callable(getattr(child, "callback", None))
        assert type(child).callback is not discord.ui.Button.callback


@pytest.mark.asyncio
async def test_hp_category_opens_dedicated_panel(monkeypatch) -> None:
    import sniperplug.cogs.unified_menu as um

    interaction = _FakeInteraction()

    async def _fake_panel(_interaction):
        interaction.response.calls.append("hp-load")
        return _ready_hp_state()

    cog = UnifiedMenuCog(bot=None)
    monkeypatch.setattr(cog, "_load_hp_panel", _fake_panel)
    await cog.open_category(interaction, "hp-store")
    # Defer-first, like every other dashboard path (10062 rule).
    assert interaction.response.calls[0] == "defer"
    assert "hp-load" in interaction.response.calls
    assert len(interaction.response.edited) == 1
    embed = interaction.response.edited[0]["embed"]
    assert "HP Store" in (embed.title or "")
    view = interaction.response.edited[0]["view"]
    assert isinstance(view, HPMenuView)
    labels = {child.label for child in view.children if isinstance(child, discord.ui.Button)}
    assert labels == {"← Back to menu", "🔄 Refresh"}
    assert um.HPMenuView is HPMenuView


@pytest.mark.asyncio
async def test_hp_refresh_button_re_reads_and_re_renders(monkeypatch) -> None:
    interaction = _FakeInteraction()
    fresh = _ready_hp_state(health=_fake_hp_health(identified_products=4500))

    cog = UnifiedMenuCog(bot=None)

    async def _fake_panel(_interaction):
        interaction.response.calls.append("hp-load")
        return fresh

    monkeypatch.setattr(cog, "_load_hp_panel", _fake_panel)
    view = HPMenuView(cog, _ready_hp_state())
    refresh = next(
        c for c in view.children
        if isinstance(c, discord.ui.Button) and c.label == "🔄 Refresh"
    )
    await refresh.callback(interaction)
    assert interaction.response.calls == ["defer", "hp-load"]
    assert len(interaction.response.edited) == 1
    embed = interaction.response.edited[0]["embed"]
    assert "4,500" in _embed_text(embed)
    assert isinstance(interaction.response.edited[0]["view"], HPMenuView)


@pytest.mark.asyncio
async def test_hp_back_button_returns_to_hub() -> None:
    cog = UnifiedMenuCog(bot=None)
    view = HPMenuView(cog, _ready_hp_state())
    back = next(
        child for child in view.children
        if isinstance(child, discord.ui.Button) and child.label == "← Back to menu"
    )
    interaction = _FakeInteraction()
    await back.callback(interaction)
    assert len(interaction.response.edited) == 1
    embed = interaction.response.edited[0]["embed"]
    assert (embed.title or "") == "SniperPlug"
