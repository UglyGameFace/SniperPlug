"""HD surfaces use the saved Home Depot location; hunts become standing.

Cog-level tests with a fake SerpApi provider: no credits, no network.
They pin Felix's three complaints (2026-11-21):
- a saved ZIP fills blank hunt/search/stock fields (and anchors the
  broad-query credit guard),
- a typed ZIP overrides and becomes the new saved ZIP,
- running a penny hunt registers a standing auto-scan.
"""

from __future__ import annotations

import asyncio

import aiosqlite

from sniperplug.cogs import home_depot_local as hd_local
from sniperplug.cogs.home_depot_local import HomeDepotLocalCog
from sniperplug.cogs.home_depot_search import HomeDepotSearchCog
from sniperplug.providers.base import (
    ProviderHealth,
    ProviderScanResult,
    ProviderStatus,
)
from sniperplug.providers.registry import provider_registry
from sniperplug.services.hd_standing_hunts import list_standing_hunts
from sniperplug.services.hd_user_location import (
    get_hd_user_location,
    save_hd_user_location,
)


class FakeDatabase:
    def __init__(self, conn):
        self.conn = conn

    def require_conn(self):
        return self.conn


async def fresh_db() -> FakeDatabase:
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    return FakeDatabase(conn)


class FakeProvider:
    provider_key = "home_depot_serpapi"

    def __init__(self):
        self.requests = []

    async def healthcheck(self):
        return ProviderHealth(
            provider_key=self.provider_key,
            ok=True,
            status=ProviderStatus.READY,
            message="ready",
        )

    async def scan(self, request):
        self.requests.append(request)
        return ProviderScanResult(
            provider_key=self.provider_key,
            candidates=(),
            warnings=(),
            metadata={"cache_hit": True},
        )


class FakeResponse:
    def __init__(self):
        self._done = False

    async def defer(self, ephemeral=True):
        self._done = True

    def is_done(self):
        return self._done


class FakeFollowup:
    def __init__(self):
        self.sent = []

    async def send(self, content=None, **kwargs):
        self.sent.append((content, kwargs))

    def texts(self) -> list[str]:
        out = []
        for content, kwargs in self.sent:
            if content:
                out.append(str(content))
            embed = kwargs.get("embed")
            if embed is not None:
                parts = [embed.title or "", embed.description or ""]
                for field in embed.fields:
                    parts.append(f"{field.name}: {field.value}")
                out.append("\n".join(parts))
        return out


class FakeUser:
    def __init__(self, user_id: int):
        self.id = user_id


class FakeInteraction:
    def __init__(self, db, user_id: int = 111, guild_id: int = 222):
        self.response = FakeResponse()
        self.followup = FakeFollowup()
        self.user = FakeUser(user_id)
        self.guild_id = guild_id
        self.guild = None
        self._db = db


class FakeBot:
    def __init__(self, db):
        self.db = db


def _setup(db):
    provider = FakeProvider()
    provider_registry.register(provider, replace=True)
    return provider, HomeDepotSearchCog(FakeBot(db))


def test_broad_hunt_with_saved_zip_passes_guard_and_registers_standing_hunt() -> None:
    async def run() -> None:
        db = await fresh_db()
        await save_hd_user_location(db, user_id=111, zip_code="06484")
        provider, cog = _setup(db)
        interaction = FakeInteraction(db)
        await cog._run_search(
            interaction, query="tools", store_id=None, zip_code=None, page=1, penny_mode=True
        )
        # The scan actually ran, anchored on the saved ZIP.
        assert len(provider.requests) == 1
        assert provider.requests[0].metadata["zip_code"] == "06484"
        texts = "\n".join(interaction.followup.texts())
        assert "too broad" not in texts
        assert "Using your saved Home Depot location" in texts
        assert "standing auto-scan" in texts
        # The hunt is standing now.
        hunts = await list_standing_hunts(db, guild_id=222, user_id=111)
        assert len(hunts) == 1
        assert hunts[0].query == "tools" and hunts[0].zip_code == "06484"

    asyncio.run(run())


def test_broad_hunt_without_any_zip_still_blocked_for_other_user() -> None:
    async def run() -> None:
        db = await fresh_db()
        await save_hd_user_location(db, user_id=111, zip_code="06484")
        provider, cog = _setup(db)
        interaction = FakeInteraction(db, user_id=999)
        await cog._run_search(
            interaction, query="tools", store_id=None, zip_code=None, page=1, penny_mode=True
        )
        assert provider.requests == []
        texts = "\n".join(interaction.followup.texts())
        assert "too broad without a ZIP or store ID" in texts
        assert await list_standing_hunts(db, guild_id=222, user_id=999) == []

    asyncio.run(run())


def test_typed_zip_overrides_saved_and_becomes_new_saved() -> None:
    async def run() -> None:
        db = await fresh_db()
        await save_hd_user_location(db, user_id=111, zip_code="06484", store_id="1234")
        provider, cog = _setup(db)
        interaction = FakeInteraction(db)
        await cog._run_search(
            interaction, query="faucet", store_id=None, zip_code="06516", page=1, penny_mode=False
        )
        assert provider.requests[0].metadata["zip_code"] == "06516"
        saved = await get_hd_user_location(db, user_id=111)
        assert saved is not None and saved.zip_code == "06516"
        # A ZIP change drops the old store (it belonged to the old area).
        assert saved.store_id is None

    asyncio.run(run())


def test_search_uses_saved_location_too() -> None:
    async def run() -> None:
        db = await fresh_db()
        await save_hd_user_location(db, user_id=111, zip_code="06484", store_id="621")
        provider, cog = _setup(db)
        interaction = FakeInteraction(db)
        await cog._run_search(
            interaction, query="faucet", store_id=None, zip_code=None, page=1, penny_mode=False
        )
        assert provider.requests[0].metadata["zip_code"] == "06484"
        assert provider.requests[0].metadata["store_id"] == "621"
        # A plain search does NOT register a standing hunt.
        assert await list_standing_hunts(db, guild_id=222, user_id=111) == []

    asyncio.run(run())


def test_set_hd_location_menu_method_saves() -> None:
    async def run() -> None:
        db = await fresh_db()
        _provider, cog = _setup(db)
        interaction = FakeInteraction(db)
        await cog.set_hd_location(interaction, zip_code="06484", store_id=None)
        saved = await get_hd_user_location(db, user_id=111)
        assert saved is not None and saved.zip_code == "06484"
        texts = "\n".join(interaction.followup.texts())
        assert "Saved your Home Depot location" in texts

    asyncio.run(run())


def test_hd_stock_blank_zip_uses_saved(monkeypatch) -> None:
    async def run() -> None:
        db = await fresh_db()
        await save_hd_user_location(db, user_id=111, zip_code="06484")
        _setup(db)

        async def no_stores(zip_code, max_results=8):
            return ()

        monkeypatch.setattr(hd_local, "find_home_depot_stores", no_stores)
        cog = HomeDepotLocalCog(FakeBot(db))
        interaction = FakeInteraction(db)
        await cog.hd_stock.callback(cog, interaction, sku="334851114", zip_code=None, store_id=None)
        texts = "\n".join(interaction.followup.texts())
        assert "06484" in texts

    asyncio.run(run())


def test_hd_stock_without_saved_or_typed_zip_explains() -> None:
    async def run() -> None:
        db = await fresh_db()
        _setup(db)
        cog = HomeDepotLocalCog(FakeBot(db))
        interaction = FakeInteraction(db)
        await cog.hd_stock.callback(cog, interaction, sku="334851114", zip_code=None, store_id=None)
        texts = "\n".join(interaction.followup.texts())
        assert "don't have a saved Home Depot location" in texts

    asyncio.run(run())
