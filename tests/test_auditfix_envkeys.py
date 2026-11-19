"""Regression tests for the 2026-11-17 audit env-key fixes (audit §3 items 6, 7, 8, 10).

Each not-configured path must name the missing key and say plainly the
feature is unavailable until the server owner sets it — instead of looking
broken per-tap. Behaviour when the key/creds ARE present is unchanged.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import aiosqlite
import pytest

from sniperplug.cogs.canonical_workflow import CanonicalWorkflowCog
from sniperplug.cogs.ebay_research import EbayResearchCog
from sniperplug.cogs.home_depot_local import (
    HomeDepotLocalCog,
    run_home_depot_local_scan,
)
from sniperplug.cogs.home_depot_search import HomeDepotSearchCog
from sniperplug.providers.base import ProviderStatus
from sniperplug.providers.registry import provider_registry
from sniperplug.providers.serpapi_home_depot import (
    SerpApiHomeDepotConfig,
    SerpApiHomeDepotProvider,
)
from sniperplug.services.command_surface import (
    REQUIRED_CANONICAL_COMMANDS,
    RETIRED_COMMAND_NAMES,
    command_surface_issues,
    prune_retired_commands,
    replacement_for,
)
from sniperplug.services.ebay_browse_api import (
    EbayApiError,
    EbayBrowse,
    EbayListing,
    EbaySettings,
)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeFollowup:
    def __init__(self) -> None:
        self.sent: list[SimpleNamespace] = []

    async def send(self, content=None, **kwargs):
        self.sent.append(SimpleNamespace(content=content, kwargs=kwargs))

    def texts(self) -> list[str]:
        out: list[str] = []
        for item in self.sent:
            if item.content:
                out.append(str(item.content))
            for value in item.kwargs.values():
                if hasattr(value, "description") and value.description:
                    out.append(str(value.description))
                if hasattr(value, "fields"):
                    for field in value.fields:
                        out.append(str(field.value or ""))
        return out


class _FakeResponse:
    def __init__(self) -> None:
        self.deferred = False

    async def defer(self, **_kwargs) -> None:
        self.deferred = True

    async def send_message(self, content=None, **_kwargs) -> None:
        pass

    def is_done(self) -> bool:
        return self.deferred


class _FakeInteraction:
    def __init__(self, *, guild_id: int | None = 1, user_id: int = 42):
        self.guild_id = guild_id
        self.guild = SimpleNamespace(id=guild_id, me=SimpleNamespace())
        self.user = SimpleNamespace(id=user_id, guild_permissions=SimpleNamespace(manage_guild=True))
        self.channel_id = 7
        self.response = _FakeResponse()
        self.followup = _FakeFollowup()


class _FakeDatabase:
    def __init__(self, conn) -> None:
        self.conn = conn

    def require_conn(self):
        return self.conn


def _register_unconfigured_home_depot_provider():
    """Register a SerpApi-less Home Depot provider; return a restore closure."""
    previous = provider_registry.get("home_depot_serpapi")
    provider_registry.register(
        SerpApiHomeDepotProvider(SerpApiHomeDepotConfig(api_key=None)),
        replace=True,
    )

    def restore() -> None:
        provider_registry.providers.pop("home_depot_serpapi", None)
        if previous is not None:
            provider_registry.register(previous, replace=True)

    return restore


def _sample_listing() -> EbayListing:
    return EbayListing(
        item_id="v1|1000|0",
        title="Sony genuine wireless controller",
        price_cents=3750,
        currency="USD",
        condition="New",
        seller="reliable-seller",
        shipping_cents=625,
        asking_url="https://www.ebay.com/itm/1000",
        affiliate_url="https://www.ebay.com/itm/1000?mkevt=1&campid=1234567890",
    )


class _FakeBrowse:
    def __init__(self, listings) -> None:
        self._listings = tuple(listings)

    async def search(self, **_kwargs):
        return self._listings


# ---------------------------------------------------------------------------
# Item 6 — Home Depot without SERPAPI_API_KEY
# ---------------------------------------------------------------------------


def test_home_depot_health_names_key_and_owner_action() -> None:
    provider = SerpApiHomeDepotProvider(SerpApiHomeDepotConfig(api_key=None))
    health = asyncio.run(provider.healthcheck())
    assert health.ok is False
    assert health.status == ProviderStatus.DISABLED
    assert "SERPAPI_API_KEY" in health.message
    assert "server owner" in health.message
    assert "not available" in health.message.lower()
    assert "no SerpApi credit was spent" in health.message


def test_home_depot_scan_without_key_warns_without_network() -> None:
    from sniperplug.providers.base import ProviderScanRequest

    provider = SerpApiHomeDepotProvider(SerpApiHomeDepotConfig(api_key=None))
    result = asyncio.run(
        provider.scan(ProviderScanRequest(source_key="home_depot_serpapi", query="drill"))
    )
    assert result.candidates == ()
    assert result.warnings
    assert "SERPAPI_API_KEY" in result.warnings[0]
    assert "server owner" in result.warnings[0]


def test_home_depot_search_reports_not_configured_up_front() -> None:
    async def run() -> None:
        restore = _register_unconfigured_home_depot_provider()
        try:
            cog = HomeDepotSearchCog(bot=None)
            interaction = _FakeInteraction()
            await cog.home_depot_search.callback(
                cog, interaction, query="milwaukee drill"
            )
            texts = interaction.followup.texts()
            assert texts, "expected a not-configured reply"
            joined = "\n".join(texts)
            assert "SERPAPI_API_KEY" in joined
            assert "server owner" in joined
            assert "not available" in joined.lower()
        finally:
            restore()

    asyncio.run(run())


def test_home_depot_local_scan_without_key_names_key() -> None:
    async def run() -> None:
        restore = _register_unconfigured_home_depot_provider()
        try:
            scan = await run_home_depot_local_scan(
                42, "334851114", "06610", store_id="1234", db=None
            )
            assert scan.candidates == ()
            assert scan.warnings
            joined = "\n".join(scan.warnings)
            assert "SERPAPI_API_KEY" in joined
            assert "server owner" in joined
        finally:
            restore()

    asyncio.run(run())


def test_hd_stock_does_not_open_store_picker_without_key(monkeypatch) -> None:
    """Without the key the picker is a dead end: fail before the store lookup."""

    async def _must_not_run(*_args, **_kwargs):
        raise AssertionError("store picker must not run when SerpApi is not configured")

    monkeypatch.setattr(
        "sniperplug.cogs.home_depot_local.find_home_depot_stores", _must_not_run
    )

    async def run() -> None:
        restore = _register_unconfigured_home_depot_provider()
        try:
            cog = HomeDepotLocalCog(bot=SimpleNamespace(db=None))
            interaction = _FakeInteraction()
            await cog.hd_stock.callback(cog, interaction, sku="334851114", zip_code="06610")
            texts = interaction.followup.texts()
            assert texts, "expected a not-configured reply"
            joined = "\n".join(texts)
            assert "SERPAPI_API_KEY" in joined
            assert "server owner" in joined
        finally:
            restore()

    asyncio.run(run())


# ---------------------------------------------------------------------------
# Item 7 — eBay research / review_sample without creds
# ---------------------------------------------------------------------------


def test_ebay_search_disabled_names_flag_and_owner() -> None:
    client = EbayBrowse(EbaySettings(client_id="a", client_secret="b"))
    with pytest.raises(EbayApiError) as caught:
        asyncio.run(client.search(query="phone"))
    message = str(caught.value)
    assert "disabled" in message
    assert "EBAY_RESEARCH_ENABLED" in message
    assert "server owner" in message
    assert "No eBay request was made" in message


def test_ebay_search_missing_creds_names_exactly_what_is_missing() -> None:
    missing_id = EbayBrowse(
        EbaySettings(client_secret="b", research_enabled=True)
    )
    with pytest.raises(EbayApiError) as caught:
        asyncio.run(missing_id.search(query="phone"))
    assert "EBAY_CLIENT_ID" in str(caught.value)
    assert "EBAY_CLIENT_SECRET" not in str(caught.value)
    assert "server owner" in str(caught.value)

    missing_both = EbayBrowse(EbaySettings(research_enabled=True))
    with pytest.raises(EbayApiError) as caught:
        asyncio.run(missing_both.search(query="phone"))
    assert "EBAY_CLIENT_ID" in str(caught.value)
    assert "EBAY_CLIENT_SECRET" in str(caught.value)


def test_ebay_research_command_reports_not_configured() -> None:
    async def run() -> None:
        cog = EbayResearchCog(bot=SimpleNamespace(db=None))
        cog.browse = EbayBrowse(EbaySettings(client_id="a", client_secret="b"))
        interaction = _FakeInteraction()
        await cog.research.callback(cog, interaction, query="iphone 11")
        texts = interaction.followup.texts()
        assert texts, "expected a not-configured reply"
        joined = "\n".join(texts)
        assert "EBAY_RESEARCH_ENABLED" in joined
        assert "server owner" in joined

    asyncio.run(run())


def test_ebay_review_sample_command_reports_not_configured() -> None:
    async def run() -> None:
        cog = EbayResearchCog(bot=SimpleNamespace(db=None))
        cog.browse = EbayBrowse(EbaySettings(research_enabled=True))
        interaction = _FakeInteraction()
        await cog.review_sample.callback(cog, interaction, query="iphone 11")
        texts = interaction.followup.texts()
        assert texts, "expected a not-configured reply"
        joined = "\n".join(texts)
        assert "EBAY_CLIENT_ID" in joined
        assert "server owner" in joined

    asyncio.run(run())


# ---------------------------------------------------------------------------
# Item 8 — Target location lookup without TARGET_REDSKY_API_KEY
# ---------------------------------------------------------------------------


def _target_cog() -> CanonicalWorkflowCog:
    bot = SimpleNamespace(
        settings=SimpleNamespace(target_redsky_api_key=None), db=None
    )
    return CanonicalWorkflowCog(bot=bot)


def test_target_lookup_error_names_key_and_owner() -> None:
    async def run() -> None:
        cog = _target_cog()
        stores, error = await cog._find_target_stores("06610")
        assert stores == ()
        assert "TARGET_REDSKY_API_KEY" in error
        assert "server owner" in error
        assert "Nothing was saved" in error
        assert "temporarily" not in error.lower()

    asyncio.run(run())


def test_target_location_command_reports_not_configured() -> None:
    async def run() -> None:
        cog = _target_cog()
        interaction = _FakeInteraction()
        await cog.target_location.callback(cog, interaction, zip_code="06610")
        texts = interaction.followup.texts()
        assert texts, "expected a not-configured reply"
        joined = "\n".join(texts)
        assert "TARGET_REDSKY_API_KEY" in joined
        assert "server owner" in joined

    asyncio.run(run())


def test_target_dm_location_command_reports_not_configured() -> None:
    async def run() -> None:
        cog = _target_cog()
        interaction = _FakeInteraction()
        await cog.target_dm_location.callback(cog, interaction, zip_code="06610")
        texts = interaction.followup.texts()
        assert texts, "expected a not-configured reply"
        joined = "\n".join(texts)
        assert "TARGET_REDSKY_API_KEY" in joined
        assert "server owner" in joined

    asyncio.run(run())


def test_target_clear_commands_work_without_redsky_key(monkeypatch) -> None:
    """Clearing a location never does a store lookup, so it needs no key."""

    async def _clear_none(*_args, **_kwargs):
        return False

    async def _noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr(
        "sniperplug.cogs.canonical_workflow.clear_target_location", _clear_none
    )
    monkeypatch.setattr(
        "sniperplug.cogs.canonical_workflow.remove_target_retailer", _noop
    )
    monkeypatch.setattr(
        "sniperplug.cogs.canonical_workflow.prune_orphan_target_product_rows",
        _noop,
    )

    async def run() -> None:
        cog = _target_cog()
        interaction = _FakeInteraction()
        await cog.target_location_clear.callback(cog, interaction)
        assert any(
            "did not have an active Target location" in text
            for text in interaction.followup.texts()
        )
        interaction = _FakeInteraction()
        await cog.target_dm_location_clear.callback(cog, interaction)
        assert any(
            "did not have an active personal Target location" in text
            for text in interaction.followup.texts()
        )

    asyncio.run(run())


# ---------------------------------------------------------------------------
# Item 10 — eBay consent/server are retired (research needs no stored consent)
# ---------------------------------------------------------------------------


def test_ebay_consent_and_server_are_retired() -> None:
    assert "ebay consent" in RETIRED_COMMAND_NAMES
    assert "ebay server" in RETIRED_COMMAND_NAMES
    assert replacement_for("ebay consent") == "/ebay status"
    assert replacement_for("ebay server") == "/ebay status"
    # The honest eBay surface stays: status is not retired.
    assert "ebay" not in RETIRED_COMMAND_NAMES
    assert "ebay status" not in RETIRED_COMMAND_NAMES


def test_prune_removes_ebay_consent_and_server_from_the_group() -> None:
    cog = EbayResearchCog(bot=SimpleNamespace(db=None))
    group = cog.app_command
    before = list(group.commands)

    class _FakeTree:
        def get_command(self, name: str):
            return group if name == "ebay" else None

        def remove_command(self, name: str, **_kwargs):  # pragma: no cover
            raise AssertionError("group subcommands must not use top-level removal")

    try:
        removed = prune_retired_commands(_FakeTree())
        assert {"ebay consent", "ebay server"} <= {item.name for item in removed}
        names = {command.name for command in group.commands}
        assert "consent" not in names
        assert "server" not in names
        assert {"status", "research", "preview"} <= names
    finally:
        # Restore the shared group so other tests see the full cog again.
        current = {command.name for command in group.commands}
        for command in before:
            if command.name not in current:
                group.add_command(command)


def test_surface_issues_flag_retired_ebay_subcommand_still_loaded() -> None:
    commands = [SimpleNamespace(name=name) for name in REQUIRED_CANONICAL_COMMANDS]
    commands.append(
        SimpleNamespace(
            name="ebay",
            commands=[SimpleNamespace(name="consent"), SimpleNamespace(name="status")],
        )
    )
    issues = command_surface_issues(commands)
    assert any("retired" in issue and "consent" in issue for issue in issues)


def test_ebay_research_does_not_require_stored_consent() -> None:
    """Evidence for retiring consent: research succeeds with no opt-in row."""

    async def run() -> None:
        conn = await aiosqlite.connect(":memory:")
        try:
            cog = EbayResearchCog(bot=SimpleNamespace(db=_FakeDatabase(conn)))
            cog.browse = _FakeBrowse([_sample_listing()])
            interaction = _FakeInteraction()
            await cog.research.callback(cog, interaction, query="controller")
            assert interaction.followup.sent, "expected a research embed reply"
            sent = interaction.followup.sent[0]
            assert "embed" in sent.kwargs
            assert sent.kwargs["embed"].title == "eBay active listing research"
        finally:
            await conn.close()

    asyncio.run(run())


def test_review_sample_no_longer_points_at_retired_consent_command() -> None:
    async def run() -> None:
        conn = await aiosqlite.connect(":memory:")
        try:
            cog = EbayResearchCog(bot=SimpleNamespace(db=_FakeDatabase(conn)))
            cog.browse = _FakeBrowse([_sample_listing()])
            interaction = _FakeInteraction()
            await cog.review_sample.callback(cog, interaction, query="controller")
            joined = "\n".join(interaction.followup.texts())
            assert "/ebay consent" not in joined
            assert "Unsubscribe with the button below" in joined
        finally:
            await conn.close()

    asyncio.run(run())
