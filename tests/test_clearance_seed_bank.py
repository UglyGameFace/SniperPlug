import asyncio

from sniperplug.storage.db import Database


def test_clearance_seed_storage_round_trip(tmp_path):
    async def run_check():
        db = Database(str(tmp_path / "sniperplug.db"))
        await db.connect()
        await db.init()
        try:
            seed_id = await db.add_clearance_seed(
                guild_id=123,
                user_id=456,
                retailer="home_depot",
                title="Milwaukee clearance drill",
                sku="1001234567",
                store_id="6237",
                zip_code="06610",
                observed_price=5.03,
                notes="yellow tag endcap",
            )
            seeds = await db.list_clearance_seeds(123, retailer="home_depot", limit=10)
            assert len(seeds) == 1
            assert seeds[0]["seed_id"] == seed_id
            assert seeds[0]["title"] == "Milwaukee clearance drill"
            assert seeds[0]["sku"] == "1001234567"
            assert seeds[0]["store_id"] == "6237"
            assert seeds[0]["observed_price"] == 5.03
        finally:
            await db.close()

    asyncio.run(run_check())


def test_clearance_seed_list_is_guild_scoped(tmp_path):
    async def run_check():
        db = Database(str(tmp_path / "sniperplug.db"))
        await db.connect()
        await db.init()
        try:
            await db.add_clearance_seed(1, 10, "home_depot", "Guild one seed")
            await db.add_clearance_seed(2, 10, "home_depot", "Guild two seed")
            seeds = await db.list_clearance_seeds(1, limit=10)
            assert len(seeds) == 1
            assert seeds[0]["title"] == "Guild one seed"
        finally:
            await db.close()

    asyncio.run(run_check())
