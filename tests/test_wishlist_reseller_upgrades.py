"""Tests for the reseller wishlist upgrades: per-item target prices and
wishlist terms steering the catalog scan's priority cadence.

Storage tests use a real in-memory SQLite connection wrapped like the
existing wishlist tests (``require_conn()``); the process-global
``_SCHEMA_READY`` flags are reset around every database test because each
test gets a fresh connection.
"""

from __future__ import annotations

import asyncio
import contextlib
from types import SimpleNamespace

import aiosqlite
import discord
import pytest

import sniperplug.services.dm_deal_alerts as alerts
import sniperplug.services.user_wishlist as wishlist
from sniperplug.cogs.deal_scanner import HuntPreset
from sniperplug.cogs.wishlist import GROUP_DESCRIPTION, WishlistCog
from sniperplug.providers.base import ProviderScanResult
from sniperplug.models.candidate import SourceCandidate
from sniperplug.services import walmart_catalog_discovery_only as catalog
from sniperplug.services.dm_deal_alerts import DmDealAlertPreference
from sniperplug.services.dm_deal_matching import match_dm_deal
from sniperplug.services.user_wishlist import (
    MAX_WISHLIST_STEERING_TERMS,
    WishlistEntry,
    add_user_wishlist_item,
    list_user_wishlist,
    list_user_wishlist_entries,
    list_wishlists_for_users,
    list_wishlist_steering_terms,
    remove_user_wishlist_item,
)
from sniperplug.services.walmart_global_catalog_autoscan import GlobalCatalogClaim
from sniperplug.services.walmart_price_sniper_routes import (
    PRIORITY_SNIPER_QUERIES,
    priority_sniper_query_for_batch,
)


class _FakeDatabase:
    def __init__(self, conn):
        self.conn = conn

    def require_conn(self):
        return self.conn


@contextlib.asynccontextmanager
async def _isolated_db():
    wishlist._SCHEMA_READY = False
    alerts._SCHEMA_READY = False
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    try:
        yield _FakeDatabase(conn)
    finally:
        await conn.close()
        wishlist._SCHEMA_READY = False
        alerts._SCHEMA_READY = False


def _card(
    *,
    title: str = "Apple iPhone 11 64GB Green",
    current: float | None = 99.0,
    reference: float | None = 249.0,
    discount: float | None = 60.2,
    score: int = 120,
    retailer: str = "walmart",
):
    embed = discord.Embed(title=f"🔥 {discount or 0:.0f}% OFF • {title}")
    embed.add_field(name="Product Proof", value=f"Brand: {title.split()[0]}")
    return SimpleNamespace(
        label=title,
        url="https://www.walmart.com/ip/123",
        embed=embed,
        api_current_price=current,
        current_price=current,
        api_reference_price=reference,
        typical_price=reference,
        api_discount_percent=discount,
        discount=discount,
        score=score,
        retailer=retailer,
        variant_attributes={},
    )


def _preference(**overrides) -> DmDealAlertPreference:
    values = dict(
        user_id=42,
        enabled=True,
        mode="custom",
        min_discount=20,
        min_score=50,
        categories=(),
    )
    values.update(overrides)
    return DmDealAlertPreference(**values)


# -- target price: storage -----------------------------------------------


@pytest.mark.asyncio
async def test_add_with_target_stores_it_and_names_stay_plain() -> None:
    async with _isolated_db() as db:
        result = await add_user_wishlist_item(db, 42, "iphone 11", target_price_cents=15000)
        assert result.added is True
        assert result.target_price_cents == 15000

        entries = await list_user_wishlist_entries(db, 42)
        assert len(entries) == 1
        assert entries[0] == "iphone 11"
        assert entries[0].target_price_cents == 15000
        # The plain-names view is unchanged for existing consumers.
        assert await list_user_wishlist(db, 42) == ("iphone 11",)


@pytest.mark.asyncio
async def test_add_without_target_defaults_to_no_target() -> None:
    async with _isolated_db() as db:
        await add_user_wishlist_item(db, 42, "iphone 11")
        entries = await list_user_wishlist_entries(db, 42)
        assert entries[0].target_price_cents is None


@pytest.mark.asyncio
async def test_readd_with_a_target_updates_it_instead_of_duplicating() -> None:
    async with _isolated_db() as db:
        await add_user_wishlist_item(db, 42, "iphone 11")

        updated = await add_user_wishlist_item(db, 42, "IPHONE 11", target_price_cents=15000)
        assert updated.added is False
        assert updated.updated is True
        assert updated.target_price_cents == 15000
        assert await list_user_wishlist(db, 42) == ("iphone 11",)
        assert (await list_user_wishlist_entries(db, 42))[0].target_price_cents == 15000

        # A second re-add moves the target again.
        updated = await add_user_wishlist_item(db, 42, "iphone 11", target_price_cents=12000)
        assert updated.updated is True
        assert (await list_user_wishlist_entries(db, 42))[0].target_price_cents == 12000


@pytest.mark.asyncio
async def test_readd_without_a_target_keeps_the_existing_target() -> None:
    async with _isolated_db() as db:
        await add_user_wishlist_item(db, 42, "iphone 11", target_price_cents=15000)
        again = await add_user_wishlist_item(db, 42, "iphone 11")
        assert again.added is False
        assert again.reason == "duplicate"
        assert (await list_user_wishlist_entries(db, 42))[0].target_price_cents == 15000


@pytest.mark.asyncio
async def test_readd_with_a_target_works_even_at_the_cap() -> None:
    async with _isolated_db() as db:
        for index in range(wishlist.MAX_WISHLIST_ITEMS_PER_USER):
            await add_user_wishlist_item(db, 42, f"item {index}")
        updated = await add_user_wishlist_item(db, 42, "item 0", target_price_cents=500)
        assert updated.updated is True
        entries = await list_user_wishlist_entries(db, 42)
        assert entries[0].target_price_cents == 500
        assert len(entries) == wishlist.MAX_WISHLIST_ITEMS_PER_USER


@pytest.mark.asyncio
async def test_target_column_migrates_a_preexisting_table() -> None:
    """A wishlist table created before target prices existed gains the
    column additively; rows already in it keep working with no target."""

    wishlist._SCHEMA_READY = False
    alerts._SCHEMA_READY = False
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    db = _FakeDatabase(conn)
    try:
        await conn.execute(
            """
            CREATE TABLE user_wishlist_items (
                user_id TEXT NOT NULL,
                item TEXT NOT NULL,
                item_key TEXT NOT NULL,
                added_at TEXT NOT NULL,
                PRIMARY KEY (user_id, item_key)
            )
            """
        )
        await conn.execute(
            "INSERT INTO user_wishlist_items (user_id, item, item_key, added_at) "
            "VALUES ('42', 'iphone 11', 'iphone 11', '2026-01-01T00:00:00+00:00')"
        )
        await conn.commit()

        entries = await list_user_wishlist_entries(db, 42)
        assert len(entries) == 1
        assert entries[0] == "iphone 11"
        assert entries[0].target_price_cents is None

        # And new targeted adds work on the migrated table.
        await add_user_wishlist_item(db, 42, "dyson airwrap", target_price_cents=20000)
        entries = await list_user_wishlist_entries(db, 42)
        assert entries[1].target_price_cents == 20000
    finally:
        await conn.close()
        wishlist._SCHEMA_READY = False
        alerts._SCHEMA_READY = False


# -- target price: DM behaviour -------------------------------------------


def test_dm_fires_at_and_under_the_target() -> None:
    entry = WishlistEntry("iphone 11", target_price_cents=15000)
    at_target = _card(current=150.0, reference=300.0, discount=50.0)
    decision = match_dm_deal(_preference(), at_target, wishlist_items=(entry,))
    assert decision.matched is True
    assert "wishlist: iphone 11" in decision.reason
    assert "$150.00" in decision.reason
    assert "beat your $150.00 target" in decision.reason

    under_target = _card(current=129.99, reference=300.0, discount=56.7)
    decision = match_dm_deal(_preference(), under_target, wishlist_items=(entry,))
    assert decision.matched is True
    assert "$129.99" in decision.reason


def test_dm_does_not_fire_above_the_target() -> None:
    entry = WishlistEntry("iphone 11", target_price_cents=15000)
    card = _card(current=180.0, reference=300.0, discount=40.0)
    # The same deal matches fine with no target attached...
    assert match_dm_deal(_preference(), card, wishlist_items=("iphone 11",)).matched is True
    # ...but the target the user set for this exact item is a hard limit.
    decision = match_dm_deal(_preference(), card, wishlist_items=(entry,))
    assert decision.matched is False
    assert "target" in decision.reason


def test_dm_fires_on_any_match_when_no_target_is_set() -> None:
    entry = WishlistEntry("iphone 11")
    card = _card(current=180.0, reference=300.0, discount=40.0)
    decision = match_dm_deal(_preference(), card, wishlist_items=(entry,))
    assert decision.matched is True
    assert "wishlist: iphone 11" in decision.reason
    assert "target" not in decision.reason


@pytest.mark.asyncio
async def test_fanout_grouping_carries_targets_into_matching() -> None:
    async with _isolated_db() as db:
        await add_user_wishlist_item(db, 42, "iphone 11", target_price_cents=15000)
        grouped = await list_wishlists_for_users(db, [42])
        assert grouped[42] == ("iphone 11",)
        assert grouped[42][0].target_price_cents == 15000

        card = _card(current=180.0, reference=300.0, discount=40.0)
        assert match_dm_deal(_preference(), card, wishlist_items=grouped[42]).matched is False
        card = _card(current=140.0, reference=300.0, discount=53.3)
        assert match_dm_deal(_preference(), card, wishlist_items=grouped[42]).matched is True


# -- steering: term collection ---------------------------------------------


@pytest.mark.asyncio
async def test_steering_terms_dedupe_and_rank_by_user_count() -> None:
    async with _isolated_db() as db:
        await add_user_wishlist_item(db, 42, "Dyson Airwrap")
        await add_user_wishlist_item(db, 43, "dyson airwrap")
        await add_user_wishlist_item(db, 44, "dyson airwrap")
        await add_user_wishlist_item(db, 42, "iphone 11")
        await add_user_wishlist_item(db, 43, "iphone 11")
        await add_user_wishlist_item(db, 42, "rtx 5070")

        terms = await list_wishlist_steering_terms(db)
        assert terms == ("dyson airwrap", "iphone 11", "rtx 5070")


@pytest.mark.asyncio
async def test_steering_terms_are_capped() -> None:
    async with _isolated_db() as db:
        # 3 users x 20 items = 60 distinct terms, all tied at one user each;
        # the cap keeps the first 50 in deterministic (term) order.
        for user in (42, 43, 44):
            for index in range(20):
                await add_user_wishlist_item(db, user, f"gadget {user} {index}")
        terms = await list_wishlist_steering_terms(db)
        assert len(terms) == MAX_WISHLIST_STEERING_TERMS == 50


@pytest.mark.asyncio
async def test_removing_the_last_holder_unsteers_a_term() -> None:
    async with _isolated_db() as db:
        await add_user_wishlist_item(db, 42, "iphone 11")
        await add_user_wishlist_item(db, 43, "iphone 11")
        assert await list_wishlist_steering_terms(db) == ("iphone 11",)

        await remove_user_wishlist_item(db, 42, "iphone 11")
        assert await list_wishlist_steering_terms(db) == ("iphone 11",)

        await remove_user_wishlist_item(db, 43, "iphone 11")
        assert await list_wishlist_steering_terms(db) == ()


@pytest.mark.asyncio
async def test_no_wishlists_means_no_steering_terms() -> None:
    async with _isolated_db() as db:
        assert await list_wishlist_steering_terms(db) == ()


# -- steering: the priority slot -------------------------------------------


def test_priority_pool_rotates_through_wishlist_terms() -> None:
    terms = ("dyson airwrap",)
    seen: set[str] = set()
    for batch in range(0, 3 * (len(PRIORITY_SNIPER_QUERIES) + 1) * 2):
        query = priority_sniper_query_for_batch(batch, extra_queries=terms)
        if query is not None:
            seen.add(query)
    # The wishlist term takes priority slots, and every hot query still does.
    assert "dyson airwrap" in seen
    assert set(PRIORITY_SNIPER_QUERIES) <= seen


def test_priority_pool_without_terms_is_unchanged() -> None:
    for batch in range(0, 60):
        assert priority_sniper_query_for_batch(
            batch, extra_queries=()
        ) == priority_sniper_query_for_batch(batch)
        assert priority_sniper_query_for_batch(batch, avoid="iphone") == (
            priority_sniper_query_for_batch(batch, avoid="iphone", extra_queries=())
        )


def _claim(*, completed_routes: int = 0, total_routes: int = 279) -> GlobalCatalogClaim:
    return GlobalCatalogClaim(
        token="tok",
        start_index=0,
        queries=("query-0", "query-1"),
        total_routes=total_routes,
        completed_routes_before=completed_routes,
        completed_passes_before=0,
    )


def test_plan_schedules_a_wishlist_term_on_the_priority_cadence() -> None:
    # Batch 18 is a priority firing whose rotation index lands on the first
    # appended wishlist term (6 hot queries + 1 term; firing 6 -> index 6).
    claim = _claim(completed_routes=36)
    baseline = catalog.catalog_request_plan_for_claim(claim)
    steered = catalog.catalog_request_plan_for_claim(
        claim, wishlist_terms=("dyson airwrap",)
    )
    assert len(steered) == 4
    # Broad routes are untouched; only the priority slot changes hands.
    assert steered[0] == baseline[0]
    assert steered[1] == baseline[1]
    assert steered[2] == baseline[2]
    assert steered[3] == ("dyson airwrap", 1, "price", "ascending")
    assert baseline[3][0] in PRIORITY_SNIPER_QUERIES


def test_plan_without_terms_matches_baseline_for_many_batches() -> None:
    for completed in range(0, 240, 2):
        claim = _claim(completed_routes=completed)
        assert catalog.catalog_request_plan_for_claim(
            claim, wishlist_terms=()
        ) == catalog.catalog_request_plan_for_claim(claim)


def test_discover_reads_steering_terms_and_notes_them(monkeypatch) -> None:
    async def run() -> None:
        requests: list[tuple] = []

        async def scan(query, page, _page_size, sort, order, _requested_by):
            requests.append((query, page, sort, order))
            idx = len(requests)
            return ProviderScanResult(
                provider_key="walmart",
                candidates=(SourceCandidate(
                    source_key="walmart",
                    retailer="Walmart",
                    title=f"Fresh generic product {idx}",
                    product_url=f"https://www.walmart.com/ip/{800000 + idx}",
                    product_id=str(800000 + idx),
                    current_price=1.00,
                ),),
            )

        async def enqueue(_db, candidates, *, min_discount, source_label):
            return SimpleNamespace(summary_line=lambda: "queue saved")

        async def terms(_db, **_kwargs):
            return ("dyson airwrap",)

        monkeypatch.setattr(catalog, "run_walmart_autoscan_scan_off_event_loop", scan)
        monkeypatch.setattr(
            catalog, "enqueue_walmart_exact_verification_candidates_bulk", enqueue
        )
        monkeypatch.setattr(catalog, "list_wishlist_steering_terms", terms)

        claim = _claim(completed_routes=36)
        preset = HuntPreset(
            "global_catalog_autoscan", "Walmart", "x", "fast", claim.queries, 10
        )
        output = await catalog.discover_walmart_catalog_candidates(
            requested_by="global_catalog_autoscan",
            preset=preset, db=object(), min_discount=10, catalog_claim=claim,
        )
        assert ("dyson airwrap", 1, "price", "ascending") in requests
        assert len(requests) == 4
        assert any(
            "wishlist steering" in note and "**1**" in note
            for note in output.warnings
        )

    asyncio.run(run())


def test_discover_without_wishlist_terms_has_no_steering_note(monkeypatch) -> None:
    async def run() -> None:
        requests: list[tuple] = []

        async def scan(query, page, _page_size, sort, order, _requested_by):
            requests.append((query, page, sort, order))
            idx = len(requests)
            return ProviderScanResult(
                provider_key="walmart",
                candidates=(SourceCandidate(
                    source_key="walmart",
                    retailer="Walmart",
                    title=f"Fresh generic product {idx}",
                    product_url=f"https://www.walmart.com/ip/{800000 + idx}",
                    product_id=str(800000 + idx),
                    current_price=1.00,
                ),),
            )

        async def enqueue(_db, candidates, *, min_discount, source_label):
            return SimpleNamespace(summary_line=lambda: "queue saved")

        monkeypatch.setattr(catalog, "run_walmart_autoscan_scan_off_event_loop", scan)
        monkeypatch.setattr(
            catalog, "enqueue_walmart_exact_verification_candidates_bulk", enqueue
        )

        claim = _claim(completed_routes=36)
        preset = HuntPreset(
            "global_catalog_autoscan", "Walmart", "x", "fast", claim.queries, 10
        )
        # db=object() cannot serve wishlist reads: steering must degrade to
        # today's exact plan instead of failing the pass.
        output = await catalog.discover_walmart_catalog_candidates(
            requested_by="global_catalog_autoscan",
            preset=preset, db=object(), min_discount=10, catalog_claim=claim,
        )
        assert sorted(requests) == sorted(catalog.catalog_request_plan_for_claim(claim))
        assert not any("wishlist steering" in note for note in output.warnings)

    asyncio.run(run())


# -- cog: target price surface ----------------------------------------------


class _FakeResponse:
    def __init__(self) -> None:
        self.sent_message = None

    def is_done(self) -> bool:
        return True

    async def defer(self, *, ephemeral: bool = False) -> None:
        return None

    async def send_message(self, message: str, *, ephemeral: bool = False) -> None:
        self.sent_message = message


class _FakeFollowup:
    def __init__(self) -> None:
        self.sent: list[tuple[tuple, dict]] = []

    async def send(self, *args, **kwargs) -> None:
        self.sent.append((args, kwargs))


class _FakeUser:
    def __init__(self) -> None:
        self.id = 42
        self.dm_sends: list[dict] = []

    async def send(self, *args, **kwargs) -> None:
        self.dm_sends.append(kwargs)


class _FakeInteraction:
    def __init__(self, *, user=None) -> None:
        self.user = user or _FakeUser()
        self.guild_id = 456
        self.response = _FakeResponse()
        self.followup = _FakeFollowup()


def _followup_text(interaction: _FakeInteraction) -> str:
    assert len(interaction.followup.sent) == 1
    args, kwargs = interaction.followup.sent[0]
    return str(args[0] if args else kwargs.get("content", ""))


@pytest.mark.asyncio
async def test_cog_add_with_target_then_list_shows_it() -> None:
    async with _isolated_db() as db:
        cog = WishlistCog(bot=SimpleNamespace(db=db))
        interaction = _FakeInteraction()
        await cog.add.callback(cog, interaction, item="iphone 11", target_price=150.0)
        assert "$150.00" in _followup_text(interaction)
        entries = await list_user_wishlist_entries(db, 42)
        assert entries[0].target_price_cents == 15000

        interaction = _FakeInteraction()
        await cog.list_items.callback(cog, interaction)
        _, kwargs = interaction.followup.sent[0]
        embed = kwargs["embed"]
        assert "iphone 11" in (embed.description or "")
        assert "iphone 11 — under $150" in (embed.description or "")


@pytest.mark.asyncio
async def test_cog_readd_with_a_new_target_updates_it() -> None:
    async with _isolated_db() as db:
        cog = WishlistCog(bot=SimpleNamespace(db=db))
        interaction = _FakeInteraction()
        await cog.add.callback(cog, interaction, item="iphone 11", target_price=150.0)

        interaction = _FakeInteraction()
        await cog.add.callback(cog, interaction, item="iphone 11", target_price=120.0)
        text = _followup_text(interaction)
        assert "Updated" in text
        assert "$120.00" in text
        assert (await list_user_wishlist_entries(db, 42))[0].target_price_cents == 12000
        assert await list_user_wishlist(db, 42) == ("iphone 11",)


@pytest.mark.asyncio
async def test_cog_rejects_a_nonsense_target_price() -> None:
    async with _isolated_db() as db:
        cog = WishlistCog(bot=SimpleNamespace(db=db))
        interaction = _FakeInteraction()
        await cog.add.callback(cog, interaction, item="iphone 11", target_price=0)
        assert await list_user_wishlist(db, 42) == ()
        assert "target" in _followup_text(interaction).lower()


def test_wishlist_descriptions_stay_within_discord_limits() -> None:
    """A >100-char description silently killed the whole command sync once;
    every description this feature touches stays under the cap."""

    assert len(GROUP_DESCRIPTION) <= 100
    cog = WishlistCog(bot=None)
    for command in cog.app_command.commands:
        assert len(command.description or "") <= 100, command.name
        for parameter in command.parameters:
            assert len(parameter.description or "") <= 100, (
                command.name,
                parameter.name,
            )
