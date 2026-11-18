"""Tests for the /walmart menu dashboard and its inline controls.

Chunk 3 of the SniperPlug menu redesign (2026-11-17): only
``sniperplug/cogs/walmart_command.py`` is under test here — the dashboard
builder ``build_walmart_menu_embed(state)`` and the inline controls on
``WalmartMenuView`` (threshold stepper, extreme toggle, DM-alerts toggle,
Refresh). The state snapshot itself is chunk 1's contract and is tested in
``tests/test_menu_dashboard.py``.

Write paths under test (all verified in code, not guessed):

* Threshold: ``set_starting_deal_percent(db, guild_id, new)``
  (``services/deal_threshold_settings.py``); the stepper clamps 10-90
  before writing, the setter's own normalizer clamps 0-95.
* Extreme: ``set_extreme_alerts_enabled(db, guild_id, enabled)``
  (``services/walmart_extreme_price_fanout.py``).
* DM alerts: the same path ``/walmart my-alerts`` enable/disable uses —
  ``get_dm_deal_alert_preference(db, user_id)`` then
  ``save_dm_deal_alert_preference(db, replace(preference, enabled=...))``.
"""

from __future__ import annotations

import asyncio
import inspect
from dataclasses import replace
from types import SimpleNamespace

import aiosqlite
import discord
import pytest

from sniperplug.cogs.walmart_command import (
    MENU_MEMBER_KEYS,
    MENU_MEMBER_SECTIONS,
    MENU_REMOVED_KEYS,
    MENU_STAFF_KEYS,
    MENU_STAFF_SECTIONS,
    WALMART_SUBCOMMANDS,
    WalmartCommandCog,
    WalmartMenuView,
    build_walmart_menu_embed,
)
from sniperplug.providers.base import ProviderHealth, ProviderStatus
from sniperplug.services import menu_dashboard_state as mds
from sniperplug.services.deal_threshold_settings import (
    get_starting_deal_percent,
    set_starting_deal_percent,
)
from sniperplug.services.dm_deal_alerts import (
    DmDealAlertPreference,
    get_dm_deal_alert_preference,
    save_dm_deal_alert_preference,
)
from sniperplug.services.menu_dashboard_state import (
    MenuDashboardState,
    load_menu_dashboard_state,
)
from sniperplug.services.walmart_extreme_price_fanout import (
    extreme_alerts_enabled,
    set_extreme_alerts_enabled,
)


GUILD = 123456789
USER = 987654321
OTHER_USER = 111222333


@pytest.fixture(autouse=True)
def _reset_schema_ready_flags():
    """DM prefs and wishlist guard table setup with a process-wide flag.

    Every test here uses a fresh in-memory db, so the flag must be reset
    per test or the second test's ensure would skip creating its tables
    (same pattern as tests/test_menu_dashboard.py).
    """
    import sniperplug.services.dm_deal_alerts as dm_alerts
    import sniperplug.services.user_wishlist as wishlist

    dm_alerts._SCHEMA_READY = False
    wishlist._SCHEMA_READY = False
    yield
    dm_alerts._SCHEMA_READY = False
    wishlist._SCHEMA_READY = False


_OPEN_CONNECTIONS: list = []


@pytest.fixture(autouse=True)
def _close_db_connections():
    """Close every in-memory connection opened by the test.

    aiosqlite 0.22.1 spawns a NON-daemon worker thread per connection, so
    an unclosed connection hangs interpreter shutdown forever (the tests
    themselves finish; the process never exits). Closing here keeps the
    run's exit clean.
    """
    yield
    for conn in list(_OPEN_CONNECTIONS):
        try:
            asyncio.run(conn.close())
        except Exception:
            pass
    _OPEN_CONNECTIONS.clear()


class _FakeProvider:
    def __init__(self, status: ProviderStatus = ProviderStatus.READY) -> None:
        self._health = ProviderHealth(
            provider_key="walmart",
            ok=status is ProviderStatus.READY,
            status=status,
            message="Ready: Walmart Affiliate API credentials are configured.",
        )

    async def healthcheck(self) -> ProviderHealth:
        return self._health


async def _open_db():
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    db = SimpleNamespace(require_conn=lambda: conn)
    _OPEN_CONNECTIONS.append(conn)
    return db, conn


def _fake_bot(db, provider=None):
    return SimpleNamespace(
        db=db,
        walmart_provider=provider if provider is not None else _FakeProvider(),
        get_guild=lambda guild_id: None,
    )


class _FakeResponse:
    def __init__(self) -> None:
        self.sent_message = None
        self.edited: list[dict] = []
        self.calls: list[str] = []
        self._done = False

    def is_done(self) -> bool:
        return self._done

    async def defer(self, *, ephemeral: bool = False) -> None:
        self.calls.append("defer")
        self._done = True

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
    def __init__(self, *, user_id: int = USER, manage_guild: bool = True) -> None:
        self.id = user_id
        self.guild_permissions = SimpleNamespace(manage_guild=manage_guild)


class _FakeInteraction:
    def __init__(self, *, user_id: int = USER, manage_guild: bool = True) -> None:
        self.user = _FakeUser(user_id=user_id, manage_guild=manage_guild)
        self.guild_id = GUILD
        self.response = _FakeResponse()
        self.followup = _FakeFollowup()

    async def edit_original_response(self, **kwargs) -> None:
        self.response.edited.append(kwargs)


def _embed_text(embed: discord.Embed) -> str:
    parts = [embed.title or "", embed.description or ""]
    parts.extend((field.name or "") + "\n" + (field.value or "") for field in embed.fields)
    parts.append(embed.footer.text or "")
    return "\n".join(parts)


def _buttons(view: WalmartMenuView) -> dict[str, discord.ui.Button]:
    return {
        button.label: button
        for button in view.children
        if isinstance(button, discord.ui.Button)
    }


async def _load_state(bot, user_id: int = USER) -> MenuDashboardState:
    return await load_menu_dashboard_state(bot, GUILD, user_id)


async def _make_view(
    bot, *, state=None, user_id: int = USER, manage_guild: bool = True, can_check: bool = True
) -> WalmartMenuView:
    cog = WalmartCommandCog(bot=bot)
    if state is None:
        state = await _load_state(bot, user_id)
    return WalmartMenuView(cog, state, manage_guild=manage_guild, can_check=can_check)


def _edited_embed(interaction: _FakeInteraction) -> discord.Embed:
    assert len(interaction.response.edited) == 1
    return interaction.response.edited[0]["embed"]


# -- view surface --------------------------------------------------------


def test_view_contains_stepper_both_toggles_and_refresh() -> None:
    view = WalmartMenuView(
        WalmartCommandCog(bot=None), manage_guild=True, can_check=True
    )
    labels = _buttons(view)
    assert {"−5%", "40%", "+5%"} <= set(labels)
    assert "Extreme: OFF" in labels or "Extreme: ON" in labels
    assert "DM alerts: OFF" in labels or "DM alerts: ON" in labels
    assert "Refresh" in labels
    # The staff select and the four member buttons are still there.
    selects = [child for child in view.children if isinstance(child, discord.ui.Select)]
    assert len(selects) == 1
    assert {option.value for option in selects[0].options} == set(MENU_STAFF_KEYS)
    assert {"Check a deal", "Find deals", "Hunt deals", "My alerts"} <= set(labels)
    assert view.timeout == 300


def test_every_new_control_resolves_to_a_real_handler_method() -> None:
    view = WalmartMenuView(
        WalmartCommandCog(bot=None), manage_guild=True, can_check=True
    )
    labels = _buttons(view)
    for label in ("−5%", "40%", "+5%", "Extreme: OFF", "DM alerts: OFF", "Refresh"):
        button = labels[label]
        assert callable(getattr(button, "callback", None))
        assert inspect.ismethod(button.callback)


# -- threshold stepper ----------------------------------------------------


@pytest.mark.asyncio
async def test_threshold_stepper_writes_db_value_and_rerenders() -> None:
    db, _conn = await _open_db()
    bot = _fake_bot(db)
    await set_starting_deal_percent(db, GUILD, 40)
    view = await _make_view(bot)
    interaction = _FakeInteraction()

    await _buttons(view)["+5%"].callback(interaction)

    assert await get_starting_deal_percent(db, GUILD) == 45
    embed = _edited_embed(interaction)
    assert "Threshold is now 45%" in _embed_text(embed)
    assert "45%" in _embed_text(embed)
    # The middle stepper button now shows the new value, in place.
    assert _buttons(view)["45%"] is _buttons(view).get("45%")
    assert interaction.response.sent_message is None


@pytest.mark.asyncio
async def test_threshold_stepper_clamps_at_90() -> None:
    db, _conn = await _open_db()
    bot = _fake_bot(db)
    await set_starting_deal_percent(db, GUILD, 88)
    view = await _make_view(bot)
    interaction = _FakeInteraction()

    await _buttons(view)["+5%"].callback(interaction)

    assert await get_starting_deal_percent(db, GUILD) == 90
    assert "Threshold is now 90%" in _embed_text(_edited_embed(interaction))


@pytest.mark.asyncio
async def test_threshold_stepper_clamps_at_10() -> None:
    db, _conn = await _open_db()
    bot = _fake_bot(db)
    await set_starting_deal_percent(db, GUILD, 12)
    view = await _make_view(bot)
    interaction = _FakeInteraction()

    await _buttons(view)["−5%"].callback(interaction)

    assert await get_starting_deal_percent(db, GUILD) == 10
    assert "Threshold is now 10%" in _embed_text(_edited_embed(interaction))


@pytest.mark.asyncio
async def test_threshold_stepper_requires_manage_server() -> None:
    db, _conn = await _open_db()
    bot = _fake_bot(db)
    await set_starting_deal_percent(db, GUILD, 40)
    view = await _make_view(bot)
    interaction = _FakeInteraction(manage_guild=False)

    await _buttons(view)["+5%"].callback(interaction)

    assert await get_starting_deal_percent(db, GUILD) == 40
    assert interaction.response.sent_message is not None
    assert "Manage Server" in interaction.response.sent_message
    assert interaction.response.edited == []


@pytest.mark.asyncio
async def test_failed_threshold_write_shows_ephemeral_error_not_broken_embed(
    monkeypatch,
) -> None:
    db, _conn = await _open_db()
    bot = _fake_bot(db)
    await set_starting_deal_percent(db, GUILD, 40)
    view = await _make_view(bot)
    interaction = _FakeInteraction()

    async def _boom(*_args, **_kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(
        "sniperplug.cogs.walmart_command.set_starting_deal_percent", _boom
    )
    await _buttons(view)["+5%"].callback(interaction)

    assert await get_starting_deal_percent(db, GUILD) == 40
    # Deferred first, so the error reaches the user as an ephemeral
    # follow-up, not a first response; the embed is not re-rendered.
    assert interaction.response.sent_message is None
    assert interaction.response.edited == []
    assert interaction.followup.sent
    assert "threshold" in str(interaction.followup.sent[0]).lower()


# -- extreme toggle ---------------------------------------------------------


@pytest.mark.asyncio
async def test_extreme_toggle_flips_stored_flag_both_ways() -> None:
    db, _conn = await _open_db()
    bot = _fake_bot(db)
    assert await extreme_alerts_enabled(db, GUILD) is False
    view = await _make_view(bot)

    interaction = _FakeInteraction()
    await _buttons(view)["Extreme: OFF"].callback(interaction)
    assert await extreme_alerts_enabled(db, GUILD) is True
    assert "Extreme: ON" in _buttons(view)
    assert _buttons(view)["Extreme: ON"].style is discord.ButtonStyle.success
    assert "Extreme leads: **ON**" in _embed_text(_edited_embed(interaction))

    interaction = _FakeInteraction()
    await _buttons(view)["Extreme: ON"].callback(interaction)
    assert await extreme_alerts_enabled(db, GUILD) is False
    assert "Extreme: OFF" in _buttons(view)
    assert _buttons(view)["Extreme: OFF"].style is discord.ButtonStyle.secondary
    assert "Extreme leads: **OFF**" in _embed_text(_edited_embed(interaction))


@pytest.mark.asyncio
async def test_extreme_toggle_requires_manage_server() -> None:
    db, _conn = await _open_db()
    bot = _fake_bot(db)
    view = await _make_view(bot)
    interaction = _FakeInteraction(manage_guild=False)

    await _buttons(view)["Extreme: OFF"].callback(interaction)

    assert await extreme_alerts_enabled(db, GUILD) is False
    assert interaction.response.sent_message is not None
    assert "Manage Server" in interaction.response.sent_message
    assert interaction.response.edited == []


# -- DM alerts toggle -------------------------------------------------------


@pytest.mark.asyncio
async def test_dm_toggle_flips_opener_on_and_updates_line() -> None:
    db, _conn = await _open_db()
    bot = _fake_bot(db)
    view = await _make_view(bot)
    interaction = _FakeInteraction()

    await _buttons(view)["DM alerts: OFF"].callback(interaction)

    saved = await get_dm_deal_alert_preference(db, USER)
    assert saved.enabled is True
    assert "DM alerts: ON" in _buttons(view)
    assert "Your DM alerts: **ON**" in _embed_text(_edited_embed(interaction))


@pytest.mark.asyncio
async def test_dm_toggle_only_touches_the_openers_row() -> None:
    db, _conn = await _open_db()
    bot = _fake_bot(db)
    await save_dm_deal_alert_preference(
        db, DmDealAlertPreference(user_id=USER, enabled=True)
    )
    await save_dm_deal_alert_preference(
        db, DmDealAlertPreference(user_id=OTHER_USER, enabled=True)
    )
    view = await _make_view(bot)
    interaction = _FakeInteraction(user_id=USER)

    await _buttons(view)["DM alerts: ON"].callback(interaction)

    assert (await get_dm_deal_alert_preference(db, USER)).enabled is False
    assert (await get_dm_deal_alert_preference(db, OTHER_USER)).enabled is True


@pytest.mark.asyncio
async def test_dm_toggle_needs_no_manage_server() -> None:
    db, _conn = await _open_db()
    bot = _fake_bot(db)
    view = await _make_view(bot)
    interaction = _FakeInteraction(manage_guild=False)

    await _buttons(view)["DM alerts: OFF"].callback(interaction)

    assert (await get_dm_deal_alert_preference(db, USER)).enabled is True
    assert interaction.response.edited != []


# -- refresh -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_refresh_rereads_state() -> None:
    db, _conn = await _open_db()
    bot = _fake_bot(db)
    await set_starting_deal_percent(db, GUILD, 40)
    view = await _make_view(bot)
    # State changes out from under the open menu after it was built.
    await set_starting_deal_percent(db, GUILD, 55)
    interaction = _FakeInteraction()

    await _buttons(view)["Refresh"].callback(interaction)

    assert "55%" in _embed_text(_edited_embed(interaction))


@pytest.mark.asyncio
async def test_refresh_needs_no_manage_server() -> None:
    db, _conn = await _open_db()
    bot = _fake_bot(db)
    view = await _make_view(bot)
    interaction = _FakeInteraction(manage_guild=False)

    await _buttons(view)["Refresh"].callback(interaction)

    assert interaction.response.edited != []
    assert interaction.response.sent_message is None


# -- dashboard embed ----------------------------------------------------------


def test_dashboard_embed_shows_state_and_is_green_when_ready() -> None:
    state = MenuDashboardState(
        state_available=True,
        provider_status="READY",
        provider_message="Walmart provider is ready.",
        threshold_percent=40,
        extreme_enabled=True,
        extreme_recent=2,
        delivery_ready=True,
        delivery_summary="Walmart delivery ready.",
        dm_enabled=True,
        dm_threshold=35,
        dm_daily_cap=25,
        wishlist_count=3,
        wishlist_cap=20,
        active_deals=[
            {"title": "LEGO Star Wars Set", "price": 29.99, "discount": 50},
            {"title": "Anker Charger", "price": 12.5, "discount": 44},
        ],
        active_deal_count=12,
        deals_today=3,
    )
    embed = build_walmart_menu_embed(state, manage_guild=True, can_check=True)
    text = _embed_text(embed)
    assert embed.color == discord.Color.green()
    assert "READY" in text
    assert "40%" in text
    assert "Extreme leads: **ON**" in text
    assert "Deals posted today: **3**" in text
    assert "Active deals: **12**" in text
    assert "LEGO Star Wars Set" in text
    assert "$29.99" in text
    assert "Your DM alerts: **ON**" in text
    assert "watchers not running" in text
    for _section, keys in MENU_MEMBER_SECTIONS + MENU_STAFF_SECTIONS:
        for key in keys:
            assert f"/walmart {key}" in text
    for key in MENU_REMOVED_KEYS:
        assert f"/walmart {key}" not in text


def test_dashboard_embed_error_is_red() -> None:
    state = MenuDashboardState(
        state_available=True,
        provider_status="ERROR",
        provider_message="Walmart provider reported an error.",
        threshold_percent=40,
    )
    embed = build_walmart_menu_embed(state)
    assert embed.color == discord.Color.red()
    assert "ERROR" in _embed_text(embed)


def test_dashboard_embed_disabled_provider_is_red() -> None:
    state = MenuDashboardState(
        state_available=True,
        provider_status="DISABLED",
        provider_message="Walmart provider is disabled.",
        threshold_percent=40,
    )
    assert build_walmart_menu_embed(state).color == discord.Color.red()


def test_dashboard_embed_ready_but_delivery_not_ready_is_orange() -> None:
    state = MenuDashboardState(
        state_available=True,
        provider_status="READY",
        provider_message="Walmart provider is ready.",
        threshold_percent=40,
        delivery_ready=False,
        delivery_summary="Walmart delivery not ready: public alerts are off.",
    )
    embed = build_walmart_menu_embed(state)
    assert embed.color == discord.Color.orange()
    assert "public alerts are off" in _embed_text(embed)


def test_state_unavailable_renders_fallback_line_and_keeps_function_list() -> None:
    state = MenuDashboardState(state_available=False)
    text = _embed_text(
        build_walmart_menu_embed(state, manage_guild=True, can_check=True)
    )
    assert "Live state unavailable — the function list still works." in text
    for _section, keys in MENU_MEMBER_SECTIONS + MENU_STAFF_SECTIONS:
        for key in keys:
            assert f"/walmart {key}" in text
    for key in MENU_REMOVED_KEYS:
        assert f"/walmart {key}" not in text
    # The legacy no-argument call renders the same fallback (member view).
    assert (
        "Live state unavailable — the function list still works."
        in _embed_text(build_walmart_menu_embed())
    )


def test_deals_today_none_shows_active_deals_instead() -> None:
    state = MenuDashboardState(
        state_available=True,
        provider_status="READY",
        provider_message="Walmart provider is ready.",
        threshold_percent=40,
        delivery_ready=True,
        delivery_summary="Walmart delivery ready.",
        active_deal_count=7,
        deals_today=None,
    )
    text = _embed_text(build_walmart_menu_embed(state))
    assert "Active deals: **7**" in text
    assert "showing active deals instead" in text
    assert "Deals posted today" not in text


def test_no_arg_embed_keeps_walmart_blue_and_function_list() -> None:
    embed = build_walmart_menu_embed()
    assert embed.color == discord.Color(0x0071DC)  # Walmart blue, same as the hub's category accent.
    text = _embed_text(embed)
    # No-argument call is the member view: member functions only.
    for key in MENU_MEMBER_KEYS:
        if key == "check":
            continue  # Check needs the Deal Finder role; not in the member list.
        assert f"/walmart {key}" in text
    for key in list(MENU_STAFF_KEYS) + list(MENU_REMOVED_KEYS):
        assert f"/walmart {key}" not in text
    assert "Deal checks need the 🔍 Deal Finder role" in text


@pytest.mark.asyncio
async def test_timeout_disables_all_controls_including_new_ones() -> None:
    db, _conn = await _open_db()
    bot = _fake_bot(db)
    view = await _make_view(bot)
    await view.on_timeout()
    assert all(item.disabled for item in view.children)


@pytest.mark.asyncio
async def test_dm_toggle_preserves_the_openers_other_settings() -> None:
    db, _conn = await _open_db()
    bot = _fake_bot(db)
    saved = await save_dm_deal_alert_preference(
        db,
        DmDealAlertPreference(
            user_id=USER, enabled=False, min_discount=50, max_alerts_per_day=10
        ),
    )
    assert saved.min_discount == 50
    view = await _make_view(bot)
    interaction = _FakeInteraction()

    await _buttons(view)["DM alerts: OFF"].callback(interaction)

    after = await get_dm_deal_alert_preference(db, USER)
    assert after.enabled is True
    assert after.min_discount == 50
    assert after.max_alerts_per_day == 10

# -- defer-first (Unknown interaction 10062 fix, live 2026-11-17) --------

# /menu and /walmart menu died live with 10062 because the dashboard
# snapshot loads from Turso BEFORE the first response, and prod lock waits
# of 2.5-7.5s blow past Discord's 3s interaction window. The same trap
# sits in every inline control: the DB write/read runs before the re-render.
# These tests pin the order: defer, then the slow work.


@pytest.mark.asyncio
async def test_menu_command_defers_before_loading_state(monkeypatch) -> None:
    import sniperplug.cogs.walmart_command as wc

    db, _conn = await _open_db()
    bot = _fake_bot(db)
    interaction = _FakeInteraction()

    async def _fake_load(bot, guild_id, user_id):
        interaction.response.calls.append("load")
        return MenuDashboardState()

    monkeypatch.setattr(wc, "load_menu_dashboard_state", _fake_load)
    cog = WalmartCommandCog(bot=bot)
    await cog.menu.callback(cog, interaction)
    assert interaction.response.calls == ["defer", "load"]
    assert len(interaction.followup.sent) == 1
    assert isinstance(interaction.followup.sent[0][1]["view"], WalmartMenuView)


@pytest.mark.asyncio
async def test_threshold_stepper_defers_before_writing(monkeypatch) -> None:
    import sniperplug.cogs.walmart_command as wc

    db, _conn = await _open_db()
    bot = _fake_bot(db)
    await set_starting_deal_percent(db, GUILD, 40)
    view = await _make_view(bot)
    interaction = _FakeInteraction()

    real_set = wc.set_starting_deal_percent

    async def _logged_set(*args, **kwargs):
        interaction.response.calls.append("write")
        return await real_set(*args, **kwargs)

    monkeypatch.setattr(wc, "set_starting_deal_percent", _logged_set)
    await _buttons(view)["+5%"].callback(interaction)
    assert interaction.response.calls == ["defer", "write"]
    assert await get_starting_deal_percent(db, GUILD) == 45


@pytest.mark.asyncio
async def test_extreme_toggle_defers_before_writing(monkeypatch) -> None:
    import sniperplug.cogs.walmart_command as wc

    db, _conn = await _open_db()
    bot = _fake_bot(db)
    view = await _make_view(bot)
    interaction = _FakeInteraction()

    real_set = wc.set_extreme_alerts_enabled

    async def _logged_set(*args, **kwargs):
        interaction.response.calls.append("write")
        return await real_set(*args, **kwargs)

    monkeypatch.setattr(wc, "set_extreme_alerts_enabled", _logged_set)
    await _buttons(view)["Extreme: OFF"].callback(interaction)
    assert interaction.response.calls == ["defer", "write"]
    assert await extreme_alerts_enabled(db, GUILD) is True


@pytest.mark.asyncio
async def test_dm_toggle_defers_before_reading(monkeypatch) -> None:
    import sniperplug.cogs.walmart_command as wc

    db, _conn = await _open_db()
    bot = _fake_bot(db)
    view = await _make_view(bot)
    interaction = _FakeInteraction()

    real_get = wc.get_dm_deal_alert_preference
    real_save = wc.save_dm_deal_alert_preference

    async def _logged_get(*args, **kwargs):
        interaction.response.calls.append("read")
        return await real_get(*args, **kwargs)

    async def _logged_save(*args, **kwargs):
        interaction.response.calls.append("write")
        return await real_save(*args, **kwargs)

    monkeypatch.setattr(wc, "get_dm_deal_alert_preference", _logged_get)
    monkeypatch.setattr(wc, "save_dm_deal_alert_preference", _logged_save)
    await _buttons(view)["DM alerts: OFF"].callback(interaction)
    assert interaction.response.calls == ["defer", "read", "write"]
    assert (await get_dm_deal_alert_preference(db, USER)).enabled is True


@pytest.mark.asyncio
async def test_refresh_defers_before_reloading(monkeypatch) -> None:
    import sniperplug.cogs.walmart_command as wc

    db, _conn = await _open_db()
    bot = _fake_bot(db)
    view = await _make_view(bot)
    interaction = _FakeInteraction()

    async def _fake_load(bot, guild_id, user_id):
        interaction.response.calls.append("load")
        return MenuDashboardState()

    monkeypatch.setattr(wc, "load_menu_dashboard_state", _fake_load)
    await _buttons(view)["Refresh"].callback(interaction)
    assert interaction.response.calls == ["defer", "load"]
    assert len(interaction.response.edited) == 1
