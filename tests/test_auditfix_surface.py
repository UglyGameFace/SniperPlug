"""Regression tests for the audit-surface fixes (audit SS3 items 5, 12, 13, 14, 17).

Every test in the ``test_*`` groups below FAILS on clean main (fb08fca) and
PASSES with the fix. The ``test_dead_items_deliberately_left_in_place``
group is the opposite: it pins items the audit found dead that are
deliberately NOT deleted, with the reason in the assertion message, so a
future deletion is a conscious decision instead of a drive-by.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from sniperplug.cogs.local_inventory import (
    LocalInventoryCog,
    build_local_inventory_embed,
)
from sniperplug.cogs.sniperplug import SniperPlugCog
from sniperplug.cogs.unified_menu import (
    ENTRIES_BY_CATEGORY,
    MENU_DELEGATES,
    MENU_ENTRIES,
    UnifiedMenuCog,
)
from sniperplug.cogs.walmart_command import (
    WALMART_SUBCOMMANDS,
    WalmartCommandCog,
    _DELEGATES,
)
from sniperplug.models.local_inventory import LocalInventoryProof
from sniperplug.services.command_surface import RETIRED_COMMAND_NAMES

ROOT = Path(__file__).resolve().parents[1]
STATIC_LINE = "Static preview / demo \u2014 not live data"


# -- fakes ---------------------------------------------------------------


class _FakeResponse:
    def __init__(self) -> None:
        self.sent: list[tuple[tuple, dict]] = []
        self.modal = None

    def is_done(self) -> bool:
        return False

    async def defer(self, *args, **kwargs) -> None:
        return None

    async def send_message(self, *args, **kwargs) -> None:
        self.sent.append((args, kwargs))

    async def send_modal(self, modal) -> None:
        self.modal = modal

    async def edit_message(self, **kwargs) -> None:
        self.sent.append(((), kwargs))


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


class _FakeBot:
    """Minimal bot stand-in: get_cog by name, cogs as a name -> cog mapping."""

    def __init__(self, *, cogs=None, get_cog_map=None) -> None:
        self.cogs = dict(cogs or {})
        self._get_cog_map = dict(get_cog_map or {})
        self.db = None

    def get_cog(self, name):
        return self._get_cog_map.get(name)


def _recording_command(calls: list):
    async def _callback(cog, interaction, *args, **kwargs):
        calls.append((args, kwargs))

    return SimpleNamespace(callback=_callback)


def _sent_texts(interaction: _FakeInteraction) -> str:
    texts: list[str] = []
    for args, kwargs in interaction.response.sent:
        texts.extend(str(arg) for arg in args)
        texts.extend(str(value) for value in kwargs.values())
    for args, kwargs in interaction.followup.sent:
        texts.extend(str(arg) for arg in args)
        texts.extend(str(value) for value in kwargs.values())
    return "\n".join(texts)


# -- item 5: /walmart scan-now name collision ----------------------------


class _RenamedGlobalRunner:
    """Stand-in for the global runner after a class rename: the class name
    changed, so get_cog("AutoScanRunnerCog") misses, but the module did not."""

    def __init__(self, calls: list) -> None:
        self.calls = calls
        self.autoscan_now = _recording_command(calls)


_RenamedGlobalRunner.__module__ = "sniperplug.cogs.global_auto_scan_runner"


class _LegacyRunner:
    """A runner from another module that also defines AutoScanRunnerCog."""

    def __init__(self, calls: list) -> None:
        self.calls = calls
        self.autoscan_now = _recording_command(calls)


_LegacyRunner.__module__ = "sniperplug.cogs.auto_scan_runner"


@pytest.mark.asyncio
async def test_scan_now_resolves_loaded_runner_instance_not_class_name() -> None:
    """Item 5: scan-now must survive the global runner class being renamed.

    The fake bot answers None for get_cog("AutoScanRunnerCog") (the rename)
    but still holds the loaded runner instance from the global module. On
    clean main the delegate looks the cog up by that colliding class name,
    gets None, and sends "isn't loaded right now" instead of scanning."""

    calls: list = []
    runner = _RenamedGlobalRunner(calls)
    bot = _FakeBot(cogs={"RenamedRunner": runner})
    cog = WalmartCommandCog(bot=bot)
    interaction = _FakeInteraction()

    await cog._delegate(interaction, "scan-now", force=True)

    assert calls == [((), {"force": True})], (
        "scan-now did not reach the loaded global runner instance: "
        f"calls={calls} reply={_sent_texts(interaction)!r}"
    )


@pytest.mark.asyncio
async def test_scan_now_prefers_global_runner_over_other_runner_modules() -> None:
    """Item 5: three modules define AutoScanRunnerCog; only the global one
    is loaded, so the delegate must pick the global-module instance even
    when another runner instance is also present."""

    global_calls: list = []
    legacy_calls: list = []
    bot = _FakeBot(
        cogs={
            "Legacy": _LegacyRunner(legacy_calls),
            "Global": _RenamedGlobalRunner(global_calls),
        }
    )
    cog = WalmartCommandCog(bot=bot)

    await cog._delegate(_FakeInteraction(), "scan-now", force=True)

    assert len(global_calls) == 1
    assert legacy_calls == []


def test_scan_now_resolution_names_the_global_module() -> None:
    """Item 5: the resolution must be anchored on the global runner module,
    not on the colliding class name."""

    source = (ROOT / "sniperplug" / "cogs" / "walmart_command.py").read_text()
    assert (
        '_SCAN_NOW_RUNNER_MODULE = "sniperplug.cogs.global_auto_scan_runner"'
        in source
    )


# -- item 12: restore recheck + probe; leave the rest --------------------


@pytest.mark.asyncio
async def test_walmart_recheck_subcommand_restored() -> None:
    """Item 12: /walmart recheck restores active_deal_recheck. A too-short
    search reaches the real handler (which rejects it) instead of the
    delegate failing with "isn't loaded right now"."""

    cog = WalmartCommandCog(bot=_FakeBot())
    interaction = _FakeInteraction()

    await WalmartCommandCog.recheck.callback(cog, interaction, search="ab")

    assert "at least three characters" in _sent_texts(interaction)


def test_walmart_recheck_and_probe_are_walmart_subcommands() -> None:
    """Item 12: both restored functions are real /walmart subcommands."""

    names = {command.name for command in WalmartCommandCog(bot=None).app_command.commands}
    assert {"recheck", "probe"}.issubset(names)
    assert {"recheck", "probe"}.issubset(set(WALMART_SUBCOMMANDS))
    assert len(names) == len(WALMART_SUBCOMMANDS) + 1  # +1 is the menu


def test_walmart_probe_delegates_to_loaded_verified_scanner() -> None:
    """Item 12: /walmart probe restores walmart_api_probe through the loaded
    VerifiedDealScannerCog (it inherits the method from DealScannerCog)."""

    from sniperplug.cogs.verified_deal_scanner import VerifiedDealScannerCog

    assert _DELEGATES["probe"] == ("VerifiedDealScannerCog", "walmart_api_probe")
    assert hasattr(VerifiedDealScannerCog, "walmart_api_probe")
    assert hasattr(VerifiedDealScannerCog, "_send_walmart_api_probe")
    bot_source = (ROOT / "sniperplug" / "bot.py").read_text()
    assert "await self.add_cog(VerifiedDealScannerCog(self))" in bot_source


@pytest.mark.asyncio
async def test_probe_delegate_calls_verified_scanner_handler() -> None:
    """Item 12: the probe delegate actually invokes the verified scanner's
    handler with the probe arguments."""

    calls: list = []
    scanner = SimpleNamespace(walmart_api_probe=_recording_command(calls))
    bot = _FakeBot(get_cog_map={"VerifiedDealScannerCog": scanner})
    cog = WalmartCommandCog(bot=bot)

    await cog._delegate(_FakeInteraction(), "probe", query="detergent", max_results=3)

    assert calls == [((), {"query": "detergent", "max_results": 3})]


def test_dead_items_deliberately_left_in_place() -> None:
    """Item 12: these dead items are NOT deleted, each for a stated reason.
    A future deletion must update this test deliberately, not by accident."""

    from sniperplug.cogs.active_deal_recheck import ActiveDealRecheckCog
    from sniperplug.cogs.active_deals import ActiveDealsCog
    from sniperplug.cogs.deal_scanner import DealScannerCog
    from sniperplug.cogs.home_depot_local import HomeDepotLocalCog
    from sniperplug.cogs.public_alerts import PublicAlertsCog
    from sniperplug.cogs.settings_dashboard import SettingsDashboardCog

    # PublicAlertsCog: existing tests assert its commands exist
    # (test_autoscan_cache_health_truth, test_persistent_public_panels_never_expire,
    # test_scout_lane_memory_reset). Deleting it breaks those tests.
    assert hasattr(PublicAlertsCog, "retailer_autoscan")
    assert hasattr(PublicAlertsCog, "retailer_autoscan_status")
    assert hasattr(PublicAlertsCog, "autoscan_clear_cache")
    assert hasattr(PublicAlertsCog, "public_alerts_status")
    # SettingsDashboardCog + its 3 commands: settings_dashboard.py is not
    # owned by this fix; its content survives as dashboard views.
    assert hasattr(SettingsDashboardCog, "sniperplug_health")
    assert hasattr(SettingsDashboardCog, "sniperplug_doctor")
    assert hasattr(SettingsDashboardCog, "sniperplug_commands")
    # hd_penny_zip: home_depot_local.py is not owned by this fix.
    assert hasattr(HomeDepotLocalCog, "hd_penny_zip")
    # active_deals_cleanup: active_deals.py is not owned by this fix; its
    # work auto-runs when Active Deals loads.
    assert hasattr(ActiveDealsCog, "active_deals_cleanup")
    # walmart_scan: deal_scanner.py is owned here for the probe method only.
    assert hasattr(DealScannerCog, "walmart_scan")
    # active_deals_recheck (batch): tests/test_walmart_batch_recheck.py
    # asserts it exists, and its function lives on via the /walmart active
    # picker button.
    assert hasattr(ActiveDealRecheckCog, "active_deals_recheck")
    # DealScannerCog itself: base class of the loaded VerifiedDealScannerCog.
    assert DealScannerCog.__name__ == "DealScannerCog"
    # Retired top-level names stay pruned even though the functions are
    # restored as /walmart subcommands; only the group names are live.
    assert "active_deal_recheck" in RETIRED_COMMAND_NAMES
    assert "walmart_api_probe" in RETIRED_COMMAND_NAMES
    assert "open_box_deals" in RETIRED_COMMAND_NAMES
    assert "setup_sniperplug_here_status" in RETIRED_COMMAND_NAMES


# -- item 13: static/demo labelling --------------------------------------


def test_static_commands_labelled_static_in_description() -> None:
    """Item 13: snipe-plan / monitor-plan / scan-test must say in their
    command description that they are static/demo, not live data."""

    for name in ("snipe_plan", "monitor_plan", "scan_test"):
        description = getattr(SniperPlugCog, name).description or ""
        assert "static" in description.lower(), f"{name}: {description!r}"
        assert "not live data" in description.lower(), f"{name}: {description!r}"


@pytest.mark.asyncio
async def test_static_embeds_carry_static_preview_line() -> None:
    """Item 13: each static command's embed carries the explicit
    'static preview / demo — not live data' line."""

    cog = SniperPlugCog(bot=SimpleNamespace())
    interaction = _FakeInteraction()

    await SniperPlugCog.snipe_plan.callback(cog, interaction)
    await SniperPlugCog.monitor_plan.callback(cog, interaction)
    await SniperPlugCog.scan_test.callback(cog, interaction)

    embeds = [
        kwargs["embed"]
        for _args, kwargs in interaction.response.sent + interaction.followup.sent
        if "embed" in kwargs
    ]
    assert len(embeds) == 3
    for embed in embeds:
        assert STATIC_LINE in (embed.description or ""), (
            f"embed {embed.title!r} is missing the static-preview line"
        )


def test_menu_entries_for_static_commands_say_static_not_live() -> None:
    """Item 13: the /menu copy advertising these commands must not call
    them live either."""

    by_key = {entry.key: entry for entry in MENU_ENTRIES}
    for key in ("sniperplug-snipe-plan", "sniperplug-monitor-plan", "sniperplug-scan-test"):
        assert "static" in by_key[key].description.lower(), f"{key}: {by_key[key].description!r}"
        assert "live" not in by_key[key].description.lower(), f"{key}: {by_key[key].description!r}"


# -- item 14: /movies menu gaps ------------------------------------------


def test_menu_has_movies_test_alert_and_disable() -> None:
    """Item 14: /movies test-alert and /movies disable are reachable from
    the unified menu."""

    assert MENU_DELEGATES["movies-test-alert"] == ("MovieTicketsCog", "test_alert")
    assert MENU_DELEGATES["movies-disable"] == ("MovieTicketsCog", "disable")
    category_keys = {entry.key for entry in ENTRIES_BY_CATEGORY["movies-verizon"]}
    assert {"movies-test-alert", "movies-disable"}.issubset(category_keys)

    from sniperplug.cogs.registered_multi_source_movies import (
        MovieTicketsCog as RegisteredMoviesCog,
    )

    assert hasattr(RegisteredMoviesCog, "test_alert")
    assert hasattr(RegisteredMoviesCog, "disable")


@pytest.mark.asyncio
async def test_menu_runs_movies_test_alert() -> None:
    """Item 14: picking the menu entry actually invokes the movies cog's
    test-alert handler."""

    calls: list = []
    movies = SimpleNamespace(test_alert=_recording_command(calls))
    bot = _FakeBot(get_cog_map={"MovieTicketsCog": movies})
    menu_cog = UnifiedMenuCog(bot=bot)

    await menu_cog.run_entry(_FakeInteraction(), "movies-test-alert")

    assert calls == [((), {})]


# -- item 17: local_check is proof-recording, not live stock -------------


def test_local_check_worded_as_proof_recording_not_live_stock() -> None:
    """Item 17: /local_check must present itself as a proof-recording tool
    and state plainly that no live inventory confirmation exists."""

    description = LocalInventoryCog.local_check.description or ""
    assert "proof" in description.lower(), description
    assert "no live" in description.lower(), description

    embed = build_local_inventory_embed(LocalInventoryProof(retailer="walmart"))
    assert "not live stock" in (embed.title or "").lower(), embed.title
    assert (
        "No live inventory confirmation exists for any retailer"
        in (embed.description or "")
    ), embed.description


def test_menu_local_check_entry_worded_as_proof() -> None:
    """Item 17: the /menu copy for local_check must not imply a live stock
    check either."""

    entry = next(item for item in MENU_ENTRIES if item.key == "local-check")
    assert entry.label == "Local proof check"
    assert "no live stock" in entry.description.lower(), entry.description


# -- follow-up: retired /ebay consent + server must not be menu-reachable --


def test_menu_cannot_invoke_retired_ebay_consent_commands() -> None:
    """Follow-up: /ebay consent and /ebay server are retired, so the
    unified menu must not list them — the menu delegates straight to the
    cog handler, bypassing the pruned command tree."""

    assert "ebay-consent" not in MENU_DELEGATES
    assert "ebay-server" not in MENU_DELEGATES
    assert {entry.key for entry in MENU_ENTRIES}.isdisjoint(
        {"ebay-consent", "ebay-server"}
    )
    # The remaining eBay surface still reaches the live commands.
    assert MENU_DELEGATES["ebay-status"] == ("EbayResearchCog", "status")
    assert MENU_DELEGATES["ebay-review-sample"] == ("EbayResearchCog", "review_sample")


def test_ebay_catalog_entry_no_longer_advertises_consent_commands() -> None:
    """Follow-up: the catalog's /ebay entry must not describe /ebay consent
    as the opt-in/out path — /ebay status is the eBay surface now, and
    review_sample's view carries the opt-out button."""

    from sniperplug.services.command_catalog import COMMAND_CATALOG

    entry = next(item for item in COMMAND_CATALOG if item.name == "/ebay \u2026")
    assert "/ebay consent" not in entry.when_to_use
    assert "consent opts in or out" not in entry.when_to_use
    assert "/ebay status" in entry.when_to_use
    assert "review_sample" in entry.when_to_use


# -- follow-up: retired /walmart cash must not be catalog-advertised ------


def test_catalog_no_longer_advertises_walmart_cash() -> None:
    """Follow-up: /walmart cash is retired (hard kill-switch, no real feed),
    so the catalog must not advertise it — neither as its own entry nor as
    a suggested command in another entry's guidance."""

    from sniperplug.services.command_catalog import COMMAND_CATALOG

    names = {entry.name for entry in COMMAND_CATALOG}
    assert "/walmart cash" not in names
    assert not any(
        "/walmart cash" in (entry.when_to_use or "") for entry in COMMAND_CATALOG
    )
    assert not any(
        "/walmart cash" in (entry.purpose or "") for entry in COMMAND_CATALOG
    )
