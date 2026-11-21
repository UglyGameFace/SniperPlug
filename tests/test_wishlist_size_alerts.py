"""Tests for wishlist size alerts.

A wishlist item may carry a size. The size is a fail-closed hard guard
in the DM matcher, beside the target-price guard: the DM fires only when
the deal's proven variant size — the structured variant proof, never
embed text — normalizes to the same size, and an unproven deal size
never DMs. Entries without a size behave exactly as before.
"""

from __future__ import annotations

import contextlib
from types import SimpleNamespace

import aiosqlite
import discord
import pytest

import sniperplug.services.dm_deal_alerts as alerts
import sniperplug.services.user_wishlist as wishlist
from sniperplug.cogs.wishlist import WishlistCog
from sniperplug.services.dm_deal_alerts import DmDealAlertPreference
from sniperplug.services.dm_deal_matching import match_dm_deal
from sniperplug.services.user_wishlist import (
    WishlistEntry,
    add_user_wishlist_item,
    list_user_wishlist,
    list_user_wishlist_entries,
    list_wishlists_for_users,
    normalize_wishlist_size,
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


def _card(*, variant_attributes=None, extra_embed_field: bool = False):
    embed = discord.Embed(title="🔥 60% OFF • Nike Shoes Runner")
    embed.add_field(name="Product Proof", value="Brand: Nike")
    if extra_embed_field:
        embed.add_field(name="Selected option", value="Size: 10")
    return SimpleNamespace(
        label="Nike Shoes Runner",
        url="https://www.walmart.com/ip/123",
        embed=embed,
        api_current_price=99.0,
        current_price=99.0,
        api_reference_price=249.0,
        typical_price=249.0,
        api_discount_percent=60.2,
        discount=60.2,
        score=120,
        retailer="walmart",
        variant_attributes=variant_attributes or {},
    )


def _preference() -> DmDealAlertPreference:
    return DmDealAlertPreference(
        user_id=42,
        enabled=True,
        min_discount=20,
        categories=(),
    )


# -- normalization ----------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("10", "10"),
        ("10.0", "10"),
        ("10.5", "10.5"),
        ("US 10", "10"),
        ("Size 10.5", "10.5"),
        ("10 1/2", "10.5"),
        ("3Y", "3Y"),
        ("3 Y", "3Y"),
        ("3.5y", "3.5Y"),
        ("12C", "12C"),
        ("Large", "L"),
        ("extra large", "XL"),
        ("X-Large", "XL"),
        ("XXL", "2XL"),
        ("32x30", "32X30"),
        ("32 x 30", "32X30"),
        ("W32 L30", "32X30"),
        ("32W x 30L", "32X30"),
        ("0-3M", "0-3M"),
        ("", None),
        (None, None),
    ],
)
def test_size_normalization(raw, expected) -> None:
    assert normalize_wishlist_size(raw) == expected


# -- storage ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_with_size_stores_the_canonical_size() -> None:
    async with _isolated_db() as db:
        result = await add_user_wishlist_item(db, 42, "nike shoes", size_text="US 10.5")
        assert result.added is True
        assert result.size_text == "10.5"
        entries = await list_user_wishlist_entries(db, 42)
        assert entries[0].size_text == "10.5"
        # Names stay plain strings for every existing consumer.
        assert await list_user_wishlist(db, 42) == ("nike shoes",)


@pytest.mark.asyncio
async def test_add_without_size_defaults_to_no_size_guard() -> None:
    async with _isolated_db() as db:
        await add_user_wishlist_item(db, 42, "nike shoes")
        entries = await list_user_wishlist_entries(db, 42)
        assert entries[0].size_text is None


@pytest.mark.asyncio
async def test_readd_with_a_size_updates_it_and_bare_readd_keeps_it() -> None:
    async with _isolated_db() as db:
        await add_user_wishlist_item(db, 42, "nike shoes", size_text="10")
        updated = await add_user_wishlist_item(db, 42, "Nike Shoes", size_text="11")
        assert updated.updated is True
        assert updated.size_text == "11"
        assert (await list_user_wishlist_entries(db, 42))[0].size_text == "11"

        duplicate = await add_user_wishlist_item(db, 42, "nike shoes")
        assert duplicate.reason == "duplicate"
        assert duplicate.size_text == "11"
        assert (await list_user_wishlist_entries(db, 42))[0].size_text == "11"
        assert await list_user_wishlist(db, 42) == ("nike shoes",)


@pytest.mark.asyncio
async def test_size_column_migrates_a_preexisting_table() -> None:
    """A wishlist table created before sizes existed gains the column
    additively; rows already in it keep working with no size guard."""

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
                target_price_cents INTEGER,
                PRIMARY KEY (user_id, item_key)
            )
            """
        )
        await conn.execute(
            "INSERT INTO user_wishlist_items "
            "(user_id, item, item_key, added_at, target_price_cents) "
            "VALUES ('42', 'nike shoes', 'nike shoes', "
            "'2026-01-01T00:00:00+00:00', NULL)"
        )
        await conn.commit()

        entries = await list_user_wishlist_entries(db, 42)
        assert len(entries) == 1
        assert entries[0].size_text is None

        await add_user_wishlist_item(db, 42, "adidas shoes", size_text="9")
        entries = await list_user_wishlist_entries(db, 42)
        assert entries[1].size_text == "9"
    finally:
        await conn.close()
        wishlist._SCHEMA_READY = False
        alerts._SCHEMA_READY = False


@pytest.mark.asyncio
async def test_fanout_grouping_carries_sizes_into_matching() -> None:
    async with _isolated_db() as db:
        await add_user_wishlist_item(db, 42, "nike shoes", size_text="10")
        grouped = await list_wishlists_for_users(db, [42])
        assert grouped[42][0].size_text == "10"
        card = _card(variant_attributes={"size": "10"})
        assert match_dm_deal(_preference(), card, wishlist_items=grouped[42]).matched is True
        card = _card(variant_attributes={"size": "12"})
        assert match_dm_deal(_preference(), card, wishlist_items=grouped[42]).matched is False


# -- DM behaviour: the fail-closed size guard ---------------------------------


def test_dm_fires_when_the_proven_size_matches() -> None:
    entry = WishlistEntry("nike shoes", size_text="10")
    decision = match_dm_deal(
        _preference(),
        _card(variant_attributes={"size": "10"}),
        wishlist_items=(entry,),
    )
    assert decision.matched is True
    assert "size 10" in decision.reason


def test_dm_fires_across_equivalent_size_spellings() -> None:
    entry = WishlistEntry("nike shoes", size_text="10")
    decision = match_dm_deal(
        _preference(),
        _card(variant_attributes={"size": "US 10.0"}),
        wishlist_items=(entry,),
    )
    assert decision.matched is True

    entry = WishlistEntry("nike shoes", size_text="XL")
    decision = match_dm_deal(
        _preference(),
        _card(variant_attributes={"size": "Extra Large"}),
        wishlist_items=(entry,),
    )
    assert decision.matched is True


def test_dm_does_not_fire_on_a_size_mismatch() -> None:
    entry = WishlistEntry("nike shoes", size_text="10")
    decision = match_dm_deal(
        _preference(),
        _card(variant_attributes={"size": "11"}),
        wishlist_items=(entry,),
    )
    assert decision.matched is False
    assert "size" in decision.reason


def test_dm_does_not_fire_when_the_deal_size_is_unproven() -> None:
    entry = WishlistEntry("nike shoes", size_text="10")
    decision = match_dm_deal(
        _preference(),
        _card(variant_attributes={}),
        wishlist_items=(entry,),
    )
    assert decision.matched is False
    assert "unknown" in decision.reason


def test_dm_never_reads_size_from_embed_text() -> None:
    """A size that appears only in an embed field is not proof: the
    guard reads the structured variant proof and fails closed."""

    entry = WishlistEntry("nike shoes", size_text="10")
    decision = match_dm_deal(
        _preference(),
        _card(variant_attributes={}, extra_embed_field=True),
        wishlist_items=(entry,),
    )
    assert decision.matched is False


def test_no_size_entry_behaves_exactly_as_before() -> None:
    entry = WishlistEntry("nike shoes")
    for attributes in ({"size": "10"}, {"size": "13"}, {}):
        decision = match_dm_deal(
            _preference(),
            _card(variant_attributes=attributes),
            wishlist_items=(entry,),
        )
        assert decision.matched is True


# -- cog: size surface ---------------------------------------------------------


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
async def test_cog_add_with_size_then_list_shows_it() -> None:
    async with _isolated_db() as db:
        cog = WishlistCog(bot=SimpleNamespace(db=db))
        interaction = _FakeInteraction()
        await cog.add.callback(cog, interaction, item="nike shoes", size="10.5")
        text = _followup_text(interaction)
        assert "Size set" in text
        assert "10.5" in text
        entries = await list_user_wishlist_entries(db, 42)
        assert entries[0].size_text == "10.5"

        interaction = _FakeInteraction()
        await cog.list_items.callback(cog, interaction)
        _, kwargs = interaction.followup.sent[0]
        embed = kwargs["embed"]
        assert "nike shoes" in (embed.description or "")
        assert "nike shoes — size 10.5" in (embed.description or "")


@pytest.mark.asyncio
async def test_cog_readd_with_a_new_size_updates_it() -> None:
    async with _isolated_db() as db:
        cog = WishlistCog(bot=SimpleNamespace(db=db))
        interaction = _FakeInteraction()
        await cog.add.callback(cog, interaction, item="nike shoes", size="10")

        interaction = _FakeInteraction()
        await cog.add.callback(cog, interaction, item="nike shoes", size="11")
        text = _followup_text(interaction)
        assert "Updated" in text
        assert "size is 11" in text
        assert (await list_user_wishlist_entries(db, 42))[0].size_text == "11"
        assert await list_user_wishlist(db, 42) == ("nike shoes",)
