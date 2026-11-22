"""ZIP → tap-to-pick Home Depot store list (Felix, 2026-11-22).

Felix's complaint: the Home Depot forms assumed he knew a store
number. Now a run (or the location setter) that has a ZIP but no store
offers the stores near the ZIP as a picker first: picking one saves it
as his store and runs the search; the ZIP-only button keeps the old
behavior. These tests stub the store finder — no network, no credits.
"""

from __future__ import annotations

import asyncio

import aiosqlite

from sniperplug.cogs import home_depot_search as hd_search
from sniperplug.cogs.home_depot_search import (
    HdStorePickSelect,
    HdStorePickView,
    HomeDepotSearchCog,
)
from sniperplug.providers.base import (
    ProviderHealth,
    ProviderScanResult,
    ProviderStatus,
)
from sniperplug.providers.registry import provider_registry
from sniperplug.services.hd_user_location import get_hd_user_location
from sniperplug.services.home_depot_store_finder import HomeDepotStoreChoice


STORES = (
    HomeDepotStoreChoice(
        store_id="4821",
        label="Bridgeport — Bridgeport, CT 06606",
        url="https://www.homedepot.com/l/Bridgeport/4821",
    ),
    HomeDepotStoreChoice(
        store_id="621",
        label="Shelton — Shelton, CT 06484",
        url="https://www.homedepot.com/l/Shelton/621",
    ),
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
        self.messages = []

    async def defer(self, ephemeral=True):
        self._done = True

    async def send_message(self, content=None, **kwargs):
        self._done = True
        self.messages.append((content, kwargs))

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

    def views(self) -> list:
        return [kwargs["view"] for _content, kwargs in self.sent if kwargs.get("view")]


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
        self.edits = []
        self._db = db

    async def edit_original_response(self, **kwargs):
        self.edits.append(kwargs)


class FakeBot:
    def __init__(self, db):
        self.db = db


def _setup(db, monkeypatch, stores=STORES):
    provider = FakeProvider()
    provider_registry.register(provider, replace=True)

    async def fake_finder(zip_code, max_results=8):
        return stores

    monkeypatch.setattr(hd_search, "find_home_depot_stores", fake_finder)
    return provider, HomeDepotSearchCog(FakeBot(db))


def _pick_view(interaction) -> HdStorePickView:
    views = [v for v in interaction.followup.views() if isinstance(v, HdStorePickView)]
    assert len(views) == 1
    return views[0]


def _select_of(view: HdStorePickView) -> HdStorePickSelect:
    selects = [c for c in view.children if isinstance(c, HdStorePickSelect)]
    assert len(selects) == 1
    return selects[0]


def test_search_with_zip_and_no_store_offers_picker_and_spends_nothing(monkeypatch) -> None:
    async def run() -> None:
        db = await fresh_db()
        provider, cog = _setup(db, monkeypatch)
        interaction = FakeInteraction(db)
        await cog._run_search(
            interaction, query="faucet", store_id=None, zip_code="06606", page=1, penny_mode=False
        )
        # No scan ran yet — the picker comes first.
        assert provider.requests == []
        view = _pick_view(interaction)
        assert view.zip_code == "06606"
        texts = "\n".join(interaction.followup.texts())
        assert "Pick your Home Depot store" in texts
        assert "Bridgeport" in texts and "Shelton" in texts
        # The escape hatch is on the view.
        labels = [getattr(child, "label", "") for child in view.children]
        assert any("Just use the ZIP" in label for label in labels)

    asyncio.run(run())


def test_picking_a_store_saves_it_and_runs_the_search(monkeypatch) -> None:
    async def run() -> None:
        db = await fresh_db()
        provider, cog = _setup(db, monkeypatch)
        interaction = FakeInteraction(db)
        await cog._run_search(
            interaction, query="faucet", store_id=None, zip_code="06606", page=1, penny_mode=False
        )
        view = _pick_view(interaction)
        select = _select_of(view)
        select._values = ["4821"]
        pick_interaction = FakeInteraction(db)
        await select.callback(pick_interaction)
        # The scan ran exactly once, against the picked store.
        assert len(provider.requests) == 1
        assert provider.requests[0].metadata["store_id"] == "4821"
        assert provider.requests[0].metadata["zip_code"] == "06606"
        # The pick became the saved location.
        saved = await get_hd_user_location(db, user_id=111)
        assert saved is not None
        assert saved.zip_code == "06606" and saved.store_id == "4821"
        # The picker message was replaced, not left tappable.
        assert pick_interaction.edits and pick_interaction.edits[0].get("view") is None

    asyncio.run(run())


def test_zip_only_button_runs_search_without_a_store(monkeypatch) -> None:
    async def run() -> None:
        db = await fresh_db()
        provider, cog = _setup(db, monkeypatch)
        interaction = FakeInteraction(db)
        await cog._run_search(
            interaction, query="faucet", store_id=None, zip_code="06606", page=1, penny_mode=True
        )
        view = _pick_view(interaction)
        pick_interaction = FakeInteraction(db)
        await view._choose(pick_interaction, None)
        assert len(provider.requests) == 1
        assert provider.requests[0].metadata["store_id"] == ""
        assert provider.requests[0].metadata["zip_code"] == "06606"
        # Nothing was saved by the ZIP-only path beyond the typed ZIP
        # itself (resolve saves the ZIP with no store).
        saved = await get_hd_user_location(db, user_id=111)
        assert saved is not None and saved.store_id is None

    asyncio.run(run())


def test_saved_store_skips_the_picker(monkeypatch) -> None:
    async def run() -> None:
        db = await fresh_db()
        provider, cog = _setup(db, monkeypatch)
        from sniperplug.services.hd_user_location import save_hd_user_location

        await save_hd_user_location(db, user_id=111, zip_code="06606", store_id="4821")
        interaction = FakeInteraction(db)
        await cog._run_search(
            interaction, query="faucet", store_id=None, zip_code=None, page=1, penny_mode=False
        )
        assert len(provider.requests) == 1
        assert provider.requests[0].metadata["store_id"] == "4821"
        assert not any(
            isinstance(v, HdStorePickView) for v in interaction.followup.views()
        )

    asyncio.run(run())


def test_picker_refuses_a_non_owner_tap(monkeypatch) -> None:
    async def run() -> None:
        db = await fresh_db()
        provider, cog = _setup(db, monkeypatch)
        interaction = FakeInteraction(db)
        await cog._run_search(
            interaction, query="faucet", store_id=None, zip_code="06606", page=1, penny_mode=False
        )
        view = _pick_view(interaction)
        stranger = FakeInteraction(db, user_id=999)
        await view._choose(stranger, STORES[0])
        assert provider.requests == []
        assert stranger.response.messages
        assert "belongs to the user" in str(stranger.response.messages[0][0])

    asyncio.run(run())


def test_location_setter_offers_picker_then_saves_the_pick(monkeypatch) -> None:
    async def run() -> None:
        db = await fresh_db()
        _provider, cog = _setup(db, monkeypatch)
        interaction = FakeInteraction(db)
        await cog.set_hd_location(interaction, zip_code="06606", store_id=None)
        # Nothing saved yet — the picker comes first.
        assert await get_hd_user_location(db, user_id=111) is None
        view = _pick_view(interaction)
        pick_interaction = FakeInteraction(db)
        await view._choose(pick_interaction, STORES[1])
        saved = await get_hd_user_location(db, user_id=111)
        assert saved is not None
        assert saved.zip_code == "06606" and saved.store_id == "621"
        texts = "\n".join(pick_interaction.followup.texts())
        assert "Saved your Home Depot location" in texts
        assert "store 621" in texts

    asyncio.run(run())


def test_location_setter_zip_only_pick_saves_zip_without_store(monkeypatch) -> None:
    async def run() -> None:
        db = await fresh_db()
        _provider, cog = _setup(db, monkeypatch)
        interaction = FakeInteraction(db)
        await cog.set_hd_location(interaction, zip_code="06606", store_id=None)
        view = _pick_view(interaction)
        pick_interaction = FakeInteraction(db)
        await view._choose(pick_interaction, None)
        saved = await get_hd_user_location(db, user_id=111)
        assert saved is not None
        assert saved.zip_code == "06606" and saved.store_id is None

    asyncio.run(run())


def test_location_setter_saves_directly_when_finder_knows_no_stores(monkeypatch) -> None:
    async def run() -> None:
        db = await fresh_db()
        _provider, cog = _setup(db, monkeypatch, stores=())
        interaction = FakeInteraction(db)
        await cog.set_hd_location(interaction, zip_code="06606", store_id=None)
        saved = await get_hd_user_location(db, user_id=111)
        assert saved is not None and saved.zip_code == "06606"
        assert not any(
            isinstance(v, HdStorePickView) for v in interaction.followup.views()
        )
        texts = "\n".join(interaction.followup.texts())
        assert "Saved your Home Depot location" in texts

    asyncio.run(run())
