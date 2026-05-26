import aiosqlite
import pytest

from sniperplug.services.fresh_deal_filter import select_fresh_deal_cards
from sniperplug.services.public_deal_posts import cache_active_deal_cards, ensure_public_post_tables


class DummyDb:
    def __init__(self, conn):
        self.conn = conn

    def require_conn(self):
        return self.conn


class Card:
    def __init__(self, *, sku, price):
        self.retailer = "walmart"
        self.url = f"https://www.walmart.com/ip/{sku}"
        self.selected_offer_id = None
        self.sku = sku
        self.upc = None
        self.current_price = price
        self.discount = 50
        self.score = 100
        self.label = sku


@pytest.mark.asyncio
async def test_fresh_filter_hides_repeat_same_price():
    async with aiosqlite.connect(":memory:") as conn:
        conn.row_factory = aiosqlite.Row
        db = DummyDb(conn)
        await ensure_public_post_tables(db)
        await cache_active_deal_cards(db, guild_id=1, cards=[Card(sku="a", price=10.0)], source_label="test", fallback_retailer="walmart")

        selection = await select_fresh_deal_cards(db, guild_id=1, cards=[Card(sku="a", price=10.0)], limit=5)

        assert selection.fresh == []
        assert selection.repeated_same_or_higher_price == 1


@pytest.mark.asyncio
async def test_fresh_filter_allows_lower_price_repeat():
    async with aiosqlite.connect(":memory:") as conn:
        conn.row_factory = aiosqlite.Row
        db = DummyDb(conn)
        await ensure_public_post_tables(db)
        await cache_active_deal_cards(db, guild_id=1, cards=[Card(sku="a", price=10.0)], source_label="test", fallback_retailer="walmart")
        new_card = Card(sku="a", price=8.0)

        selection = await select_fresh_deal_cards(db, guild_id=1, cards=[new_card], limit=5)

        assert selection.fresh == [new_card]
        assert selection.lower_price_repeats == 1


@pytest.mark.asyncio
async def test_fresh_filter_allows_new_product():
    async with aiosqlite.connect(":memory:") as conn:
        conn.row_factory = aiosqlite.Row
        db = DummyDb(conn)
        await ensure_public_post_tables(db)
        new_card = Card(sku="b", price=20.0)

        selection = await select_fresh_deal_cards(db, guild_id=1, cards=[new_card], limit=5)

        assert selection.fresh == [new_card]
