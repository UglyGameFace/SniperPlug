"""Production-chain regression scenarios for Walmart deal delivery.

Every test drives the real discovery -> exact queue -> verification ->
classification -> persistence -> lane -> Discord-send chain through
``tests/walmart_pipeline_harness.py``. Only Walmart's HTTP endpoint and
Discord's gateway objects are simulated.
"""

from __future__ import annotations

import pytest

from walmart_pipeline_harness import Pipeline, walmart_item


TAB = 'Samsung Galaxy Tab S11 11" 128GB Wi-Fi Tablet'


async def _pipeline(tmp_path, monkeypatch, **kw) -> Pipeline:
    return await Pipeline(tmp_path, monkeypatch, **kw).start()


def _titles(posts) -> list[str]:
    return [str(getattr(embed, "title", "") or "") for embed in posts]


def _text(embed) -> str:
    return str(embed.to_dict()).lower()


# --------------------------------------------------------------- deal quality
@pytest.mark.asyncio
async def test_one_dollar_premium_tablet_with_was_price_posts_markdown(tmp_path, monkeypatch):
    p = await _pipeline(tmp_path, monkeypatch)
    try:
        await p.enroll(p.guilds[0], threshold=40)
        p.api.add(walmart_item(9002, TAB, 1.00, was_price=899.99), queries=("samsung galaxy tab",))
        await p.discover("samsung galaxy tab")
        await p.cycle()
        row = await p.queue_row(9002)
        assert row["status"] == "verified_markdown"
        titles = _titles(p.posts())
        assert len(titles) == 1 and "Galaxy Tab S11" in titles[0]
        # A paid $1 offer is not "100% OFF".
        assert "100% OFF" not in titles[0] and "99.9% OFF" in titles[0]
    finally:
        await p.close()


@pytest.mark.asyncio
async def test_one_dollar_tablet_without_trusted_reference_is_opt_in_exact_lead(tmp_path, monkeypatch):
    p = await _pipeline(tmp_path, monkeypatch, guild_ids=(111000, 222000))
    try:
        opted, plain = p.guilds
        await p.enroll(opted, threshold=40, extreme=True)
        await p.enroll(plain, threshold=40, extreme=False)
        p.api.add(walmart_item(9001, TAB, 1.00, msrp=799.99), queries=("samsung galaxy tab",))
        await p.discover("samsung galaxy tab")
        await p.cycle()
        assert (await p.queue_row(9001))["status"] == "verified_no_reference"
        assert len(p.posts(opted)) == 1
        embed = p.posts(opted)[0]
        assert embed.title.startswith("Extreme exact-price find")
        # No fabricated percentage; Walmart's list price shown only as context.
        assert "% off" not in _text(embed)
        assert "not a verified" in _text(embed)
        assert p.posts(plain) == []
    finally:
        await p.close()


@pytest.mark.parametrize(
    "item, query",
    [
        # $600 TV at $49 where Walmart only returns an (untrusted) MSRP.
        (walmart_item(9103, 'TCL 65" Class 4K UHD Smart TV', 49.00, msrp=599.99,
                      category="Electronics/TV & Video/Televisions"), "65 inch 4k smart tv"),
        # Power tool: not in any product-type list, sold by Walmart.
        (walmart_item(9105, "DEWALT 20V MAX Cordless Drill Driver Kit", 2.00, msrp=159.00,
                      category="Home Improvement/Tools/Power Tools/Drills"), "dewalt power tool kit"),
        # Collectible: not hardware at all.
        (walmart_item(9109, "LEGO Star Wars Millennium Falcon 75257 Building Set", 3.00, msrp=169.99,
                      category="Toys/Building Sets/LEGO"), "lego set"),
        # Product type the code has never heard of (overfitting guard).
        (walmart_item(9110, "Breville Barista Express Espresso Machine BES870XL", 25.00, msrp=749.95,
                      category="Home/Kitchen Appliances/Coffee"), "espresso machine"),
    ],
)
@pytest.mark.asyncio
async def test_listed_price_anomalies_reach_opt_in_lane_at_any_price(tmp_path, monkeypatch, item, query):
    p = await _pipeline(tmp_path, monkeypatch)
    try:
        await p.enroll(p.guilds[0], threshold=40, extreme=True)
        p.api.add(item, queries=(query,))
        await p.discover(query)
        await p.cycle()
        assert (await p.queue_row(item["itemId"]))["status"] == "verified_no_reference"
        posts = p.posts()
        assert len(posts) == 1, "verified anomaly must not be stranded between lanes"
        assert f"${item['msrp']:,.2f}" in str(posts[0].to_dict())
    finally:
        await p.close()


@pytest.mark.parametrize(
    "item, query",
    [
        # Ordinary inexpensive merchandise.
        (walmart_item(9201, "Sterling Silver Heart Pendant Necklace", 9.92, msrp=29.99,
                      category="Jewelry/Necklaces"), "necklace"),
        # Marketplace seller with an inflated "MSRP" on ordinary jewelry.
        (walmart_item(9202, "Cubic Zirconia Stud Earrings", 10.00, msrp=250.00,
                      category="Jewelry/Earrings", seller="Glam Deals LLC", marketplace=True), "earrings"),
        # Digital credit sold at face value.
        (walmart_item(9203, "Razer Gold $10 Digital Code", 10.00, category="Video Games/Digital Content"), "razer gold"),
        # Subscription.
        (walmart_item(9204, "EA Play 1 Month Subscription Digital", 5.99, msrp=59.99,
                      category="Video Games/Subscriptions"), "ea play"),
        # Game code that mentions a console name.
        (walmart_item(9205, "Mario Kart 8 Deluxe Nintendo Switch Digital Game Code", 9.97,
                      category="Video Games/Nintendo Switch Games"), "mario kart"),
        # Accessory inheriting a premium device name.
        (walmart_item(9206, "Galaxy Tab S11 Case Cover", 1.00, msrp=59.99,
                      category="Electronics/Tablet Accessories/Cases"), "galaxy tab case"),
    ],
)
@pytest.mark.asyncio
async def test_low_value_merchandise_never_posts_as_extreme_find(tmp_path, monkeypatch, item, query):
    p = await _pipeline(tmp_path, monkeypatch)
    try:
        await p.enroll(p.guilds[0], threshold=40, extreme=True)
        p.api.add(item, queries=(query,))
        await p.discover(query)
        await p.cycle()
        # Either verified-and-rejected or fail-closed (unproven marketplace
        # shipping) is acceptable; a public post is not.
        assert (await p.queue_row(item["itemId"])) is not None
        assert p.posts() == []
    finally:
        await p.close()


@pytest.mark.asyncio
async def test_physical_controller_without_model_list_is_recognized(tmp_path, monkeypatch):
    p = await _pipeline(tmp_path, monkeypatch)
    try:
        await p.enroll(p.guilds[0], threshold=40, extreme=True)
        p.api.add(
            walmart_item(9301, "Zyrax Pro Wireless Game Controller for PC", 1.00,
                         category="Video Games/Accessories/Controllers"),
            queries=("controller",),
        )
        await p.discover("controller")
        await p.cycle()
        assert len(p.posts()) == 1
    finally:
        await p.close()


# ------------------------------------------------------- markdown thresholds
@pytest.mark.asyncio
async def test_threshold_is_respected_per_server(tmp_path, monkeypatch):
    p = await _pipeline(tmp_path, monkeypatch, guild_ids=(111000, 222000))
    try:
        loose, strict = p.guilds
        await p.enroll(loose, threshold=40)
        await p.enroll(strict, threshold=60)
        p.api.add(walmart_item(9401, "KitchenAid Artisan 5 Qt Stand Mixer", 239.00, was_price=449.99,
                               category="Home/Kitchen Appliances/Mixers"), queries=("mixer",))
        await p.discover("mixer")
        await p.cycle()
        assert len(p.posts(loose)) == 1 and "47% OFF" in p.posts(loose)[0].title
        assert p.posts(strict) == []
    finally:
        await p.close()


@pytest.mark.asyncio
async def test_repeated_discovery_does_not_repost(tmp_path, monkeypatch):
    p = await _pipeline(tmp_path, monkeypatch)
    try:
        await p.enroll(p.guilds[0], threshold=40)
        p.api.add(walmart_item(9402, "Dyson V8 Cordless Vacuum", 199.00, was_price=469.99,
                               category="Home/Vacuums"), queries=("dyson",))
        for _ in range(3):
            await p.discover("dyson")
            await p.make_due(9402)
            await p.cycle()
        assert len(p.posts()) == 1
    finally:
        await p.close()


# -------------------------------------------------- observed-price memory lane
@pytest.mark.asyncio
async def test_observed_price_drop_on_watched_item_reaches_markdown_lane(tmp_path, monkeypatch):
    """Generic anomaly detection without any MSRP: SniperPlug's own history."""
    p = await _pipeline(tmp_path, monkeypatch)
    try:
        await p.enroll(p.guilds[0], threshold=40)
        item_id = p.api.add(
            walmart_item(9501, "Ninja Foodi 8-in-1 Pressure Cooker", 179.00,
                         category="Home/Kitchen Appliances/Pressure Cookers"),
            queries=("ninja foodi",),
        )
        await p.discover("ninja foodi")
        await p.cycle()  # first observation
        for _ in range(2):  # two further confirmations >= 4h apart
            await p.age_offer_memory(item_id, hours=5)
            await p.make_due(item_id)
            await p.cycle()
        assert (await p.queue_row(item_id))["status"] == "verified_no_reference"
        assert p.posts() == []

        # The glitch is seen the way production sees it: catalog rediscovery
        # with a material price change wakes the exact row immediately.
        p.api.set_price(item_id, 59.00)
        await p.discover("ninja foodi")
        await p.cycle()
        row = await p.queue_row(item_id)
        assert row["status"] == "verified_markdown"
        assert row["exact_reference_cents"] == 17900
        posts = p.posts()
        assert len(posts) == 1
        assert "observed" in _text(posts[0])
    finally:
        await p.close()


@pytest.mark.asyncio
async def test_unconfirmed_history_never_creates_a_markdown(tmp_path, monkeypatch):
    p = await _pipeline(tmp_path, monkeypatch)
    try:
        await p.enroll(p.guilds[0], threshold=40)
        item_id = p.api.add(
            walmart_item(9502, "Ninja Foodi 6-in-1 Air Fryer", 129.00, category="Home/Kitchen Appliances"),
            queries=("ninja air fryer",),
        )
        await p.discover("ninja air fryer")
        await p.cycle()
        p.api.set_price(item_id, 39.00)  # one prior sighting is not a stable price
        await p.discover("ninja air fryer")
        await p.cycle()
        assert (await p.queue_row(item_id))["status"] == "verified_no_reference"
        assert p.posts() == []
    finally:
        await p.close()


# ----------------------------------------------------------- offer integrity
@pytest.mark.asyncio
async def test_out_of_stock_and_not_online_never_post(tmp_path, monkeypatch):
    p = await _pipeline(tmp_path, monkeypatch)
    try:
        await p.enroll(p.guilds[0], threshold=40, extreme=True)
        p.api.add(walmart_item(9601, TAB, 1.00, msrp=799.99, stock="Not available"), queries=("tab oos",))
        p.api.add(walmart_item(9602, 'Samsung 65" QLED 4K TV', 9.00, was_price=899.99,
                               available_online=False, category="Electronics/Televisions"), queries=("tab oos",))
        await p.discover("tab oos")
        await p.cycle()
        assert p.posts() == []
    finally:
        await p.close()


@pytest.mark.asyncio
async def test_batched_lookup_partial_response_recovers_missing_items_individually(tmp_path, monkeypatch):
    p = await _pipeline(tmp_path, monkeypatch)
    try:
        await p.enroll(p.guilds[0], threshold=40)
        ids = [
            p.api.add(walmart_item(9700 + n, f"Acme Robot Vacuum Model {n}", 99.00, was_price=299.00,
                                   category="Home/Vacuums/Robot Vacuums"), queries=("acme robot",))
            for n in range(5)
        ]
        p.api.lookup_failures[ids[2]] = 1  # absent from the batch response only
        await p.discover("acme robot")
        await p.cycle()
        statuses = [(await p.queue_row(i))["status"] for i in ids]
        assert statuses == ["verified_markdown"] * len(ids)
        batch_calls = [r for r in p.api.requests if r.startswith("lookup:") and "," in r]
        # One batched Product Lookup for the claim; the item it omitted is
        # still proved on its own (fresh catalog proof or a single lookup),
        # never by borrowing another item's payload.
        assert len(batch_calls) == 1, p.api.requests
        assert len(p.posts()) == 5
    finally:
        await p.close()


# --------------------------------------------------------- delivery safety
@pytest.mark.asyncio
async def test_every_enrolled_server_receives_the_same_markdown(tmp_path, monkeypatch):
    """Regression: the first server's quality gate mutated the shared card and
    every later server rejected it as 'review_or_watchlist'."""
    p = await _pipeline(tmp_path, monkeypatch, guild_ids=(111000, 222000, 333000))
    try:
        for guild in p.guilds:
            await p.enroll(guild, threshold=40)
        p.api.add(walmart_item(9800, "Apple iPad Air 11-inch M2", 99.00, was_price=599.00,
                               category="Electronics/Tablets"), queries=("ipad air m2",))
        await p.discover("ipad air m2")
        await p.cycle()
        assert [len(p.posts(g)) for g in p.guilds] == [1, 1, 1]
    finally:
        await p.close()


@pytest.mark.asyncio
async def test_markdown_waits_when_enrolled_guild_is_temporarily_absent(tmp_path, monkeypatch):
    """Gateway shows zero guilds: the verified deal must not be acknowledged."""
    p = await _pipeline(tmp_path, monkeypatch)
    try:
        guild = p.guilds[0]
        await p.enroll(guild, threshold=40)
        p.api.add(walmart_item(9801, "Apple iPad 10th Gen 64GB", 49.00, was_price=349.00,
                               category="Electronics/Tablets"), queries=("ipad",))
        await p.discover("ipad")
        p.bot.guilds = []
        await p.cycle()
        assert p.posts(guild) == []
        p.bot.guilds = [guild]
        await p.expire_event_leases()  # deferral backoff elapsed
        await p.cycle()
        assert len(p.posts(guild)) == 1
    finally:
        await p.close()


@pytest.mark.asyncio
async def test_markdown_reaches_guild_that_was_missing_from_live_set(tmp_path, monkeypatch):
    p = await _pipeline(tmp_path, monkeypatch, guild_ids=(111000, 222000))
    try:
        present, absent = p.guilds
        await p.enroll(present, threshold=40)
        await p.enroll(absent, threshold=40)
        p.api.add(walmart_item(9802, "Apple iPad Air 11-inch M2", 99.00, was_price=599.00,
                               category="Electronics/Tablets"), queries=("ipad air",))
        await p.discover("ipad air")
        p.bot.guilds = [present]
        await p.cycle()
        assert len(p.posts(present)) == 1 and p.posts(absent) == []
        p.bot.guilds = [present, absent]
        await p.expire_event_leases()
        await p.cycle()
        assert len(p.posts(present)) == 1
        assert len(p.posts(absent)) == 1
    finally:
        await p.close()


@pytest.mark.asyncio
async def test_missing_guild_replay_rejects_offer_whose_price_changed(tmp_path, monkeypatch):
    p = await _pipeline(tmp_path, monkeypatch, guild_ids=(111000, 222000))
    try:
        present, absent = p.guilds
        await p.enroll(present, threshold=40)
        await p.enroll(absent, threshold=40)
        item_id = p.api.add(walmart_item(9803, "Apple iPad mini 8.3-inch", 99.00, was_price=499.00,
                                         category="Electronics/Tablets"), queries=("ipad mini",))
        await p.discover("ipad mini")
        p.bot.guilds = [present]
        await p.cycle()
        # Price returns to normal before the absent guild comes back.
        p.api.set_price(item_id, 489.00)
        await p.discover("ipad mini")
        await p.cycle()
        p.bot.guilds = [present, absent]
        await p.expire_event_leases()
        await p.cycle()
        assert p.posts(absent) == []
    finally:
        await p.close()


@pytest.mark.asyncio
async def test_failed_discord_send_retries_without_duplicates(tmp_path, monkeypatch):
    p = await _pipeline(tmp_path, monkeypatch)
    try:
        guild = p.guilds[0]
        await p.enroll(guild, threshold=40)
        p.api.add(walmart_item(9804, "Sony WH-1000XM5 Headphones", 89.00, was_price=399.99,
                               category="Electronics/Audio/Headphones"), queries=("sony headphones",))
        await p.discover("sony headphones")
        guild.channel.fail_with = RuntimeError("discord 503")
        await p.cycle()
        assert p.posts(guild) == []
        guild.channel.fail_with = None
        await p.expire_event_leases()
        await p.cycle()
        await p.cycle()
        assert len(p.posts(guild)) == 1
    finally:
        await p.close()


# ------------------------------------------------------ cross-backend rows
@pytest.mark.asyncio
async def test_full_chain_with_turso_shaped_mapping_rows(tmp_path, monkeypatch):
    """Every reachable query must tolerate dict rows (the KeyError: 0 class)."""
    p = await _pipeline(tmp_path, monkeypatch, guild_ids=(111000, 222000), mapping_rows=True)
    try:
        first, second = p.guilds
        await p.enroll(first, threshold=40, extreme=True)
        await p.enroll(second, threshold=40)
        p.api.add(walmart_item(9901, "Apple iPad Air 11-inch M2", 99.00, was_price=599.00,
                               category="Electronics/Tablets"), queries=("mapping rows",))
        p.api.add(walmart_item(9902, TAB, 1.00, msrp=799.99), queries=("mapping rows",))
        item_id = p.api.add(walmart_item(9903, "Ninja Foodi 8-in-1 Pressure Cooker", 179.00,
                                         category="Home/Kitchen Appliances"), queries=("mapping rows",))
        # A pre-#277 confirmed no-price retry row exercises the one-time legacy
        # cooldown normalizer, which used positional row access.
        from datetime import datetime, timedelta, timezone

        from sniperplug.services.walmart_confirmed_no_price_legacy_cooldown import (
            normalize_legacy_confirmed_no_price_cooldowns,
        )
        from sniperplug.services.walmart_exact_verification_queue import QUEUE_TABLE

        await p.discover("mapping rows")
        now = datetime.now(timezone.utc)
        conn = p.db.require_conn()
        await conn.execute(
            f"UPDATE {QUEUE_TABLE} SET status = 'retry', "
            "last_error = 'retry_kind=api_price_missing recovery_kind=api_price_missing', "
            "last_attempt_at = ?, last_seen_at = ?, next_attempt_at = ? WHERE item_id = ?",
            (
                (now - timedelta(hours=1)).isoformat(),
                (now - timedelta(hours=2)).isoformat(),
                (now + timedelta(hours=1)).isoformat(),
                item_id,
            ),
        )
        await conn.commit()
        # Previously raised KeyError: 0 on mapping rows and, because the schema
        # pass retries every cycle until it completes, failed every exact cycle.
        assert await normalize_legacy_confirmed_no_price_cooldowns(p.db) == 1

        await p.cycle()
        assert len(p.posts(first)) == 2  # markdown + extreme lead (opted in)
        assert len(p.posts(second)) == 1  # markdown only
    finally:
        await p.close()


# ------------------------------------------------------------- throughput
@pytest.mark.asyncio
async def test_verification_backlog_costs_one_lookup_request_per_twenty_items(tmp_path, monkeypatch):
    p = await _pipeline(tmp_path, monkeypatch)
    try:
        await p.enroll(p.guilds[0], threshold=40)
        for n in range(50):
            p.api.add(walmart_item(50000 + n, f"Widget Pro Model {n}", 50.0 + n, was_price=80.0 + n,
                                   category="Home/Widgets"), queries=("widget pro",))
        await p.discover("widget pro")  # two 25-result pages -> 50 queued items
        cycles = 0
        while cycles < 10:
            result = (await p.cycle())[0]
            if not result.claimed:
                break
            cycles += 1
        lookups = [r for r in p.api.requests if r.startswith("lookup:")]
        ids_per_call = [len(r.split(":", 1)[1].split(",")) for r in lookups]
        # 50 items verify in <= 3 worker cycles (~60s) using <= 4 lookup calls.
        # With the former 8-row claim the same backlog took 5 cycles.
        assert cycles <= 3
        assert len(lookups) <= 4 and max(ids_per_call) <= 20
        assert sum(ids_per_call) == 50
    finally:
        await p.close()
